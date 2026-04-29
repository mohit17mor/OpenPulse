from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from contextlib import suppress
from typing import Any

from openpulse.storage import Database


class DeliveryDispatcher:
    def __init__(
        self,
        db: Database,
        *,
        poll_seconds: int = 2,
        max_concurrent_deliveries: int = 5,
    ):
        self.db = db
        self.poll_seconds = poll_seconds
        self.max_concurrent_deliveries = max(1, max_concurrent_deliveries)
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

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
        semaphore = asyncio.Semaphore(self.max_concurrent_deliveries)
        tasks = [
            asyncio.create_task(self._deliver_one(delivery, semaphore))
            for delivery in self.db.list_pending_deliveries()
        ]
        if not tasks:
            return []
        return await asyncio.gather(*tasks)

    async def _deliver_one(self, delivery: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
        async with semaphore:
            destination = self.db.get_destination(delivery["destinationId"])
            if destination is None or not destination["enabled"]:
                self.db.mark_delivery_failed(delivery["id"], error="destination_disabled_or_missing")
                return {"id": delivery["id"], "status": "failed"}

            self.db.mark_delivery_sending(delivery["id"])
            try:
                response_status = await self._send(destination, delivery["payload"])
            except Exception as exc:
                self.db.mark_delivery_failed(delivery["id"], error=str(exc))
                return {"id": delivery["id"], "status": "failed", "error": str(exc)}

            self.db.mark_delivery_delivered(delivery["id"], response_status=response_status)
            return {"id": delivery["id"], "status": "delivered", "responseStatus": response_status}

    async def _send(self, destination: dict[str, Any], payload: dict[str, Any]) -> int | None:
        if destination["type"] == "command":
            return await _send_command(destination["config"], payload)
        if destination["type"] == "webhook":
            return await asyncio.to_thread(_send_webhook, destination["config"], payload)
        raise ValueError(f"Unsupported destination type: {destination['type']}")


async def _send_command(config: dict[str, Any], payload: dict[str, Any]) -> int | None:
    command = config.get("command")
    if not command:
        raise ValueError("Command destination is missing command")
    args = [str(arg) for arg in config.get("args") or []]
    timeout_seconds = int(config.get("timeoutSeconds") or 30)
    process = await asyncio.create_subprocess_exec(
        str(command),
        *args,
        cwd=config.get("cwd") or None,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    event_json = json.dumps(payload, sort_keys=True).encode()
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(event_json), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        with suppress(Exception):
            await process.wait()
        raise TimeoutError("command_timeout") from exc
    if process.returncode != 0:
        raise RuntimeError((stderr or b"command_failed").decode(errors="replace").strip())
    return process.returncode


def _send_webhook(config: dict[str, Any], payload: dict[str, Any]) -> int:
    url = config.get("url")
    if not url:
        raise ValueError("Webhook destination is missing url")
    body = json.dumps(payload, sort_keys=True).encode()
    headers = {"Content-Type": "application/json"}
    headers.update(config.get("headers") or {})
    if config.get("secret"):
        headers["Authorization"] = f"Bearer {config['secret']}"
    request = urllib.request.Request(str(url), data=body, method="POST", headers=headers)
    timeout_seconds = int(config.get("timeoutSeconds") or 10)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"webhook_http_{exc.code}") from exc
