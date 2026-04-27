from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


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
            conn.executescript(
                """
                create table if not exists monitors (
                    id text primary key,
                    name text not null,
                    url text not null,
                    target_json text not null,
                    condition_json text not null,
                    interval_seconds integer not null default 300,
                    enabled integer not null default 1,
                    created_at text not null,
                    last_checked_at text,
                    next_check_at text,
                    last_status text not null default 'pending',
                    last_error text,
                    last_duration_ms integer,
                    last_value text,
                    consecutive_failures integer not null default 0,
                    check_started_at text
                );

                create table if not exists logs (
                    id text primary key,
                    monitor_id text,
                    status text not null,
                    previous_value text,
                    current_value text,
                    condition_matched integer not null default 0,
                    message text not null,
                    details_json text not null,
                    created_at text not null,
                    foreign key(monitor_id) references monitors(id)
                );

                create table if not exists script_seen_items (
                    monitor_id text not null,
                    item_id text not null,
                    item_json text not null,
                    first_seen_at text not null,
                    primary key(monitor_id, item_id),
                    foreign key(monitor_id) references monitors(id)
                );
                """
            )
            self._ensure_monitor_lifecycle_columns(conn)

    @staticmethod
    def _ensure_monitor_lifecycle_columns(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("pragma table_info(monitors)").fetchall()}
        migrations = {
            "next_check_at": "alter table monitors add column next_check_at text",
            "last_status": "alter table monitors add column last_status text not null default 'pending'",
            "last_error": "alter table monitors add column last_error text",
            "last_duration_ms": "alter table monitors add column last_duration_ms integer",
            "last_value": "alter table monitors add column last_value text",
            "consecutive_failures": "alter table monitors add column consecutive_failures integer not null default 0",
            "check_started_at": "alter table monitors add column check_started_at text",
        }
        for column, sql in migrations.items():
            if column not in columns:
                conn.execute(sql)
        conn.execute(
            """
            update monitors
            set check_started_at = null,
                last_status = 'error',
                last_error = 'interrupted_check'
            where check_started_at is not null
            """
        )

    def create_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = payload.get("createdAt") or utc_now()
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
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into monitors (
                    id, name, url, target_json, condition_json,
                    interval_seconds, enabled, created_at, last_checked_at,
                    next_check_at, last_status, last_error, last_duration_ms,
                    last_value, consecutive_failures, check_started_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
        return monitor

    def list_monitors(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from monitors order by created_at desc").fetchall()
        return [self._monitor_from_row(row) for row in rows]

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
        return self._monitor_from_row(row) if row else None

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

    def delete_monitor(self, monitor_id: str) -> bool:
        with self.connect() as conn:
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

    def create_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        log = {
            "id": payload.get("id") or str(uuid4()),
            "monitorId": payload.get("monitorId"),
            "status": payload["status"],
            "previousValue": payload.get("previousValue"),
            "currentValue": payload.get("currentValue"),
            "conditionMatched": bool(payload.get("conditionMatched", False)),
            "message": payload["message"],
            "details": payload.get("details") or {},
            "createdAt": payload.get("createdAt") or utc_now(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into logs (
                    id, monitor_id, status, previous_value, current_value,
                    condition_matched, message, details_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log["id"],
                    log["monitorId"],
                    log["status"],
                    log["previousValue"],
                    log["currentValue"],
                    1 if log["conditionMatched"] else 0,
                    log["message"],
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
        return {
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
        }

    @staticmethod
    def _log_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "monitorId": row["monitor_id"],
            "status": row["status"],
            "previousValue": row["previous_value"],
            "currentValue": row["current_value"],
            "conditionMatched": bool(row["condition_matched"]),
            "message": row["message"],
            "details": json.loads(row["details_json"]),
            "createdAt": row["created_at"],
        }
