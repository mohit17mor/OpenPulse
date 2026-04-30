from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from openpulse.migrations import run_migrations


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            run_migrations(conn)

    def create_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = payload.get("createdAt") or utc_now()
        destination_ids = list(dict.fromkeys(payload.get("destinationIds") or []))
        monitor = {
            "id": payload.get("id") or str(uuid4()),
            "name": payload["name"],
            "url": payload["url"],
            "target": payload["target"],
            "condition": payload["condition"],
            "intervalSeconds": max(5, int(payload.get("intervalSeconds") or 300)),
            "enabled": bool(payload.get("enabled", True)),
            "createdAt": created_at,
            "lastCheckedAt": payload.get("lastCheckedAt"),
            "nextCheckAt": payload.get("nextCheckAt") or created_at,
            "lastStatus": payload.get("lastStatus") or "pending",
            "lastError": payload.get("lastError"),
            "lastDurationMs": payload.get("lastDurationMs"),
            "lastValue": payload.get("lastValue"),
            "consecutiveFailures": int(payload.get("consecutiveFailures") or 0),
            "checkStartedAt": payload.get("checkStartedAt"),
            "destinationIds": destination_ids,
            "agentInstructions": payload.get("agentInstructions") or "",
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into monitors (
                    id, name, url, target_json, condition_json,
                    interval_seconds, enabled, created_at, last_checked_at,
                    next_check_at, last_status, last_error, last_duration_ms,
                    last_value, consecutive_failures, check_started_at,
                    agent_instructions
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    monitor["id"],
                    monitor["name"],
                    monitor["url"],
                    json.dumps(monitor["target"]),
                    json.dumps(monitor["condition"]),
                    monitor["intervalSeconds"],
                    1 if monitor["enabled"] else 0,
                    monitor["createdAt"],
                    monitor["lastCheckedAt"],
                    monitor["nextCheckAt"],
                    monitor["lastStatus"],
                    monitor["lastError"],
                    monitor["lastDurationMs"],
                    monitor["lastValue"],
                    monitor["consecutiveFailures"],
                    monitor["checkStartedAt"],
                    monitor["agentInstructions"],
                ),
            )
            self._set_monitor_destinations(conn, monitor["id"], destination_ids)
        return monitor

    def list_monitors(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from monitors order by created_at desc").fetchall()
            monitors = [self._monitor_from_row(row) for row in rows]
            for monitor in monitors:
                monitor["destinationIds"] = self._list_monitor_destination_ids(conn, monitor["id"])
        return monitors

    def list_due_monitors(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current_time = now or datetime.now(UTC)
        due_monitors = []
        for monitor in self.list_monitors():
            if not monitor["enabled"]:
                continue
            if monitor.get("checkStartedAt") is not None:
                continue
            next_check_at = monitor.get("nextCheckAt")
            if next_check_at is None:
                last_checked_at = monitor["lastCheckedAt"]
                if last_checked_at is None:
                    due_monitors.append(monitor)
                    continue
                last_checked = _parse_iso(last_checked_at)
                if current_time - last_checked >= timedelta(seconds=monitor["intervalSeconds"]):
                    due_monitors.append(monitor)
                continue
            next_check = _parse_iso(next_check_at)
            if next_check <= current_time:
                due_monitors.append(monitor)
        due_monitors.sort(key=lambda item: item["createdAt"])
        return due_monitors

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from monitors where id = ?", (monitor_id,)).fetchone()
            if row is None:
                return None
            monitor = self._monitor_from_row(row)
            monitor["destinationIds"] = self._list_monitor_destination_ids(conn, monitor["id"])
        return monitor

    def mark_checked(self, monitor_id: str) -> None:
        self.record_check_result(
            monitor_id,
            status="checked",
            current_value=None,
            duration_ms=None,
            error=None,
        )

    def mark_check_started(self, monitor_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update monitors
                set last_status = 'checking',
                    last_error = null,
                    check_started_at = ?
                where id = ?
                """,
                (utc_now(), monitor_id),
            )

    def record_check_result(
        self,
        monitor_id: str,
        *,
        status: str,
        current_value: str | None,
        duration_ms: int | None,
        error: str | None,
    ) -> None:
        checked_at = utc_now()
        failure_statuses = {"missing", "blocked", "error"}
        with self.connect() as conn:
            row = conn.execute(
                "select interval_seconds, consecutive_failures from monitors where id = ?",
                (monitor_id,),
            ).fetchone()
            if row is None:
                return
            next_check_at = (_parse_iso(checked_at) + timedelta(seconds=row["interval_seconds"])).isoformat()
            consecutive_failures = (
                int(row["consecutive_failures"] or 0) + 1 if status in failure_statuses else 0
            )
            conn.execute(
                """
                update monitors
                set last_checked_at = ?,
                    next_check_at = ?,
                    last_status = ?,
                    last_error = ?,
                    last_duration_ms = ?,
                    last_value = ?,
                    consecutive_failures = ?,
                    check_started_at = null
                where id = ?
                """,
                (
                    checked_at,
                    next_check_at,
                    status,
                    error,
                    duration_ms,
                    current_value,
                    consecutive_failures,
                    monitor_id,
                ),
            )

    def update_monitor_target(self, monitor_id: str, target: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "update monitors set target_json = ? where id = ?",
                (json.dumps(target), monitor_id),
            )

    def set_monitor_enabled(self, monitor_id: str, enabled: bool) -> dict[str, Any] | None:
        status = "pending" if enabled else "paused"
        error = None if enabled else "paused_by_user"
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update monitors
                set enabled = ?,
                    last_status = ?,
                    last_error = ?,
                    check_started_at = null
                where id = ?
                """,
                (1 if enabled else 0, status, error, monitor_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_monitor(monitor_id)

    def create_destination(self, payload: dict[str, Any]) -> dict[str, Any]:
        destination = {
            "id": payload.get("id") or str(uuid4()),
            "name": payload["name"],
            "type": payload["type"],
            "config": payload.get("config") or {},
            "enabled": bool(payload.get("enabled", True)),
            "createdAt": payload.get("createdAt") or utc_now(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into destinations (id, name, type, config_json, enabled, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    destination["id"],
                    destination["name"],
                    destination["type"],
                    json.dumps(destination["config"]),
                    1 if destination["enabled"] else 0,
                    destination["createdAt"],
                ),
            )
        return destination

    def list_destinations(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from destinations order by created_at desc").fetchall()
        return [self._destination_from_row(row) for row in rows]

    def get_destination(self, destination_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from destinations where id = ?", (destination_id,)).fetchone()
        return self._destination_from_row(row) if row else None

    def delete_destination(self, destination_id: str) -> bool:
        with self.connect() as conn:
            conn.execute("delete from monitor_destinations where destination_id = ?", (destination_id,))
            cursor = conn.execute("delete from destinations where id = ?", (destination_id,))
            return cursor.rowcount > 0

    def set_monitor_destinations(self, monitor_id: str, destination_ids: list[str]) -> None:
        with self.connect() as conn:
            self._set_monitor_destinations(conn, monitor_id, list(dict.fromkeys(destination_ids)))

    def list_monitor_destination_ids(self, monitor_id: str) -> list[str]:
        with self.connect() as conn:
            return self._list_monitor_destination_ids(conn, monitor_id)

    def delete_monitor(self, monitor_id: str) -> bool:
        with self.connect() as conn:
            log_ids = [
                row["id"]
                for row in conn.execute("select id from logs where monitor_id = ?", (monitor_id,)).fetchall()
            ]
            if log_ids:
                conn.executemany("delete from event_deliveries where log_id = ?", [(log_id,) for log_id in log_ids])
            conn.execute("delete from monitor_destinations where monitor_id = ?", (monitor_id,))
            conn.execute("delete from logs where monitor_id = ?", (monitor_id,))
            conn.execute("delete from script_seen_items where monitor_id = ?", (monitor_id,))
            cursor = conn.execute("delete from monitors where id = ?", (monitor_id,))
            return cursor.rowcount > 0

    def add_script_seen_items(self, monitor_id: str, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert or ignore into script_seen_items (monitor_id, item_id, item_json, first_seen_at)
                values (?, ?, ?, ?)
                """,
                [
                    (
                        monitor_id,
                        str(item["id"]),
                        json.dumps(item.get("item", item), sort_keys=True),
                        now,
                    )
                    for item in items
                ],
            )

    def list_script_seen_item_ids(self, monitor_id: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "select item_id from script_seen_items where monitor_id = ? order by first_seen_at",
                (monitor_id,),
            ).fetchall()
        return {row["item_id"] for row in rows}

    def enqueue_deliveries_for_log(self, log: dict[str, Any], monitor: dict[str, Any]) -> list[dict[str, Any]]:
        destination_ids = monitor.get("destinationIds") or self.list_monitor_destination_ids(monitor["id"])
        if not destination_ids:
            return []
        now = utc_now()
        deliveries = []
        with self.connect() as conn:
            rows = conn.execute(
                f"select * from destinations where enabled = 1 and id in ({','.join('?' for _ in destination_ids)})",
                destination_ids,
            ).fetchall()
            destinations = [self._destination_from_row(row) for row in rows]
            for destination in destinations:
                delivery = {
                    "id": str(uuid4()),
                    "logId": log["id"],
                    "monitorId": monitor["id"],
                    "destinationId": destination["id"],
                    "status": "pending",
                    "attempts": 0,
                    "nextAttemptAt": now,
                    "lastError": None,
                    "responseStatus": None,
                    "payload": build_event_payload(log, monitor),
                    "createdAt": now,
                    "deliveredAt": None,
                }
                deliveries.append(delivery)
            conn.executemany(
                """
                insert into event_deliveries (
                    id, log_id, monitor_id, destination_id, status, attempts,
                    next_attempt_at, last_error, response_status, payload_json,
                    created_at, delivered_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        delivery["id"],
                        delivery["logId"],
                        delivery["monitorId"],
                        delivery["destinationId"],
                        delivery["status"],
                        delivery["attempts"],
                        delivery["nextAttemptAt"],
                        delivery["lastError"],
                        delivery["responseStatus"],
                        json.dumps(delivery["payload"]),
                        delivery["createdAt"],
                        delivery["deliveredAt"],
                    )
                    for delivery in deliveries
                ],
            )
        return deliveries

    def list_pending_deliveries(self, *, limit: int = 25, now: datetime | None = None) -> list[dict[str, Any]]:
        current_time = (now or datetime.now(UTC)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from event_deliveries
                where status = 'pending' and next_attempt_at <= ?
                order by created_at
                limit ?
                """,
                (current_time, limit),
            ).fetchall()
        return [self._delivery_from_row(row) for row in rows]

    def list_deliveries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from event_deliveries order by created_at desc limit ?", (limit,)).fetchall()
        return [self._delivery_from_row(row) for row in rows]

    def mark_delivery_sending(self, delivery_id: str) -> None:
        with self.connect() as conn:
            conn.execute("update event_deliveries set status = 'sending' where id = ?", (delivery_id,))

    def mark_delivery_delivered(self, delivery_id: str, *, response_status: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update event_deliveries
                set status = 'delivered',
                    delivered_at = ?,
                    response_status = ?,
                    last_error = null
                where id = ?
                """,
                (utc_now(), response_status, delivery_id),
            )

    def mark_delivery_failed(self, delivery_id: str, *, error: str, response_status: int | None = None) -> None:
        with self.connect() as conn:
            row = conn.execute("select attempts from event_deliveries where id = ?", (delivery_id,)).fetchone()
            if row is None:
                return
            attempts = int(row["attempts"] or 0) + 1
            retry_delay_seconds = min(300, 2**attempts)
            next_attempt_at = (datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)).isoformat()
            status = "failed" if attempts >= 3 else "pending"
            conn.execute(
                """
                update event_deliveries
                set status = ?,
                    attempts = ?,
                    next_attempt_at = ?,
                    last_error = ?,
                    response_status = ?
                where id = ?
                """,
                (status, attempts, next_attempt_at, error, response_status, delivery_id),
            )

    def create_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = payload["status"]
        message = payload["message"]
        previous_value = payload.get("previousValue")
        current_value = payload.get("currentValue")
        condition_matched = bool(payload.get("conditionMatched", False))
        event_type = payload.get("eventType") or _default_event_type(status, message, condition_matched)
        severity = payload.get("severity") or _default_severity(status, condition_matched)
        title = payload.get("title") or _default_title(event_type, status)
        summary = payload.get("summary") or _default_summary(
            event_type,
            previous_value=previous_value,
            current_value=current_value,
            message=message,
        )
        log = {
            "id": payload.get("id") or str(uuid4()),
            "monitorId": payload.get("monitorId"),
            "status": status,
            "eventType": event_type,
            "severity": severity,
            "sourceType": payload.get("sourceType") or "unknown",
            "title": title,
            "summary": summary,
            "previousValue": previous_value,
            "currentValue": current_value,
            "conditionMatched": condition_matched,
            "message": message,
            "reasonCode": payload.get("reasonCode") or message,
            "evidence": payload.get("evidence") or {},
            "actionHint": payload.get("actionHint"),
            "details": payload.get("details") or {},
            "createdAt": payload.get("createdAt") or utc_now(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into logs (
                    id, monitor_id, status, event_type, severity, source_type,
                    title, summary, previous_value, current_value,
                    condition_matched, message, reason_code, evidence_json,
                    action_hint, details_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log["id"],
                    log["monitorId"],
                    log["status"],
                    log["eventType"],
                    log["severity"],
                    log["sourceType"],
                    log["title"],
                    log["summary"],
                    log["previousValue"],
                    log["currentValue"],
                    1 if log["conditionMatched"] else 0,
                    log["message"],
                    log["reasonCode"],
                    json.dumps(log["evidence"]),
                    log["actionHint"],
                    json.dumps(log["details"]),
                    log["createdAt"],
                ),
            )
        return log

    def list_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select * from logs order by created_at desc limit ?",
                (limit,),
            ).fetchall()
        return [self._log_from_row(row) for row in rows]

    @staticmethod
    def _monitor_from_row(row: sqlite3.Row) -> dict[str, Any]:
        monitor = {
            "id": row["id"],
            "name": row["name"],
            "url": row["url"],
            "target": json.loads(row["target_json"]),
            "condition": json.loads(row["condition_json"]),
            "intervalSeconds": row["interval_seconds"],
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
            "lastCheckedAt": row["last_checked_at"],
            "nextCheckAt": row["next_check_at"],
            "lastStatus": row["last_status"],
            "lastError": row["last_error"],
            "lastDurationMs": row["last_duration_ms"],
            "lastValue": row["last_value"],
            "consecutiveFailures": row["consecutive_failures"],
            "checkStartedAt": row["check_started_at"],
            "destinationIds": [],
            "agentInstructions": row["agent_instructions"],
        }
        return monitor

    @staticmethod
    def _log_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "monitorId": row["monitor_id"],
            "status": row["status"],
            "eventType": row["event_type"],
            "severity": row["severity"],
            "sourceType": row["source_type"],
            "title": row["title"],
            "summary": row["summary"],
            "previousValue": row["previous_value"],
            "currentValue": row["current_value"],
            "conditionMatched": bool(row["condition_matched"]),
            "message": row["message"],
            "reasonCode": row["reason_code"],
            "evidence": json.loads(row["evidence_json"]),
            "actionHint": row["action_hint"],
            "details": json.loads(row["details_json"]),
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _destination_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "config": json.loads(row["config_json"]),
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _delivery_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "logId": row["log_id"],
            "monitorId": row["monitor_id"],
            "destinationId": row["destination_id"],
            "status": row["status"],
            "attempts": row["attempts"],
            "nextAttemptAt": row["next_attempt_at"],
            "lastError": row["last_error"],
            "responseStatus": row["response_status"],
            "payload": json.loads(row["payload_json"]),
            "createdAt": row["created_at"],
            "deliveredAt": row["delivered_at"],
        }

    def _set_monitor_destinations(
        self,
        conn: sqlite3.Connection,
        monitor_id: str,
        destination_ids: list[str],
    ) -> None:
        conn.execute("delete from monitor_destinations where monitor_id = ?", (monitor_id,))
        conn.executemany(
            "insert or ignore into monitor_destinations (monitor_id, destination_id) values (?, ?)",
            [(monitor_id, destination_id) for destination_id in destination_ids],
        )

    def _list_monitor_destination_ids(self, conn: sqlite3.Connection, monitor_id: str) -> list[str]:
        rows = conn.execute(
            "select destination_id from monitor_destinations where monitor_id = ? order by destination_id",
            (monitor_id,),
        ).fetchall()
        return [row["destination_id"] for row in rows]


def _default_event_type(status: str, message: str, condition_matched: bool) -> str:
    if condition_matched or status == "matched":
        return "condition_matched"
    if status == "missing":
        return "target_missing"
    if status == "blocked":
        return "page_blocked"
    if status == "error":
        return "check_failed"
    return "check_completed"


def _default_severity(status: str, condition_matched: bool) -> str:
    if condition_matched or status == "matched":
        return "success"
    if status in {"missing", "blocked"}:
        return "warning"
    if status == "error":
        return "error"
    return "info"


def _default_title(event_type: str, status: str) -> str:
    titles = {
        "condition_matched": "Condition matched",
        "target_missing": "Target missing",
        "page_blocked": "Page blocked",
        "script_failed": "Script check failed",
        "script_timeout": "Script timed out",
        "new_item_detected": "New item detected",
        "scheduler_error": "Scheduled check failed",
        "check_failed": "Check failed",
        "check_completed": "Check completed",
    }
    return titles.get(event_type, status.replace("_", " ").title())


def _default_summary(
    event_type: str,
    *,
    previous_value: str | None,
    current_value: str | None,
    message: str,
) -> str:
    if event_type == "condition_matched":
        return f"Condition matched. Previous: {previous_value or '-'}, current: {current_value or '-'}."
    if event_type == "target_missing":
        return "OpenPulse loaded the source but could not find the selected target."
    if event_type == "page_blocked":
        return "The website showed a security or verification page."
    if event_type == "new_item_detected":
        return f"New item detected: {current_value or '-'}."
    if event_type in {"script_failed", "script_timeout", "scheduler_error", "check_failed"}:
        return f"Check failed with reason: {message}."
    return f"Check completed. Current value: {current_value or '-'}."


def build_event_payload(log: dict[str, Any], monitor: dict[str, Any]) -> dict[str, Any]:
    event_type = log.get("eventType") or _default_event_type(
        log["status"],
        log["message"],
        bool(log.get("conditionMatched")),
    )
    return {
        "specversion": "1.0",
        "id": log["id"],
        "source": f"openpulse://monitor/{monitor['id']}",
        "type": f"openpulse.monitor.{event_type}",
        "time": log["createdAt"],
        "subject": monitor["name"],
        "datacontenttype": "application/json",
        "data": {
            "monitor": {
                "id": monitor["id"],
                "name": monitor["name"],
                "url": monitor["url"],
                "sourceType": (monitor.get("target") or {}).get("sourceType") or "website",
                "condition": monitor["condition"],
                "agentInstructions": monitor.get("agentInstructions") or "",
            },
            "event": {
                "id": log["id"],
                "status": log["status"],
                "eventType": event_type,
                "severity": log.get("severity"),
                "title": log.get("title"),
                "summary": log.get("summary"),
                "previousValue": log.get("previousValue"),
                "currentValue": log.get("currentValue"),
                "conditionMatched": bool(log.get("conditionMatched")),
                "reasonCode": log.get("reasonCode") or log.get("message"),
                "createdAt": log["createdAt"],
            },
            "evidence": log.get("evidence") or log.get("details") or {},
        },
    }
