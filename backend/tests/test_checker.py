from openpulse.checker import CheckEngine, ExtractedValue
from openpulse.storage import Database


class FakeExtractor:
    def __init__(self, extracted):
        self.extracted = extracted

    async def extract(self, url, target):
        return self.extracted


def make_monitor(db):
    return db.create_monitor(
        {
            "name": "Price drop",
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
            "intervalSeconds": 300,
            "enabled": True,
        }
    )


async def test_check_engine_logs_matched_condition(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = make_monitor(db)
    engine = CheckEngine(db, FakeExtractor(ExtractedValue(found=True, value="$89.00", details={"selector": "#price"})))

    result = await engine.run_check(monitor["id"])

    logs = db.list_logs()
    assert result["status"] == "matched"
    assert logs[0]["conditionMatched"] is True
    assert logs[0]["currentValue"] == "$89.00"
    assert logs[0]["message"] == "number_less_than"


async def test_check_engine_logs_missing_target(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = make_monitor(db)
    engine = CheckEngine(db, FakeExtractor(ExtractedValue(found=False, value=None, details={"reason": "not_found"})))

    result = await engine.run_check(monitor["id"])

    logs = db.list_logs()
    assert result["status"] == "missing"
    assert logs[0]["conditionMatched"] is False
    assert logs[0]["message"] == "target_missing"


async def test_check_engine_logs_security_verification_as_blocked(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = make_monitor(db)
    engine = CheckEngine(
        db,
        FakeExtractor(
            ExtractedValue(
                found=False,
                value=None,
                details={"reason": "security_verification", "title": "Just a moment..."},
            )
        ),
    )

    result = await engine.run_check(monitor["id"])

    logs = db.list_logs()
    assert result["status"] == "blocked"
    assert logs[0]["message"] == "security_verification"

