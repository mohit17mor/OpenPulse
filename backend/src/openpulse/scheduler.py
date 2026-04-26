from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from openpulse.checker import CheckEngine
from openpulse.storage import Database


class MonitorScheduler:
    def __init__(self, db: Database, check_engine: CheckEngine, *, poll_seconds: int = 5):
        self.db = db
        self.check_engine = check_engine
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> list[dict[str, Any]]:
        async with self._lock:
            results = []
            for monitor in self.db.list_due_monitors():
                try:
                    results.append(await self.check_engine.run_check(monitor["id"]))
                except Exception as exc:
                    self.db.create_log(
                        {
                            "monitorId": monitor["id"],
                            "status": "error",
                            "previousValue": monitor.get("target", {}).get("initialValue"),
                            "currentValue": None,
                            "conditionMatched": False,
                            "message": "scheduled_check_failed",
                            "details": {"error": str(exc)},
                        }
                    )
                    self.db.mark_checked(monitor["id"])
            return results

