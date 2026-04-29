from openpulse.storage import Database


def test_database_persists_monitors_and_logs(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()

    monitor = db.create_monitor(
        {
            "name": "Price watch",
            "url": "https://example.com/product",
            "target": {
                "semanticType": "price",
                "initialValue": "$129.00",
                "selector": "#price",
                "domPath": "html > body > main > span",
                "nearbyText": "Demo shoe",
                "boundingBox": {"x": 10, "y": 20, "width": 80, "height": 24},
            },
            "condition": {"type": "less_than", "value": 100},
            "intervalSeconds": 300,
            "enabled": True,
        }
    )

    db.create_log(
        {
            "monitorId": monitor["id"],
            "status": "matched",
            "previousValue": "$129.00",
            "currentValue": "$89.00",
            "conditionMatched": True,
            "message": "number_less_than",
            "details": {"selector": "#price"},
        }
    )

    monitors = db.list_monitors()
    logs = db.list_logs()

    assert monitors[0]["name"] == "Price watch"
    assert monitors[0]["target"]["semanticType"] == "price"
    assert logs[0]["status"] == "matched"
    assert logs[0]["conditionMatched"] is True
    assert logs[0]["eventType"] == "condition_matched"
    assert logs[0]["severity"] == "success"
    assert logs[0]["title"] == "Condition matched"


def test_database_persists_structured_log_event_fields(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()

    log = db.create_log(
        {
            "monitorId": "monitor-1",
            "status": "missing",
            "eventType": "target_missing",
            "severity": "warning",
            "sourceType": "website",
            "title": "Target missing",
            "summary": "OpenPulse loaded the page but could not find the selected target.",
            "previousValue": "$129.00",
            "currentValue": None,
            "conditionMatched": False,
            "message": "target_missing",
            "reasonCode": "selector_not_found",
            "evidence": {"selector": "#price"},
            "actionHint": "Open the page and repair the selection.",
            "details": {"selector": "#price"},
        }
    )

    saved = db.list_logs()[0]
    assert log["eventType"] == "target_missing"
    assert saved["severity"] == "warning"
    assert saved["sourceType"] == "website"
    assert saved["title"] == "Target missing"
    assert saved["summary"].startswith("OpenPulse loaded")
    assert saved["reasonCode"] == "selector_not_found"
    assert saved["evidence"] == {"selector": "#price"}
    assert saved["actionHint"] == "Open the page and repair the selection."


def test_database_deletes_monitor_and_its_logs(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Price watch",
            "url": "https://example.com/product",
            "target": {
                "semanticType": "price",
                "initialValue": "$129.00",
                "selector": "#price",
                "domPath": "html > body > main > span",
                "nearbyText": "Demo shoe",
                "boundingBox": {"x": 10, "y": 20, "width": 80, "height": 24},
            },
            "condition": {"type": "less_than", "value": 100},
            "intervalSeconds": 300,
            "enabled": True,
        }
    )
    db.create_log(
        {
            "monitorId": monitor["id"],
            "status": "checked",
            "previousValue": "$129.00",
            "currentValue": "$129.00",
            "conditionMatched": False,
            "message": "number_not_less_than",
            "details": {},
        }
    )
    db.add_script_seen_items(monitor["id"], [{"id": "item-1", "item": {"id": "item-1"}}])

    assert db.delete_monitor(monitor["id"]) is True
    assert db.list_monitors() == []
    assert db.list_logs() == []
    assert db.list_script_seen_item_ids(monitor["id"]) == set()


def test_database_lists_due_monitors(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    due_monitor = db.create_monitor(
        {
            "name": "Due now",
            "url": "https://example.com/product",
            "target": {"initialValue": "$129.00"},
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )
    db.create_monitor(
        {
            "name": "Disabled",
            "url": "https://example.com/product",
            "target": {"initialValue": "$129.00"},
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": False,
        }
    )

    due_monitors = db.list_due_monitors()

    assert [monitor["id"] for monitor in due_monitors] == [due_monitor["id"]]


def test_database_clamps_monitor_interval_to_five_seconds(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()

    monitor = db.create_monitor(
        {
            "name": "Fast watch",
            "url": "https://example.com/product",
            "target": {"initialValue": "$129.00"},
            "condition": {"type": "changed"},
            "intervalSeconds": 1,
            "enabled": True,
        }
    )

    assert monitor["intervalSeconds"] == 5
    assert db.get_monitor(monitor["id"])["intervalSeconds"] == 5


def test_database_records_monitor_lifecycle_state(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Price watch",
            "url": "https://example.com/product",
            "target": {"initialValue": "$129.00"},
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )

    created = db.get_monitor(monitor["id"])
    assert created["nextCheckAt"] is not None
    assert created["lastStatus"] == "pending"
    assert created["consecutiveFailures"] == 0

    db.record_check_result(
        monitor["id"],
        status="missing",
        current_value=None,
        duration_ms=1234,
        error="target_missing",
    )
    failed = db.get_monitor(monitor["id"])
    assert failed["lastCheckedAt"] is not None
    assert failed["nextCheckAt"] is not None
    assert failed["lastStatus"] == "missing"
    assert failed["lastError"] == "target_missing"
    assert failed["lastDurationMs"] == 1234
    assert failed["lastValue"] is None
    assert failed["consecutiveFailures"] == 1

    db.record_check_result(
        monitor["id"],
        status="checked",
        current_value="$129.00",
        duration_ms=456,
        error=None,
    )
    recovered = db.get_monitor(monitor["id"])
    assert recovered["lastStatus"] == "checked"
    assert recovered["lastError"] is None
    assert recovered["lastDurationMs"] == 456
    assert recovered["lastValue"] == "$129.00"
    assert recovered["consecutiveFailures"] == 0


def test_database_records_check_in_progress_state(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Price watch",
            "url": "https://example.com/product",
            "target": {"initialValue": "$129.00"},
            "condition": {"type": "changed"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )

    db.mark_check_started(monitor["id"])

    checking = db.get_monitor(monitor["id"])
    assert checking["lastStatus"] == "checking"
    assert checking["checkStartedAt"] is not None
    assert db.list_due_monitors() == []

    db.record_check_result(
        monitor["id"],
        status="checked",
        current_value="$129.00",
        duration_ms=10,
        error=None,
    )
    checked = db.get_monitor(monitor["id"])
    assert checked["lastStatus"] == "checked"
    assert checked["checkStartedAt"] is None


def test_database_stores_script_seen_items_once(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Feed",
            "url": "script://feed.py",
            "target": {"sourceType": "script"},
            "condition": {"type": "new_item"},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )

    db.add_script_seen_items(
        monitor["id"],
        [
            {"id": "a", "item": {"id": "a", "title": "A"}},
            {"id": "a", "item": {"id": "a", "title": "A again"}},
            {"id": "b", "item": {"id": "b", "title": "B"}},
        ],
    )

    assert db.list_script_seen_item_ids(monitor["id"]) == {"a", "b"}


def test_database_routes_monitor_events_to_selected_destinations(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    first = db.create_destination(
        {
            "name": "Codex bridge",
            "type": "webhook",
            "config": {"url": "http://127.0.0.1:8765/codex"},
            "enabled": True,
        }
    )
    second = db.create_destination(
        {
            "name": "Claude bridge",
            "type": "command",
            "config": {"command": "claude", "args": ["-p"]},
            "enabled": True,
        }
    )
    monitor = db.create_monitor(
        {
            "name": "Price watch",
            "url": "https://example.com/product",
            "target": {"initialValue": "$129.00"},
            "condition": {"type": "less_than", "value": 100},
            "intervalSeconds": 30,
            "enabled": True,
            "destinationIds": [first["id"]],
        }
    )
    log = db.create_log(
        {
            "monitorId": monitor["id"],
            "status": "matched",
            "sourceType": "website",
            "previousValue": "$129.00",
            "currentValue": "$89.00",
            "conditionMatched": True,
            "message": "number_less_than",
            "details": {},
        }
    )

    deliveries = db.enqueue_deliveries_for_log(log, monitor)

    assert db.get_monitor(monitor["id"])["destinationIds"] == [first["id"]]
    assert [delivery["destinationId"] for delivery in deliveries] == [first["id"]]
    assert second["id"] not in [delivery["destinationId"] for delivery in deliveries]
    pending = db.list_pending_deliveries()
    assert pending[0]["payload"]["data"]["monitor"]["name"] == "Price watch"
    assert pending[0]["payload"]["data"]["event"]["currentValue"] == "$89.00"
