from fastapi.testclient import TestClient

from openpulse.app import create_app


def test_sample_monitors_api_returns_templates(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/sample-monitors")

    assert response.status_code == 200
    samples = response.json()
    sample_ids = {sample["id"] for sample in samples}
    assert {"disk-usage", "folder-size", "rss-feed", "process-count", "system-load"}.issubset(sample_ids)
    disk_usage = next(sample for sample in samples if sample["id"] == "disk-usage")
    assert disk_usage["script"]["command"] == "python3"
    assert disk_usage["selection"]["path"] == "disk.usedPercent"
    assert disk_usage["condition"] == {"type": "greater_than", "value": 85}


def test_sample_monitor_scripts_preview_successfully(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", start_scheduler=False)
    client = TestClient(app)
    samples = client.get("/api/sample-monitors").json()

    for sample in samples:
        response = client.post("/api/scripts/preview", json=sample["script"])
        preview = response.json()
        assert response.status_code == 200
        assert preview["ok"] is True, sample["id"]
        assert preview["outputType"] == "json", sample["id"]
        assert preview["nodes"], sample["id"]
