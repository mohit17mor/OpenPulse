from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Any


class ScriptOutputError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ScriptRunResult:
    command: str
    args: list[str]
    cwd: str | None
    timeout_seconds: int
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


async def run_script(
    command: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout_seconds: int = 10,
) -> ScriptRunResult:
    start = time.monotonic()
    argv = [command, *(args or [])]
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd or None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        timed_out = False
    except TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        timed_out = True
    return ScriptRunResult(
        command=command,
        args=args or [],
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        exit_code=process.returncode if not timed_out else None,
        stdout=stdout_bytes.decode(errors="replace"),
        stderr=stderr_bytes.decode(errors="replace"),
        duration_ms=round((time.monotonic() - start) * 1000),
        timed_out=timed_out,
    )


def parse_script_output(stdout: str) -> dict[str, Any]:
    raw = stdout.strip()
    if not raw:
        raise ScriptOutputError("script_empty_output", "Script stdout was empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return parse_text_output(raw)
    if not isinstance(parsed, dict | list):
        return parse_text_output(raw)

    nodes: list[dict[str, Any]] = []
    discover_nodes(parsed, "", nodes)
    return {
        "outputType": "json",
        "stdout": raw,
        "parsed": parsed,
        "nodes": nodes,
    }


def parse_text_output(raw: str) -> dict[str, Any]:
    value = raw
    return {
        "outputType": "text",
        "stdout": raw,
        "parsed": None,
        "nodes": [{"kind": "scalar", "path": "$stdout", "value": value, "valueType": value_type(value)}],
    }


def discover_nodes(value: Any, path: str, nodes: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            discover_nodes(child, child_path, nodes)
        return
    if isinstance(value, list):
        if _is_item_array(value):
            nodes.append(
                {
                    "kind": "array",
                    "path": path or "$",
                    "length": len(value),
                    "idFieldOptions": sorted({key for item in value if isinstance(item, dict) for key in item.keys()}),
                    "sample": value[0] if value else None,
                }
            )
        return
    nodes.append({"kind": "scalar", "path": path or "$", "value": value, "valueType": value_type(value)})


def _is_item_array(value: list[Any]) -> bool:
    return len(value) > 0 and all(isinstance(item, dict) for item in value)


def value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        try:
            float(value.replace(",", ""))
            return "number"
        except ValueError:
            return "string"
    if value is None:
        return "null"
    return "string"


def extract_scalar(preview: dict[str, Any], selection: dict[str, Any]) -> str:
    output_type = selection.get("outputType")
    path = selection.get("path")
    if output_type == "text" or path == "$stdout":
        value = preview.get("stdout", "")
    else:
        if preview.get("outputType") != "json":
            raise ScriptOutputError("script_invalid_json", "Selected JSON path requires JSON output")
        value = get_path(preview.get("parsed"), path)
    if value is _MISSING:
        raise ScriptOutputError("script_path_missing", f"Selected path missing: {path}")
    return _stringify_value(value)


def extract_items(preview: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    if selection.get("outputType", preview.get("outputType")) != "json":
        raise ScriptOutputError("script_invalid_json", "Item-list mode requires JSON output")
    array_path = selection.get("arrayPath")
    id_field = selection.get("idField")
    raw_items = get_path(preview.get("parsed"), array_path)
    if raw_items is _MISSING or not isinstance(raw_items, list):
        raise ScriptOutputError("script_path_missing", f"Selected array path missing: {array_path}")
    items = []
    for item in raw_items:
        if not isinstance(item, dict) or id_field not in item or item[id_field] in (None, ""):
            raise ScriptOutputError("script_item_id_missing", f"Item missing id field: {id_field}")
        items.append({"id": str(item[id_field]), "item": item})
    return items


_MISSING = object()


def get_path(value: Any, path: str | None) -> Any:
    if path in (None, "", "$"):
        return value
    current = value
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _stringify_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool) or value is None:
        return str(value)
    return json.dumps(value, sort_keys=True)


async def run_script_preview(script: dict[str, Any]) -> dict[str, Any]:
    command = script["command"]
    args = list(script.get("args") or [])
    cwd = script.get("cwd") or None
    timeout_seconds = int(script.get("timeoutSeconds") or 10)
    try:
        result = await run_script(command, args, cwd, timeout_seconds)
    except OSError as exc:
        return {
            "ok": False,
            "error": "script_failed",
            "execution": {
                "command": command,
                "args": args,
                "cwd": cwd,
                "timeoutSeconds": timeout_seconds,
                "exitCode": None,
                "stdout": "",
                "stderr": str(exc),
                "durationMs": 0,
                "timedOut": False,
            },
        }
    execution = {
        "command": result.command,
        "args": result.args,
        "cwd": result.cwd,
        "timeoutSeconds": result.timeout_seconds,
        "exitCode": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "durationMs": result.duration_ms,
        "timedOut": result.timed_out,
    }
    if result.timed_out:
        return {"ok": False, "error": "script_timeout", "execution": execution}
    if result.exit_code != 0:
        return {"ok": False, "error": "script_failed", "execution": execution}
    try:
        preview = parse_script_output(result.stdout)
    except ScriptOutputError as exc:
        return {"ok": False, "error": exc.reason, "execution": execution}
    return {"ok": True, "execution": execution, **preview}
