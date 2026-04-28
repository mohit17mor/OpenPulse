import json
import sqlite3

from openpulse.migrations import CURRENT_SCHEMA_VERSION
from openpulse.storage import Database


def test_fresh_database_records_all_schema_migrations(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    db.initialize()

    with db.connect() as conn:
        versions = [
            row["version"]
            for row in conn.execute("select version from schema_migrations order by version").fetchall()
        ]
        monitor_columns = {row["name"] for row in conn.execute("pragma table_info(monitors)").fetchall()}
        log_columns = {row["name"] for row in conn.execute("pragma table_info(logs)").fetchall()}

    assert versions == list(range(1, CURRENT_SCHEMA_VERSION + 1))
    assert "check_started_at" in monitor_columns
    assert "event_type" in log_columns
    assert "evidence_json" in log_columns


def test_database_upgrades_original_schema_without_losing_rows(tmp_path):
    db_path = tmp_path / "openpulse.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table monitors (
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

            create table logs (
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
        conn.execute(
            """
            insert into monitors (
                id, name, url, target_json, condition_json,
                interval_seconds, enabled, created_at, last_checked_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "monitor-1",
                "Old price watch",
                "https://example.com/product",
                json.dumps({"initialValue": "$129.00"}),
                json.dumps({"type": "changed"}),
                30,
                1,
                "2026-04-01T00:00:00+00:00",
                None,
            ),
        )
        conn.execute(
            """
            insert into logs (
                id, monitor_id, status, previous_value, current_value,
                condition_matched, message, details_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "log-1",
                "monitor-1",
                "checked",
                "$129.00",
                "$129.00",
                0,
                "value_unchanged",
                "{}",
                "2026-04-01T00:01:00+00:00",
            ),
        )

    db = Database(db_path)
    db.initialize()

    monitor = db.get_monitor("monitor-1")
    log = db.list_logs()[0]
    with db.connect() as conn:
        versions = [
            row["version"]
            for row in conn.execute("select version from schema_migrations order by version").fetchall()
        ]

    assert versions == list(range(1, CURRENT_SCHEMA_VERSION + 1))
    assert monitor["name"] == "Old price watch"
    assert monitor["lastStatus"] == "pending"
    assert monitor["checkStartedAt"] is None
    assert log["message"] == "value_unchanged"
    assert log["eventType"] == "check_completed"
    assert log["severity"] == "info"


def test_database_clears_interrupted_check_during_migration(tmp_path):
    db_path = tmp_path / "openpulse.db"
    db = Database(db_path)
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Checking monitor",
            "url": "https://example.com",
            "target": {"initialValue": "old"},
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    db.mark_check_started(monitor["id"])

    with db.connect() as conn:
        conn.execute("delete from schema_migrations where version = ?", (4,))

    db.initialize()

    migrated = db.get_monitor(monitor["id"])
    assert migrated["checkStartedAt"] is None
    assert migrated["lastStatus"] == "error"
    assert migrated["lastError"] == "interrupted_check"
