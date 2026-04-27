from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from openpulse.conditions import evaluate_condition
from openpulse.scripts import ScriptOutputError, extract_items, extract_scalar, run_script_preview
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
        check_started = perf_counter()
        monitor = self.db.get_monitor(monitor_id)
        if monitor is None:
            raise ValueError(f"Monitor not found: {monitor_id}")

        if monitor["target"].get("sourceType") == "script":
            return await self._run_script_check(monitor, check_started)

        if self.extractor is None:
            raise ValueError("Website extractor is not configured")
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
        self._record_lifecycle(monitor_id, status, extracted.value, check_started, message)
        return log

    async def _run_script_check(self, monitor: dict[str, Any], check_started: float) -> dict[str, Any]:
        target = monitor["target"]
        selection = target.get("selection") or {}
        preview = await run_script_preview(target["script"])
        monitor_id = monitor["id"]

        if not preview.get("ok"):
            log = self.db.create_log(
                {
                    "monitorId": monitor_id,
                    "status": "error" if preview.get("error") != "script_empty_output" else "missing",
                    "previousValue": selection.get("initialValue"),
                    "currentValue": None,
                    "conditionMatched": False,
                    "message": preview.get("error", "script_failed"),
                    "details": {"execution": preview.get("execution")},
                }
            )
            self._record_lifecycle(monitor_id, log["status"], None, check_started, log["message"])
            return log

        try:
            if selection.get("mode") == "items":
                return self._run_script_items_check(monitor, preview, check_started)
            return self._run_script_scalar_check(monitor, preview, check_started)
        except ScriptOutputError as exc:
            log = self.db.create_log(
                {
                    "monitorId": monitor_id,
                    "status": "missing" if exc.reason == "script_path_missing" else "error",
                    "previousValue": selection.get("initialValue"),
                    "currentValue": None,
                    "conditionMatched": False,
                    "message": exc.reason,
                    "details": {"error": str(exc), "execution": preview.get("execution")},
                }
            )
            self._record_lifecycle(monitor_id, log["status"], None, check_started, log["message"])
            return log

    def _run_script_scalar_check(
        self,
        monitor: dict[str, Any],
        preview: dict[str, Any],
        check_started: float,
    ) -> dict[str, Any]:
        target = monitor["target"]
        selection = target.get("selection") or {}
        previous_value = selection.get("initialValue")
        current_value = extract_scalar(preview, selection)
        condition_result = evaluate_condition(
            monitor["condition"],
            previous_value=previous_value,
            current_value=current_value,
            found=True,
        )
        status = "matched" if condition_result.matched else "checked"
        log = self.db.create_log(
            {
                "monitorId": monitor["id"],
                "status": status,
                "previousValue": previous_value,
                "currentValue": current_value,
                "conditionMatched": condition_result.matched,
                "message": condition_result.reason,
                "details": {"selection": selection, "execution": preview.get("execution")},
            }
        )
        target.setdefault("selection", {})["initialValue"] = current_value
        self.db.update_monitor_target(monitor["id"], target)
        self._record_lifecycle(monitor["id"], status, current_value, check_started, log["message"])
        return log

    def _run_script_items_check(
        self,
        monitor: dict[str, Any],
        preview: dict[str, Any],
        check_started: float,
    ) -> dict[str, Any]:
        selection = monitor["target"].get("selection") or {}
        items = extract_items(preview, selection)
        seen_ids = self.db.list_script_seen_item_ids(monitor["id"])

        if not seen_ids:
            self.db.add_script_seen_items(monitor["id"], items)
            log = self.db.create_log(
                {
                    "monitorId": monitor["id"],
                    "status": "checked",
                    "previousValue": None,
                    "currentValue": str(len(items)),
                    "conditionMatched": False,
                    "message": "baseline_established",
                    "details": {"itemCount": len(items), "selection": selection},
                }
            )
            self._record_lifecycle(monitor["id"], "checked", str(len(items)), check_started, log["message"])
            return log

        new_items = [item for item in items if item["id"] not in seen_ids]
        if not new_items:
            log = self.db.create_log(
                {
                    "monitorId": monitor["id"],
                    "status": "checked",
                    "previousValue": str(len(seen_ids)),
                    "currentValue": str(len(items)),
                    "conditionMatched": False,
                    "message": "no_new_items",
                    "details": {"itemCount": len(items), "selection": selection},
                }
            )
            self._record_lifecycle(monitor["id"], "checked", str(len(items)), check_started, log["message"])
            return log

        logs = []
        for item in new_items:
            logs.append(
                self.db.create_log(
                    {
                        "monitorId": monitor["id"],
                        "status": "matched",
                        "previousValue": None,
                        "currentValue": item["id"],
                        "conditionMatched": True,
                        "message": "new_item_detected",
                        "details": {
                            "item": item["item"],
                            "selection": selection,
                            "display": _item_display(item["item"], selection),
                            "url": _item_field(item["item"], selection.get("urlField")),
                        },
                    }
                )
            )
        self.db.add_script_seen_items(monitor["id"], new_items)
        self._record_lifecycle(monitor["id"], "matched", str(len(new_items)), check_started, "new_items_detected")
        return {
            "status": "matched",
            "message": "new_items_detected",
            "conditionMatched": True,
            "currentValue": str(len(new_items)),
            "details": {"newItemCount": len(new_items), "logs": logs},
        }

    def _record_lifecycle(
        self,
        monitor_id: str,
        status: str,
        current_value: str | None,
        check_started: float,
        message: str | None,
    ) -> None:
        error = message if status in {"missing", "blocked", "error"} else None
        self.db.record_check_result(
            monitor_id,
            status=status,
            current_value=current_value,
            duration_ms=max(0, round((perf_counter() - check_started) * 1000)),
            error=error,
        )


def _item_field(item: dict[str, Any], field: str | None) -> Any:
    if not field:
        return None
    return item.get(field)


def _item_display(item: dict[str, Any], selection: dict[str, Any]) -> str:
    value = _item_field(item, selection.get("displayField"))
    if value is None:
        value = _item_field(item, selection.get("idField"))
    return str(value)
