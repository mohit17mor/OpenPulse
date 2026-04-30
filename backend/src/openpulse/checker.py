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
        elif extracted.details.get("reason") == "navigation_failed":
            status = "error"
            message = "navigation_failed"
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
                **_event_fields(
                    source_type="website",
                    status=status,
                    message=message,
                    previous_value=previous_value,
                    current_value=extracted.value,
                    condition_matched=condition_result.matched,
                    evidence=extracted.details,
                ),
                "previousValue": previous_value,
                "currentValue": extracted.value,
                "conditionMatched": condition_result.matched,
                "message": message,
                "details": extracted.details,
            }
        )
        self._enqueue_delivery_if_needed(log, monitor)
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
                    **_event_fields(
                        source_type="script",
                        status="error" if preview.get("error") != "script_empty_output" else "missing",
                        message=preview.get("error", "script_failed"),
                        previous_value=selection.get("initialValue"),
                        current_value=None,
                        condition_matched=False,
                        evidence={"execution": preview.get("execution")},
                    ),
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
                    **_event_fields(
                        source_type="script",
                        status="missing" if exc.reason == "script_path_missing" else "error",
                        message=exc.reason,
                        previous_value=selection.get("initialValue"),
                        current_value=None,
                        condition_matched=False,
                        evidence={"error": str(exc), "execution": preview.get("execution")},
                    ),
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
                **_event_fields(
                    source_type="script",
                    status=status,
                    message=condition_result.reason,
                    previous_value=previous_value,
                    current_value=current_value,
                    condition_matched=condition_result.matched,
                    evidence={"selection": selection, "execution": preview.get("execution")},
                ),
                "previousValue": previous_value,
                "currentValue": current_value,
                "conditionMatched": condition_result.matched,
                "message": condition_result.reason,
                "details": {"selection": selection, "execution": preview.get("execution")},
            }
        )
        self._enqueue_delivery_if_needed(log, monitor)
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
                    **_event_fields(
                        source_type="script",
                        status="checked",
                        message="baseline_established",
                        previous_value=None,
                        current_value=str(len(items)),
                        condition_matched=False,
                        evidence={"itemCount": len(items), "selection": selection},
                    ),
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
                    **_event_fields(
                        source_type="script",
                        status="checked",
                        message="no_new_items",
                        previous_value=str(len(seen_ids)),
                        current_value=str(len(items)),
                        condition_matched=False,
                        evidence={"itemCount": len(items), "selection": selection},
                    ),
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
            log = self.db.create_log(
                {
                    "monitorId": monitor["id"],
                    "status": "matched",
                    **_event_fields(
                        source_type="script",
                        status="matched",
                        message="new_item_detected",
                        previous_value=None,
                        current_value=item["id"],
                        condition_matched=True,
                        evidence={
                            "item": item["item"],
                            "selection": selection,
                            "display": _item_display(item["item"], selection),
                            "url": _item_field(item["item"], selection.get("urlField")),
                        },
                    ),
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
            self._enqueue_delivery_if_needed(log, monitor)
            logs.append(log)
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

    def _enqueue_delivery_if_needed(self, log: dict[str, Any], monitor: dict[str, Any]) -> None:
        if log.get("status") != "matched" and not log.get("conditionMatched"):
            return
        self.db.enqueue_deliveries_for_log(log, monitor)


def _item_field(item: dict[str, Any], field: str | None) -> Any:
    if not field:
        return None
    return item.get(field)


def _item_display(item: dict[str, Any], selection: dict[str, Any]) -> str:
    value = _item_field(item, selection.get("displayField"))
    if value is None:
        value = _item_field(item, selection.get("idField"))
    return str(value)


def _event_fields(
    *,
    source_type: str,
    status: str,
    message: str,
    previous_value: str | None,
    current_value: str | None,
    condition_matched: bool,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    event_type = _event_type(source_type, status, message, condition_matched)
    display = evidence.get("display")
    summary = _event_summary(
        event_type,
        message=message,
        previous_value=previous_value,
        current_value=current_value,
        display=display,
    )
    return {
        "eventType": event_type,
        "severity": _severity(status, condition_matched),
        "sourceType": source_type,
        "title": _title(event_type),
        "summary": summary,
        "reasonCode": message,
        "evidence": evidence,
        "actionHint": _action_hint(event_type),
    }


def _event_type(source_type: str, status: str, message: str, condition_matched: bool) -> str:
    if message == "new_item_detected":
        return "new_item_detected"
    if source_type == "script" and status == "error":
        return "script_timeout" if message == "script_timeout" else "script_failed"
    if source_type == "script" and status == "missing":
        return "script_output_missing"
    if source_type == "website" and message == "navigation_failed":
        return "website_navigation_failed"
    if status == "blocked":
        return "page_blocked"
    if status == "missing":
        return "target_missing"
    if condition_matched or status == "matched":
        return "condition_matched"
    return "check_completed"


def _severity(status: str, condition_matched: bool) -> str:
    if condition_matched or status == "matched":
        return "success"
    if status in {"missing", "blocked"}:
        return "warning"
    if status == "error":
        return "error"
    return "info"


def _title(event_type: str) -> str:
    titles = {
        "condition_matched": "Condition matched",
        "target_missing": "Target missing",
        "page_blocked": "Page blocked",
        "script_failed": "Script check failed",
        "script_timeout": "Script timed out",
        "script_output_missing": "Script output missing",
        "website_navigation_failed": "Website navigation failed",
        "new_item_detected": "New item detected",
        "check_completed": "Check completed",
    }
    return titles.get(event_type, "Check event")


def _event_summary(
    event_type: str,
    *,
    message: str,
    previous_value: str | None,
    current_value: str | None,
    display: Any,
) -> str:
    if event_type == "condition_matched":
        return f"Condition matched. Previous: {previous_value or '-'}, current: {current_value or '-'}."
    if event_type == "target_missing":
        return "OpenPulse loaded the page but could not find the selected target."
    if event_type == "page_blocked":
        return "The website showed a security or verification page."
    if event_type == "website_navigation_failed":
        return "OpenPulse could not load the website for this check."
    if event_type == "new_item_detected":
        return f"New item detected: {display or current_value or '-'}."
    if event_type == "script_output_missing":
        return "The script ran, but the selected output was not found."
    if event_type in {"script_failed", "script_timeout"}:
        return f"Script check failed with reason: {message}."
    return f"Check completed. Current value: {current_value or '-'}."


def _action_hint(event_type: str) -> str | None:
    hints = {
        "target_missing": "Open the page and repair the monitor target.",
        "page_blocked": "Open the browser session and check whether the site is asking for verification.",
        "website_navigation_failed": "Open the browser session and rerun the check from an interactive session.",
        "script_failed": "Run the script preview and inspect stderr/output.",
        "script_timeout": "Increase the timeout or make the script finish faster.",
        "script_output_missing": "Run preview again and choose an output path that exists.",
    }
    return hints.get(event_type)
