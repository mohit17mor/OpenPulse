import json
import sys

from openpulse.checker import CheckEngine
from openpulse.storage import Database


def create_script_file(tmp_path, output):
    script = tmp_path / "emit.py"
    script.write_text(f"import sys\nsys.stdout.write({output!r})\n")
    return script


async def test_script_scalar_changed_check_updates_baseline(tmp_path):
    script = create_script_file(tmp_path, '{"price": 90}')
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Script price",
            "url": "script://emit.py",
            "target": {
                "sourceType": "script",
                "script": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "cwd": str(tmp_path),
                    "timeoutSeconds": 5,
                },
                "selection": {
                    "mode": "scalar",
                    "outputType": "json",
                    "path": "price",
                    "initialValue": "100",
                },
            },
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    engine = CheckEngine(db, extractor=None)

    result = await engine.run_check(monitor["id"])

    updated = db.get_monitor(monitor["id"])
    assert result["status"] == "matched"
    assert result["currentValue"] == "90"
    assert updated["target"]["selection"]["initialValue"] == "90"


async def test_script_items_baseline_then_detects_new_items(tmp_path):
    script = create_script_file(tmp_path, '{"items": [{"id": "a", "title": "A"}]}')
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "RSS items",
            "url": "script://emit.py",
            "target": {
                "sourceType": "script",
                "script": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "cwd": str(tmp_path),
                    "timeoutSeconds": 5,
                },
                "selection": {
                    "mode": "items",
                    "outputType": "json",
                    "arrayPath": "items",
                    "idField": "id",
                    "displayField": "title",
                },
            },
            "condition": {"type": "new_item"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    db.add_script_seen_items(monitor["id"], [{"id": "a", "item": {"id": "a", "title": "A"}}])
    script.write_text("print('{\"items\": [{\"id\": \"a\", \"title\": \"A\"}, {\"id\": \"b\", \"title\": \"B\"}]}')\n")
    engine = CheckEngine(db, extractor=None)

    result = await engine.run_check(monitor["id"])

    logs = db.list_logs()
    assert result["status"] == "matched"
    assert logs[0]["message"] == "new_item_detected"
    assert logs[0]["currentValue"] == "b"
    assert logs[0]["eventType"] == "new_item_detected"
    assert logs[0]["severity"] == "success"
    assert logs[0]["sourceType"] == "script"
    assert logs[0]["title"] == "New item detected"
    assert "B" in logs[0]["summary"]
    assert db.list_script_seen_item_ids(monitor["id"]) == {"a", "b"}


async def test_script_items_without_new_items_logs_checked(tmp_path):
    script = create_script_file(tmp_path, '{"items": [{"id": "a", "title": "A"}]}')
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "RSS items",
            "url": "script://emit.py",
            "target": {
                "sourceType": "script",
                "script": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "cwd": str(tmp_path),
                    "timeoutSeconds": 5,
                },
                "selection": {
                    "mode": "items",
                    "outputType": "json",
                    "arrayPath": "items",
                    "idField": "id",
                },
            },
            "condition": {"type": "new_item"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    db.add_script_seen_items(monitor["id"], [{"id": "a", "item": {"id": "a", "title": "A"}}])
    engine = CheckEngine(db, extractor=None)

    result = await engine.run_check(monitor["id"])

    assert result["status"] == "checked"
    assert result["message"] == "no_new_items"


async def test_script_scalar_json_selection_reports_invalid_json(tmp_path):
    script = create_script_file(tmp_path, "plain text")
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Script price",
            "url": "script://emit.py",
            "target": {
                "sourceType": "script",
                "script": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "cwd": str(tmp_path),
                    "timeoutSeconds": 5,
                },
                "selection": {
                    "mode": "scalar",
                    "outputType": "json",
                    "path": "price",
                    "initialValue": "100",
                },
            },
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    engine = CheckEngine(db, extractor=None)

    result = await engine.run_check(monitor["id"])

    assert result["status"] == "error"
    assert result["message"] == "script_invalid_json"
    log = db.list_logs()[0]
    assert log["eventType"] == "script_failed"
    assert log["severity"] == "error"
    assert log["title"] == "Script check failed"


async def test_script_scalar_missing_path_logs_missing(tmp_path):
    script = create_script_file(tmp_path, '{"other": 90}')
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Script price",
            "url": "script://emit.py",
            "target": {
                "sourceType": "script",
                "script": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "cwd": str(tmp_path),
                    "timeoutSeconds": 5,
                },
                "selection": {
                    "mode": "scalar",
                    "outputType": "json",
                    "path": "price",
                    "initialValue": "100",
                },
            },
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    engine = CheckEngine(db, extractor=None)

    result = await engine.run_check(monitor["id"])

    assert result["status"] == "missing"
    assert result["message"] == "script_path_missing"


async def test_script_items_missing_id_field_logs_error(tmp_path):
    script = create_script_file(tmp_path, '{"items": [{"title": "A"}]}')
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "RSS items",
            "url": "script://emit.py",
            "target": {
                "sourceType": "script",
                "script": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "cwd": str(tmp_path),
                    "timeoutSeconds": 5,
                },
                "selection": {
                    "mode": "items",
                    "outputType": "json",
                    "arrayPath": "items",
                    "idField": "id",
                },
            },
            "condition": {"type": "new_item"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    engine = CheckEngine(db, extractor=None)

    result = await engine.run_check(monitor["id"])

    assert result["status"] == "error"
    assert result["message"] == "script_item_id_missing"
