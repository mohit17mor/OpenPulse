from openpulse.checker import CheckEngine, ExtractedValue
from openpulse.scheduler import MonitorScheduler
from openpulse.storage import Database


class FakeExtractor:
    async def extract(self, url, target):
        return ExtractedValue(found=True, value="$89.00", details={"selector": target["selector"]})


def create_due_monitor(db):
    return db.create_monitor(
        {
            "name": "Scheduled price watch",
            "url": "http://example.test/product",
            "target": {
                "semanticType": "price",
                "initialValue": "$129.00",
                "selector": "#price",
                "domPath": "html > body > span",
                "nearbyText": "Demo shoe",
                "boundingBox": {"x": 10, "y": 20, "width": 80, "height": 24},
            },
            "condition": {"type": "less_than", "value": 100},
            "intervalSeconds": 30,
            "enabled": True,
        }
    )


async def test_scheduler_run_once_checks_due_monitors(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    create_due_monitor(db)
    scheduler = MonitorScheduler(db, CheckEngine(db, FakeExtractor()), poll_seconds=1)

    results = await scheduler.run_once()

    logs = db.list_logs()
    assert len(results) == 1
    assert logs[0]["status"] == "matched"
    monitor = db.list_monitors()[0]
    assert monitor["lastCheckedAt"] is not None
    assert monitor["nextCheckAt"] is not None
    assert monitor["lastStatus"] == "matched"


class ExplodingCheckEngine:
    async def run_check(self, monitor_id):
        raise RuntimeError(f"boom for {monitor_id}")


async def test_scheduler_records_lifecycle_state_for_failed_checks(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = create_due_monitor(db)
    scheduler = MonitorScheduler(db, ExplodingCheckEngine(), poll_seconds=1)

    results = await scheduler.run_once()

    logs = db.list_logs()
    updated = db.get_monitor(monitor["id"])
    assert results == []
    assert logs[0]["status"] == "error"
    assert updated["lastStatus"] == "error"
    assert updated["lastError"] == "scheduled_check_failed"
    assert updated["consecutiveFailures"] == 1
