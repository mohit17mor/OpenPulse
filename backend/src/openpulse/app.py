from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from openpulse.browser import BrowserController, PlaywrightExtractor, SessionFirstExtractor
from openpulse.checker import CheckEngine, Extractor
from openpulse.delivery import DeliveryDispatcher, check_destination_health
from openpulse.sample_monitors import ensure_script_workspace, list_custom_scripts, list_sample_monitors, workspace_paths
from openpulse.scripts import run_script_preview
from openpulse.scheduler import MonitorScheduler
from openpulse.storage import Database


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
DEFAULT_DB_PATH = PACKAGE_DIR.parents[2] / "data" / "openpulse.db"
FIXTURES_DIR = PACKAGE_DIR.parents[2] / "fixtures"


class NavigateRequest(BaseModel):
    url: str


class MonitorRequest(BaseModel):
    name: str
    url: str
    target: dict[str, Any]
    condition: dict[str, Any]
    intervalSeconds: int = Field(default=300, ge=5)
    enabled: bool = True
    destinationIds: list[str] = []
    agentInstructions: str = ""


class ScriptPreviewRequest(BaseModel):
    command: str
    args: list[str] = []
    cwd: str | None = None
    timeoutSeconds: int = 10


class DestinationRequest(BaseModel):
    name: str
    type: str
    config: dict[str, Any]
    enabled: bool = True


def create_app(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    browser: BrowserController | None = None,
    extractor: Extractor | None = None,
    start_scheduler: bool = True,
    scheduler_poll_seconds: int = 5,
    scheduler_max_concurrent_checks: int = 5,
    start_delivery_dispatcher: bool = True,
) -> FastAPI:
    db = Database(db_path)
    db.initialize()
    browser_controller = browser or BrowserController()
    check_extractor = extractor or SessionFirstExtractor(
        browser_controller,
        PlaywrightExtractor(profile_dir=browser_controller.profile_dir),
    )
    check_engine = CheckEngine(db, check_extractor)
    scheduler = MonitorScheduler(
        db,
        check_engine,
        poll_seconds=scheduler_poll_seconds,
        max_concurrent_checks=scheduler_max_concurrent_checks,
    )
    delivery_dispatcher = DeliveryDispatcher(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        ensure_script_workspace()
        if start_scheduler:
            scheduler.start()
        if start_delivery_dispatcher:
            delivery_dispatcher.start()
        yield
        if start_delivery_dispatcher:
            await delivery_dispatcher.stop()
        if start_scheduler:
            await scheduler.stop()
        await browser_controller.close()

    app = FastAPI(title="OpenPulse", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if FIXTURES_DIR.exists():
        app.mount("/fixtures", StaticFiles(directory=FIXTURES_DIR, html=True), name="fixtures")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/workspace")
    async def workspace() -> dict[str, str]:
        return workspace_paths()

    @app.get("/api/script-templates")
    async def script_templates() -> list[dict[str, Any]]:
        return list_sample_monitors()

    @app.get("/api/scripts/custom")
    async def custom_scripts() -> list[dict[str, Any]]:
        return list_custom_scripts()

    @app.get("/api/sample-monitors")
    async def sample_monitors_compat() -> list[dict[str, Any]]:
        return list_sample_monitors()

    @app.post("/api/browser/launch")
    async def launch_browser() -> dict[str, str]:
        return await browser_controller.launch()

    @app.post("/api/browser/navigate")
    async def navigate_browser(request: NavigateRequest) -> dict[str, str]:
        return await browser_controller.navigate(request.url)

    @app.post("/api/browser/monitor-mode")
    async def enable_monitor_mode() -> dict[str, str]:
        return await browser_controller.enable_monitor_mode()

    @app.get("/api/selection")
    async def get_selection() -> dict[str, Any] | None:
        return browser_controller.latest_selection

    @app.post("/api/selection/clear")
    async def clear_selection() -> dict[str, str]:
        browser_controller.latest_selection = None
        return {"status": "cleared"}

    @app.post("/api/scripts/preview")
    async def preview_script(request: ScriptPreviewRequest) -> dict[str, Any]:
        return await run_script_preview(
            {
                "command": request.command,
                "args": request.args,
                "cwd": request.cwd,
                "timeoutSeconds": request.timeoutSeconds,
            }
        )

    @app.post("/api/monitors")
    async def create_monitor(request: MonitorRequest) -> dict[str, Any]:
        target = dict(request.target)
        baseline_items = target.pop("_baselineItems", [])
        monitor = db.create_monitor(
            {
                "name": request.name,
                "url": request.url,
                "target": target,
                "condition": request.condition,
                "intervalSeconds": request.intervalSeconds,
                "enabled": request.enabled,
                "destinationIds": request.destinationIds,
                "agentInstructions": request.agentInstructions,
            }
        )
        if target.get("sourceType") == "script" and target.get("selection", {}).get("mode") == "items":
            db.add_script_seen_items(monitor["id"], baseline_items)
        return monitor

    @app.get("/api/monitors")
    async def list_monitors() -> list[dict[str, Any]]:
        return db.list_monitors()

    @app.get("/api/destinations")
    async def list_destinations() -> list[dict[str, Any]]:
        return db.list_destinations()

    @app.post("/api/destinations")
    async def create_destination(request: DestinationRequest) -> dict[str, Any]:
        if request.type not in {"webhook", "command"}:
            raise HTTPException(status_code=400, detail="Destination type must be webhook or command")
        return db.create_destination(
            {
                "name": request.name,
                "type": request.type,
                "config": request.config,
                "enabled": request.enabled,
            }
        )

    @app.delete("/api/destinations/{destination_id}")
    async def delete_destination(destination_id: str) -> dict[str, str]:
        if not db.delete_destination(destination_id):
            raise HTTPException(status_code=404, detail=f"Destination not found: {destination_id}")
        return {"status": "deleted"}

    @app.post("/api/destinations/{destination_id}/health")
    async def destination_health(destination_id: str) -> dict[str, Any]:
        destination = db.get_destination(destination_id)
        if destination is None:
            raise HTTPException(status_code=404, detail=f"Destination not found: {destination_id}")
        result = await check_destination_health(destination)
        return {**result, "destinationId": destination_id}

    @app.get("/api/deliveries")
    async def list_deliveries() -> list[dict[str, Any]]:
        return db.list_deliveries()

    @app.delete("/api/monitors/{monitor_id}")
    async def delete_monitor(monitor_id: str) -> dict[str, str]:
        if not db.delete_monitor(monitor_id):
            raise HTTPException(status_code=404, detail=f"Monitor not found: {monitor_id}")
        return {"status": "deleted"}

    @app.post("/api/monitors/{monitor_id}/pause")
    async def pause_monitor(monitor_id: str) -> dict[str, Any]:
        monitor = db.set_monitor_enabled(monitor_id, False)
        if monitor is None:
            raise HTTPException(status_code=404, detail=f"Monitor not found: {monitor_id}")
        return monitor

    @app.post("/api/monitors/{monitor_id}/resume")
    async def resume_monitor(monitor_id: str) -> dict[str, Any]:
        monitor = db.set_monitor_enabled(monitor_id, True)
        if monitor is None:
            raise HTTPException(status_code=404, detail=f"Monitor not found: {monitor_id}")
        return monitor

    @app.post("/api/monitors/{monitor_id}/check")
    async def run_check(monitor_id: str) -> dict[str, Any]:
        try:
            return await check_engine.run_check(monitor_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/logs")
    async def list_logs() -> list[dict[str, Any]]:
        return db.list_logs()

    return app


app = create_app()
