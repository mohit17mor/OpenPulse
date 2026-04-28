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
    assert logs[0]["eventType"] == "condition_matched"
    assert logs[0]["severity"] == "success"
    assert logs[0]["sourceType"] == "website"
    assert logs[0]["title"] == "Condition matched"
    assert "$89.00" in logs[0]["summary"]
    updated = db.get_monitor(monitor["id"])
    assert updated["lastStatus"] == "matched"
    assert updated["lastValue"] == "$89.00"
    assert updated["lastDurationMs"] >= 0
    assert updated["nextCheckAt"] is not None
    assert updated["consecutiveFailures"] == 0


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
    assert logs[0]["eventType"] == "target_missing"
    assert logs[0]["severity"] == "warning"
    assert logs[0]["title"] == "Target missing"
    assert logs[0]["actionHint"] == "Open the page and repair the monitor target."
    updated = db.get_monitor(monitor["id"])
    assert updated["lastStatus"] == "missing"
    assert updated["lastError"] == "target_missing"
    assert updated["consecutiveFailures"] == 1


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
    assert logs[0]["eventType"] == "page_blocked"
    assert logs[0]["severity"] == "warning"
    assert logs[0]["title"] == "Page blocked"


async def test_check_engine_detects_new_website_items(tmp_path):
    db = Database(tmp_path / "openpulse.db")
    db.initialize()
    monitor = db.create_monitor(
        {
            "name": "Social feed",
            "url": "https://example.test/feed",
            "target": {
                "sourceType": "website",
                "mode": "items",
                "semanticType": "item_list",
                "selector": "main",
                "selection": {
                    "mode": "items",
                    "idField": "id",
                    "displayField": "title",
                    "urlField": "url",
                },
            },
            "condition": {"type": "new_item"},
            "intervalSeconds": 300,
            "enabled": True,
        }
    )
    db.add_seen_items(monitor["id"], [{"id": "post-a", "item": {"id": "post-a", "title": "Already seen"}}])
    engine = CheckEngine(
        db,
        FakeExtractor(
            ExtractedValue(
                found=True,
                value="2",
                details={
                    "items": [
                        {"id": "post-a", "item": {"id": "post-a", "title": "Already seen"}},
                        {
                            "id": "post-b",
                            "item": {
                                "id": "post-b",
                                "title": "Fresh post",
                                "url": "https://example.test/post-b",
                            },
                        },
                    ]
                },
            )
        ),
    )

    result = await engine.run_check(monitor["id"])

    logs = db.list_logs()
    assert result["status"] == "matched"
    assert result["message"] == "new_items_detected"
    assert logs[0]["message"] == "new_item_detected"
    assert logs[0]["sourceType"] == "website"
    assert logs[0]["summary"] == "New item detected: Fresh post."
    assert logs[0]["details"]["url"] == "https://example.test/post-b"
    assert db.list_seen_item_ids(monitor["id"]) == {"post-a", "post-b"}
