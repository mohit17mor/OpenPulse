from fastapi.testclient import TestClient

import openpulse.app as app_module
from openpulse.app import create_app
from openpulse.sample_monitors import list_custom_scripts


def test_sample_monitors_api_returns_templates(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/script-templates")

    assert response.status_code == 200
    samples = response.json()
    sample_ids = {sample["id"] for sample in samples}
    assert {"disk-usage", "folder-size", "rss-feed", "process-count", "system-load"}.issubset(sample_ids)
    disk_usage = next(sample for sample in samples if sample["id"] == "disk-usage")
    assert disk_usage["script"]["command"] == "python3"
    assert disk_usage["script"]["args"][0].endswith("scripts/examples/disk_usage.py")
    assert disk_usage["selection"]["path"] == "disk.usedPercent"
    assert disk_usage["condition"] == {"type": "greater_than", "value": 85}


def test_sample_monitor_scripts_preview_successfully(tmp_path):
    app = create_app(db_path=tmp_path / "openpulse.db", start_scheduler=False)
    client = TestClient(app)
    samples = client.get("/api/script-templates").json()

    for sample in samples:
        response = client.post("/api/scripts/preview", json=sample["script"])
        preview = response.json()
        assert response.status_code == 200
        assert preview["ok"] is True, sample["id"]
        assert preview["outputType"] == "json", sample["id"]
        assert preview["nodes"], sample["id"]


def test_custom_script_discovery_returns_user_scripts(tmp_path):
    custom_dir = tmp_path / "scripts" / "custom"
    project_root = tmp_path
    custom_dir.mkdir(parents=True)
    (custom_dir / "arc_recent_ai_feeds.py").write_text("print('{}')\n")
    (custom_dir / ".gitkeep").write_text("")
    (custom_dir / "__pycache__").mkdir()
    (custom_dir / "__pycache__" / "cached.pyc").write_bytes(b"cache")

    scripts = list_custom_scripts(custom_dir=custom_dir, project_root=project_root)

    assert scripts == [
        {
            "id": "custom:arc_recent_ai_feeds.py",
            "name": "arc recent ai feeds",
            "description": "Custom script in scripts/custom.",
            "category": "Custom",
            "path": str(custom_dir / "arc_recent_ai_feeds.py"),
            "relativePath": "scripts/custom/arc_recent_ai_feeds.py",
            "script": {
                "command": "python3",
                "args": ["scripts/custom/arc_recent_ai_feeds.py"],
                "cwd": str(project_root),
                "timeoutSeconds": 10,
            },
            "condition": {"type": "changed"},
            "intervalSeconds": 300,
        }
    ]


def test_custom_scripts_api_returns_discovered_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "list_custom_scripts",
        lambda: [{"id": "custom:feed.py", "name": "feed", "script": {"command": "python3"}}],
    )
    app = create_app(db_path=tmp_path / "openpulse.db", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/scripts/custom")

    assert response.status_code == 200
    assert response.json() == [{"id": "custom:feed.py", "name": "feed", "script": {"command": "python3"}}]
