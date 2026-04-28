import asyncio

from openpulse.checker import CheckEngine, ExtractedValue
from openpulse.scheduler import MonitorScheduler
from openpulse.storage import Database


class FakeExtractor:
    async def extract(self, url, target):
        return ExtractedValue(found=True, value="$89.00", details={"selector": target["selector"]})


def create_due_monitor(db, *, monitor_id=None, name="Scheduled price watch"):
    return db.create_monitor(
        {
            "id": monitor_id,
            "name": name,
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
    assert logs[0]["eventType"] == "scheduler_error"
    assert logs[0]["severity"] == "error"
    assert logs[0]["sourceType"] == "scheduler"
    assert logs[0]["title"] == "Scheduled check failed"
    assert updated["lastStatus"] == "error"
    assert updated["lastError"] == "scheduled_check_failed"
    assert updated["consecutiveFailures"] == 1


def test_scheduler_defaults_to_five_concurrent_checks(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    scheduler = MonitorScheduler(db, ExplodingCheckEngine(), poll_seconds=1)

    assert scheduler.max_concurrent_checks == 5


class TrackingCheckEngine:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = []

    async def run_check(self, monitor_id):
        self.calls.append(monitor_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return {"monitorId": monitor_id, "status": "checked"}


async def test_scheduler_runs_due_monitors_with_bounded_concurrency(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    for index in range(7):
        create_due_monitor(db, monitor_id=f"monitor-{index}", name=f"Monitor {index}")
    engine = TrackingCheckEngine()
    scheduler = MonitorScheduler(db, engine, poll_seconds=1, max_concurrent_checks=3)

    results = await scheduler.run_once()

    assert len(results) == 7
    assert len(engine.calls) == 7
    assert engine.max_active == 3


class InspectingCheckEngine:
    def __init__(self, db):
        self.db = db
        self.saw_checking_state = False

    async def run_check(self, monitor_id):
        monitor = self.db.get_monitor(monitor_id)
        self.saw_checking_state = monitor["lastStatus"] == "checking" and monitor["checkStartedAt"] is not None
        self.db.record_check_result(
            monitor_id,
            status="checked",
            current_value="$89.00",
            duration_ms=1,
            error=None,
        )
        return {"monitorId": monitor_id, "status": "checked"}


async def test_scheduler_marks_monitor_checking_before_running(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    create_due_monitor(db)
    engine = InspectingCheckEngine(db)
    scheduler = MonitorScheduler(db, engine, poll_seconds=1)

    await scheduler.run_once()

    assert engine.saw_checking_state is True
    assert db.list_monitors()[0]["lastStatus"] == "checked"


async def test_scheduler_skips_monitor_when_already_locked(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = create_due_monitor(db)
    engine = TrackingCheckEngine()
    scheduler = MonitorScheduler(db, engine, poll_seconds=1)
    monitor_lock = asyncio.Lock()
    await monitor_lock.acquire()
    scheduler._monitor_locks[monitor["id"]] = monitor_lock

    try:
        result = await scheduler._run_due_monitor(monitor, asyncio.Semaphore(1))
    finally:
        monitor_lock.release()

    assert result is None
    assert engine.calls == []
