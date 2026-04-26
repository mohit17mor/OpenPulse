from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openpulse.browser import BrowserController, PlaywrightExtractor
from openpulse.checker import CheckEngine, Extractor
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
    intervalSeconds: int = 300
    enabled: bool = True


def create_app(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    browser: BrowserController | None = None,
    extractor: Extractor | None = None,
    start_scheduler: bool = True,
    scheduler_poll_seconds: int = 5,
) -> FastAPI:
    db = Database(db_path)
    db.initialize()
    browser_controller = browser or BrowserController()
    check_engine = CheckEngine(db, extractor or PlaywrightExtractor())
    scheduler = MonitorScheduler(db, check_engine, poll_seconds=scheduler_poll_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_scheduler:
            scheduler.start()
        yield
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

    @app.post("/api/monitors")
    async def create_monitor(request: MonitorRequest) -> dict[str, Any]:
        return db.create_monitor(
            {
                "name": request.name,
                "url": request.url,
                "target": request.target,
                "condition": request.condition,
                "intervalSeconds": request.intervalSeconds,
                "enabled": request.enabled,
            }
        )

    @app.get("/api/monitors")
    async def list_monitors() -> list[dict[str, Any]]:
        return db.list_monitors()

    @app.delete("/api/monitors/{monitor_id}")
    async def delete_monitor(monitor_id: str) -> dict[str, str]:
        if not db.delete_monitor(monitor_id):
            raise HTTPException(status_code=404, detail=f"Monitor not found: {monitor_id}")
        return {"status": "deleted"}

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
