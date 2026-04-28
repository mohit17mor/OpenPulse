from fastapi.testclient import TestClient
import json
import sys

from openpulse.app import create_app
from openpulse.checker import ExtractedValue
from openpulse.storage import Database


class FakeExtractor:
    async def extract(self, url, target):
        return ExtractedValue(found=True, value="$89.00", details={"selector": target["selector"]})


def test_monitor_api_saves_and_checks_monitor(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", extractor=FakeExtractor(), start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/monitors",
        json={
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
        },
    )

    assert response.status_code == 200
    monitor_id = response.json()["id"]

    check_response = client.post(f"/api/monitors/{monitor_id}/check")
    logs_response = client.get("/api/logs")

    assert check_response.status_code == 200
    assert check_response.json()["status"] == "matched"
    assert logs_response.json()[0]["currentValue"] == "$89.00"


def test_monitor_api_deletes_monitor(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", extractor=FakeExtractor(), start_scheduler=False)
    client = TestClient(app)

    create_response = client.post(
        "/api/monitors",
        json={
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
        },
    )
    monitor_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/monitors/{monitor_id}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}
    assert client.get("/api/monitors").json() == []


def test_script_preview_api_returns_selectable_nodes(tmp_path):
    script = tmp_path / "feed.py"
    script.write_text('print("""{"items": [{"guid": "a", "title": "A"}]}""")\n')
    app = create_app(db_path=tmp_path / "openpulse.db", extractor=FakeExtractor(), start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/scripts/preview",
        json={
            "command": sys.executable,
            "args": [str(script)],
            "cwd": str(tmp_path),
            "timeoutSeconds": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["nodes"][0]["kind"] == "array"
    assert response.json()["nodes"][0]["path"] == "items"


def test_script_item_monitor_save_baselines_preview_items(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", extractor=FakeExtractor(), start_scheduler=False)
    client = TestClient(app)
    baseline_items = [{"id": "a", "item": {"id": "a", "title": "A"}}]

    response = client.post(
        "/api/monitors",
        json={
            "name": "Feed watch",
            "url": "script://feed.py",
            "target": {
                "sourceType": "script",
                "script": {"command": sys.executable, "args": ["feed.py"], "cwd": str(tmp_path), "timeoutSeconds": 5},
                "selection": {"mode": "items", "outputType": "json", "arrayPath": "items", "idField": "id"},
                "_baselineItems": baseline_items,
            },
            "condition": {"type": "new_item"},
            "intervalSeconds": 30,
        },
    )

    assert response.status_code == 200
    monitor_id = response.json()["id"]
    assert response.json()["target"].get("_baselineItems") is None
    assert client.delete(f"/api/monitors/{monitor_id}").status_code == 200


def test_website_item_monitor_save_baselines_selected_items(tmp_path):
    db_path = tmp_path / "openpulse.db"
    app = create_app(db_path=db_path, extractor=FakeExtractor(), start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/monitors",
        json={
            "name": "Social feed",
            "url": "https://example.test/feed",
            "target": {
                "sourceType": "website",
                "mode": "items",
                "selector": "main",
                "selection": {"mode": "items", "idField": "id", "displayField": "title", "urlField": "url"},
                "_baselineItems": [
                    {"id": "item-1", "item": {"id": "item-1", "title": "First"}},
                    {"id": "item-2", "item": {"id": "item-2", "title": "Second"}},
                ],
            },
            "condition": {"type": "new_item"},
            "intervalSeconds": 300,
        },
    )

    db = Database(db_path)
    assert response.status_code == 200
    assert response.json()["target"].get("_baselineItems") is None
    assert db.list_seen_item_ids(response.json()["id"]) == {"item-1", "item-2"}
