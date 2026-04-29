from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import sqlite3


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists schema_migrations (
            version integer primary key,
            name text not null,
            applied_at text not null
        )
        """
    )
    applied_versions = {
        row["version"] if isinstance(row, sqlite3.Row) else row[0]
        for row in conn.execute("select version from schema_migrations").fetchall()
    }
    for version, name, migration in MIGRATIONS:
        if version in applied_versions:
            continue
        migration(conn)
        conn.execute(
            "insert into schema_migrations (version, name, applied_at) values (?, ?, ?)",
            (version, name, utc_now()),
        )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in conn.execute(f"pragma table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("select name from sqlite_master where type = 'table' and name = ?", (table,)).fetchone()
    return row is not None


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if not _table_exists(conn, table):
        return
    if column not in _columns(conn, table):
        conn.execute(f"alter table {table} add column {column} {definition}")


def _initial_schema(conn: sqlite3.Connection) -> None:
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


def _script_seen_items(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists script_seen_items (
            monitor_id text not null,
            item_id text not null,
            item_json text not null,
            first_seen_at text not null,
            primary key(monitor_id, item_id),
            foreign key(monitor_id) references monitors(id)
        )
        """
    )


def _monitor_lifecycle_state(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "monitors", "next_check_at", "text")
    _add_column_if_missing(conn, "monitors", "last_status", "text not null default 'pending'")
    _add_column_if_missing(conn, "monitors", "last_error", "text")
    _add_column_if_missing(conn, "monitors", "last_duration_ms", "integer")
    _add_column_if_missing(conn, "monitors", "last_value", "text")
    _add_column_if_missing(conn, "monitors", "consecutive_failures", "integer not null default 0")


def _scheduler_check_state(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "monitors", "check_started_at", "text")
    conn.execute(
        """
        update monitors
        set check_started_at = null,
            last_status = 'error',
            last_error = 'interrupted_check'
        where check_started_at is not null
        """
    )


def _structured_event_logs(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "logs", "event_type", "text not null default 'check_completed'")
    _add_column_if_missing(conn, "logs", "severity", "text not null default 'info'")
    _add_column_if_missing(conn, "logs", "source_type", "text not null default 'unknown'")
    _add_column_if_missing(conn, "logs", "title", "text not null default 'Check completed'")
    _add_column_if_missing(conn, "logs", "summary", "text not null default ''")
    _add_column_if_missing(conn, "logs", "reason_code", "text")
    _add_column_if_missing(conn, "logs", "evidence_json", "text not null default '{}'")
    _add_column_if_missing(conn, "logs", "action_hint", "text")


def _event_destinations(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists destinations (
            id text primary key,
            name text not null,
            type text not null,
            config_json text not null,
            enabled integer not null default 1,
            created_at text not null
        );

        create table if not exists monitor_destinations (
            monitor_id text not null,
            destination_id text not null,
            primary key(monitor_id, destination_id),
            foreign key(monitor_id) references monitors(id),
            foreign key(destination_id) references destinations(id)
        );

        create table if not exists event_deliveries (
            id text primary key,
            log_id text not null,
            monitor_id text,
            destination_id text not null,
            status text not null,
            attempts integer not null default 0,
            next_attempt_at text not null,
            last_error text,
            response_status integer,
            payload_json text not null,
            created_at text not null,
            delivered_at text,
            foreign key(log_id) references logs(id),
            foreign key(destination_id) references destinations(id)
        );
        """
    )


def _monitor_agent_instructions(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "monitors", "agent_instructions", "text not null default ''")


MIGRATIONS: list[Migration] = [
    (1, "initial_schema", _initial_schema),
    (2, "script_seen_items", _script_seen_items),
    (3, "monitor_lifecycle_state", _monitor_lifecycle_state),
    (4, "scheduler_check_state", _scheduler_check_state),
    (5, "structured_event_logs", _structured_event_logs),
    (6, "event_destinations", _event_destinations),
    (7, "monitor_agent_instructions", _monitor_agent_instructions),
]

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1][0]
