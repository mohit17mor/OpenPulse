from __future__ import annotations

import asyncio
from contextlib import suppress
from time import perf_counter
from typing import Any

from openpulse.checker import CheckEngine
from openpulse.storage import Database


class MonitorScheduler:
    def __init__(
        self,
        db: Database,
        check_engine: CheckEngine,
        *,
        poll_seconds: int = 5,
        max_concurrent_checks: int = 5,
    ):
        self.db = db
        self.check_engine = check_engine
        self.poll_seconds = poll_seconds
        self.max_concurrent_checks = max(1, max_concurrent_checks)
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()
        self._monitor_locks: dict[str, asyncio.Lock] = {}

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
            semaphore = asyncio.Semaphore(self.max_concurrent_checks)
            tasks = [
                asyncio.create_task(self._run_due_monitor(monitor, semaphore))
                for monitor in self.db.list_due_monitors()
            ]
            results = await asyncio.gather(*tasks) if tasks else []
            return [result for result in results if result is not None]

    async def _run_due_monitor(
        self,
        monitor: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any] | None:
        async with semaphore:
            monitor_id = monitor["id"]
            monitor_lock = self._monitor_locks.setdefault(monitor_id, asyncio.Lock())
            if monitor_lock.locked():
                return None
            async with monitor_lock:
                check_started = perf_counter()
                self.db.mark_check_started(monitor_id)
                try:
                    return await self.check_engine.run_check(monitor_id)
                except Exception as exc:
                    self.db.create_log(
                        {
                            "monitorId": monitor_id,
                            "status": "error",
                            "eventType": "scheduler_error",
                            "severity": "error",
                            "sourceType": "scheduler",
                            "title": "Scheduled check failed",
                            "summary": "The scheduler tried to run this monitor, but the check raised an unexpected error.",
                            "previousValue": monitor.get("target", {}).get("initialValue"),
                            "currentValue": None,
                            "conditionMatched": False,
                            "message": "scheduled_check_failed",
                            "reasonCode": "scheduled_check_failed",
                            "evidence": {"error": str(exc)},
                            "actionHint": "Check the event details and run the monitor manually to reproduce the failure.",
                            "details": {"error": str(exc)},
                        }
                    )
                    self.db.record_check_result(
                        monitor_id,
                        status="error",
                        current_value=None,
                        duration_ms=max(0, round((perf_counter() - check_started) * 1000)),
                        error="scheduled_check_failed",
                    )
                    return None
