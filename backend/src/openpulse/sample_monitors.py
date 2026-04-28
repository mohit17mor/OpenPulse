from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[2]
SAMPLE_MONITORS_DIR = PROJECT_ROOT / "sample-monitors"
SAMPLES_PATH = SAMPLE_MONITORS_DIR / "samples.json"


def list_sample_monitors() -> list[dict[str, Any]]:
    samples = json.loads(SAMPLES_PATH.read_text())
    return [_resolve_sample(sample) for sample in samples]


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
