from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openpulse.conditions import evaluate_condition
from openpulse.storage import Database


@dataclass(frozen=True)
class ExtractedValue:
    found: bool
    value: str | None
    details: dict[str, Any]


class Extractor(Protocol):
    async def extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        ...


class CheckEngine:
    def __init__(self, db: Database, extractor: Extractor):
        self.db = db
        self.extractor = extractor

    async def run_check(self, monitor_id: str) -> dict[str, Any]:
        monitor = self.db.get_monitor(monitor_id)
        if monitor is None:
            raise ValueError(f"Monitor not found: {monitor_id}")

        extracted = await self.extractor.extract(monitor["url"], monitor["target"])
        previous_value = monitor["target"].get("initialValue")
        condition_result = evaluate_condition(
            monitor["condition"],
            previous_value=previous_value,
            current_value=extracted.value,
            found=extracted.found,
        )

        if extracted.details.get("reason") == "security_verification":
            status = "blocked"
            message = "security_verification"
        elif not extracted.found:
            status = "matched" if condition_result.matched else "missing"
            message = condition_result.reason
        else:
            status = "matched" if condition_result.matched else "checked"
            message = condition_result.reason

        log = self.db.create_log(
            {
                "monitorId": monitor_id,
                "status": status,
                "previousValue": previous_value,
                "currentValue": extracted.value,
                "conditionMatched": condition_result.matched,
                "message": message,
                "details": extracted.details,
            }
        )
        self.db.mark_checked(monitor_id)
        return log
