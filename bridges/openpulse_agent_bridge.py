#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shlex
import subprocess
import sys


class BridgeHandler(BaseHTTPRequestHandler):
    agent_command: list[str] = []
    token: str | None = None
    timeout_seconds: int = 120
    prompt_mode: str = "stdin"
    stream_output: bool = True

    def do_POST(self) -> None:
        if self.token:
            expected = f"Bearer {self.token}"
            received = self.headers.get("Authorization", "")
            if received != expected:
                self.send_json(401, {"error": "unauthorized"})
                return
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode())
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_json"})
            return

        prompt = format_prompt(payload)
        event_type = payload.get("type", "-")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        monitor = data.get("monitor", {}) if isinstance(data, dict) else {}
        monitor_name = monitor.get("name", "-") if isinstance(monitor, dict) else "-"
        try:
            command = self.agent_command + [prompt] if self.prompt_mode == "arg" else self.agent_command
            display_command = self.agent_command + (
                ["<openpulse-event-prompt>"] if self.prompt_mode == "arg" else []
            )
            self.log_message("received event %s for %s", event_type, monitor_name)
            self.log_message("running agent command: %s", shlex.join(display_command))
            completed = subprocess.run(
                command,
                input=None if self.prompt_mode == "arg" else prompt,
                capture_output=not self.stream_output,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.log_message("agent command timed out after %s seconds", self.timeout_seconds)
            self.send_json(504, {"error": "command_timeout"})
            return
        if completed.returncode != 0:
            stderr = completed.stderr[-2000:] if completed.stderr else ""
            self.log_message("agent command failed with exit code %s", completed.returncode)
            self.send_json(
                502,
                {
                    "error": "command_failed",
                    "returnCode": completed.returncode,
                    "stderr": stderr,
                },
            )
            return
        stdout = completed.stdout[-2000:] if completed.stdout else ""
        self.log_message("agent command completed with exit code 0")
        self.send_json(202, {"status": "accepted", "stdout": stdout})

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"openpulse-agent-bridge: {fmt % args}\n")

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def format_prompt(payload: dict[str, object]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    monitor = data.get("monitor", {}) if isinstance(data, dict) else {}
    event = data.get("event", {}) if isinstance(data, dict) else {}
    instructions = monitor.get("agentInstructions") if isinstance(monitor, dict) else None
    instruction_lines = ["Instructions:", str(instructions), ""] if instructions else []
    return "\n".join(
        [
            "You were woken by an OpenPulse monitor event.",
            "",
            f"Monitor: {monitor.get('name', '-')}",
            f"Event: {event.get('title', payload.get('type', '-'))}",
            f"Summary: {event.get('summary', '-')}",
            f"Previous value: {event.get('previousValue', '-')}",
            f"Current value: {event.get('currentValue', '-')}",
            "",
            *instruction_lines,
            "Full event JSON:",
            json.dumps(payload, indent=2, sort_keys=True),
            "",
            "Take the requested action using your available tools.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive OpenPulse webhooks and wake a local agent command.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--prompt-mode", choices=["stdin", "arg"], default="stdin")
    parser.add_argument(
        "--capture-output",
        action="store_true",
        help="Hide agent stdout/stderr and return a small stdout tail in the webhook response.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("Provide a command after --, for example: -- codex exec")

    BridgeHandler.agent_command = command
    BridgeHandler.token = args.token
    BridgeHandler.timeout_seconds = args.timeout_seconds
    BridgeHandler.prompt_mode = args.prompt_mode
    BridgeHandler.stream_output = not args.capture_output
    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"OpenPulse agent bridge listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
