#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys


class BridgeHandler(BaseHTTPRequestHandler):
    agent_command: list[str] = []
    token: str | None = None
    timeout_seconds: int = 120
    prompt_mode: str = "stdin"

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
        try:
            command = self.agent_command + [prompt] if self.prompt_mode == "arg" else self.agent_command
            completed = subprocess.run(
                command,
                input=None if self.prompt_mode == "arg" else prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.send_json(504, {"error": "command_timeout"})
            return
        if completed.returncode != 0:
            self.send_json(
                502,
                {
                    "error": "command_failed",
                    "returnCode": completed.returncode,
                    "stderr": completed.stderr[-2000:],
                },
            )
            return
        self.send_json(202, {"status": "accepted", "stdout": completed.stdout[-2000:]})

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
            "Full event JSON:",
            json.dumps(payload, indent=2, sort_keys=True),
            "",
            "Take the appropriate action using your available tools.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive OpenPulse webhooks and wake a local agent command.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--prompt-mode", choices=["stdin", "arg"], default="stdin")
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
    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"OpenPulse agent bridge listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
