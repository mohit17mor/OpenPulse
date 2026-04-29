import json
import sys

from openpulse.delivery import DeliveryDispatcher
from openpulse.storage import Database


async def test_command_delivery_sends_event_json_to_stdin(tmp_path):
    output_path = tmp_path / "event.json"
    script_path = tmp_path / "agent.py"
    script_path.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(output_path)!r}).write_text(sys.stdin.read())\n"
    )
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    destination = db.create_destination(
        {
            "name": "Local agent",
            "type": "command",
            "config": {
                "command": sys.executable,
                "args": [str(script_path)],
                "cwd": str(tmp_path),
                "timeoutSeconds": 5,
            },
            "enabled": True,
        }
    )
    monitor = db.create_monitor(
        {
            "name": "Price watch",
            "url": "script://price",
            "target": {"sourceType": "script"},
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
            "destinationIds": [destination["id"]],
        }
    )
    log = db.create_log(
        {
            "monitorId": monitor["id"],
            "status": "matched",
            "sourceType": "script",
            "previousValue": "10",
            "currentValue": "11",
            "conditionMatched": True,
            "message": "value_changed",
            "details": {"selection": {"path": "price"}},
        }
    )
    db.enqueue_deliveries_for_log(log, monitor)

    results = await DeliveryDispatcher(db).run_once()

    deliveries = db.list_deliveries()
    payload = json.loads(output_path.read_text())
    assert results[0]["status"] == "delivered"
    assert deliveries[0]["status"] == "delivered"
    assert payload["data"]["monitor"]["name"] == "Price watch"
    assert payload["data"]["event"]["currentValue"] == "11"
