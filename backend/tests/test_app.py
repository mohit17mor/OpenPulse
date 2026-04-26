from fastapi.testclient import TestClient

from openpulse.app import create_app
from openpulse.checker import ExtractedValue


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
