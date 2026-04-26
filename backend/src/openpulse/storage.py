from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
                    last_checked_at text
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
                """
            )

    def create_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        monitor = {
            "id": payload.get("id") or str(uuid4()),
            "name": payload["name"],
            "url": payload["url"],
            "target": payload["target"],
            "condition": payload["condition"],
            "intervalSeconds": int(payload.get("intervalSeconds") or 300),
            "enabled": bool(payload.get("enabled", True)),
            "createdAt": payload.get("createdAt") or utc_now(),
            "lastCheckedAt": payload.get("lastCheckedAt"),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into monitors (
                    id, name, url, target_json, condition_json,
                    interval_seconds, enabled, created_at, last_checked_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            last_checked_at = monitor["lastCheckedAt"]
            if last_checked_at is None:
                due_monitors.append(monitor)
                continue
            last_checked = datetime.fromisoformat(last_checked_at)
            if last_checked.tzinfo is None:
                last_checked = last_checked.replace(tzinfo=UTC)
            if current_time - last_checked >= timedelta(seconds=monitor["intervalSeconds"]):
                due_monitors.append(monitor)
        due_monitors.sort(key=lambda item: item["createdAt"])
        return due_monitors

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from monitors where id = ?", (monitor_id,)).fetchone()
        return self._monitor_from_row(row) if row else None

    def mark_checked(self, monitor_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update monitors set last_checked_at = ? where id = ?",
                (utc_now(), monitor_id),
            )

    def delete_monitor(self, monitor_id: str) -> bool:
        with self.connect() as conn:
            conn.execute("delete from logs where monitor_id = ?", (monitor_id,))
            cursor = conn.execute("delete from monitors where id = ?", (monitor_id,))
            return cursor.rowcount > 0

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
