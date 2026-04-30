from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SCRIPT_TEMPLATES_PATH = SCRIPTS_DIR / "templates.json"
CUSTOM_SCRIPTS_DIR = SCRIPTS_DIR / "custom"
SCRIPT_COMMANDS = {
    ".py": "python3",
    ".sh": "bash",
    ".js": "node",
    ".mjs": "node",
}


def list_sample_monitors() -> list[dict[str, Any]]:
    samples = json.loads(SCRIPT_TEMPLATES_PATH.read_text())
    return [_resolve_sample(sample) for sample in samples]


def list_custom_scripts(
    *,
    custom_dir: Path = CUSTOM_SCRIPTS_DIR,
    project_root: Path = PROJECT_ROOT,
) -> list[dict[str, Any]]:
    if not custom_dir.exists():
        custom_dir.mkdir(parents=True, exist_ok=True)
        return []

    scripts: list[dict[str, Any]] = []
    for path in sorted(custom_dir.rglob("*")):
        if not _is_custom_script_file(path):
            continue
        relative_path = path.relative_to(project_root)
        scripts.append(
            {
                "id": f"custom:{path.name}",
                "name": _script_name(path),
                "description": "Custom script in scripts/custom.",
                "category": "Custom",
                "path": str(path),
                "relativePath": relative_path.as_posix(),
                "script": {
                    "command": SCRIPT_COMMANDS.get(path.suffix.lower(), "python3"),
                    "args": [relative_path.as_posix()],
                    "cwd": str(project_root),
                    "timeoutSeconds": 10,
                },
                "condition": {"type": "changed"},
                "intervalSeconds": 300,
            }
        )
    return scripts


def _resolve_sample(sample: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(sample)
    script = dict(resolved["script"])
    cwd = script.get("cwd")
    if cwd in (None, "", "."):
        script["cwd"] = str(PROJECT_ROOT)
    script["args"] = [_replace_tokens(arg) for arg in script.get("args") or []]
    resolved["script"] = script
    return resolved


def _replace_tokens(value: str) -> str:
    return value.replace("{projectRoot}", str(PROJECT_ROOT))


def _is_custom_script_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(".") or "__pycache__" in path.parts:
        return False
    if path.suffix.lower() in {".pyc", ".pyo", ".log", ".json", ".txt", ".md"}:
        return False
    return path.suffix.lower() in SCRIPT_COMMANDS


def _script_name(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name
