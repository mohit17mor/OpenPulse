from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ConditionResult:
    matched: bool
    reason: str


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def evaluate_condition(
    condition: dict[str, Any],
    *,
    previous_value: str | None,
    current_value: str | None,
    found: bool,
) -> ConditionResult:
    condition_type = condition.get("type")
    previous = normalize_text(previous_value)
    current = normalize_text(current_value)

    if condition_type == "appears":
        return ConditionResult(found and bool(current), "target_appeared" if found else "target_missing")

    if condition_type == "disappears":
        return ConditionResult(not found, "target_disappeared" if not found else "target_still_present")

    if not found:
        return ConditionResult(False, "target_missing")

    if condition_type == "changed":
        return ConditionResult(previous != current, "value_changed" if previous != current else "value_unchanged")

    if condition_type == "equals":
        expected = normalize_text(str(condition.get("value", "")))
        return ConditionResult(current == expected, "text_equals" if current == expected else "text_not_equal")

    if condition_type == "contains":
        expected = normalize_text(str(condition.get("value", ""))).lower()
        matched = expected in current.lower()
        return ConditionResult(matched, "text_contains" if matched else "text_missing")

    if condition_type in {"greater_than", "less_than"}:
        current_number = parse_number(current)
        threshold = condition.get("value")
        if current_number is None or not isinstance(threshold, int | float):
            return ConditionResult(False, "number_unavailable")
        if condition_type == "greater_than":
            matched = current_number > threshold
            return ConditionResult(matched, "number_greater_than" if matched else "number_not_greater_than")
        matched = current_number < threshold
        return ConditionResult(matched, "number_less_than" if matched else "number_not_less_than")

    return ConditionResult(False, "unknown_condition")

