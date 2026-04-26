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

    assert db.delete_monitor(monitor["id"]) is True
    assert db.list_monitors() == []
    assert db.list_logs() == []


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

