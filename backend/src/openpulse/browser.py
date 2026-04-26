from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from openpulse.checker import ExtractedValue


ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parent / "static"


class BrowserController:
    def __init__(self):
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.latest_selection: dict[str, Any] | None = None

    async def launch(self) -> dict[str, str]:
        if self.page is not None:
            return {"status": "already_open"}
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(channel="chrome", headless=False)
        except Exception:
            self.browser = await self.playwright.chromium.launch(headless=False)
        self.context = await self.browser.new_context(
            viewport={"width": 1440, "height": 1000},
            bypass_csp=True,
        )
        self.page = await self.context.new_page()
        await self._expose_bindings(self.page)
        return {"status": "opened"}

    async def navigate(self, url: str) -> dict[str, str]:
        page = await self.ensure_page()
        if not url.startswith(("http://", "https://", "file://")):
            url = f"https://{url}"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(500)
        await self.inject_overlay(page)
        return {"status": "navigated", "url": page.url}

    async def enable_monitor_mode(self) -> dict[str, str]:
        page = await self.ensure_page()
        await self.inject_overlay(page)
        await page.evaluate("window.OpenPulseOverlay.enable()")
        return {"status": "monitor_mode_enabled"}

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def ensure_page(self) -> Page:
        if self.page is None:
            await self.launch()
        assert self.page is not None
        return self.page

    async def _expose_bindings(self, page: Page) -> None:
        async def selection_binding(_source: Any, selection: dict[str, Any]) -> None:
            self.latest_selection = selection

        await page.expose_binding("openPulseSelectCandidate", selection_binding)

    async def inject_overlay(self, page: Page) -> None:
        overlay_path = STATIC_DIR / "overlay.js"
        try:
            already_loaded = await page.evaluate("Boolean(window.OpenPulseOverlay)")
        except Exception:
            already_loaded = False
        if already_loaded:
            return
        await page.add_script_tag(content=overlay_path.read_text())


class PlaywrightExtractor:
    async def extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(channel="chrome", headless=True)
            except Exception:
                browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(500)
                value = await _extract_target_value(page, target)
                if value is None:
                    return ExtractedValue(found=False, value=None, details={"reason": "target_not_found"})
                return ExtractedValue(
                    found=True,
                    value=value,
                    details={
                        "selector": target.get("selector"),
                        "semanticType": target.get("semanticType"),
                    },
                )
            finally:
                await browser.close()


async def _extract_target_value(page: Page, target: dict[str, Any]) -> str | None:
    selector = target.get("selector")
    if selector:
        locator = page.locator(selector).first
        try:
            if await locator.count() > 0:
                text = await locator.inner_text(timeout=1000)
                if text.strip():
                    return text.strip()
        except Exception:
            pass

    dom_path = target.get("domPath")
    if dom_path:
        text = await page.evaluate(
            """
            (path) => {
              const parts = path.split(" > ").filter(Boolean);
              let node = document.documentElement;
              if (parts[0] === "html") parts.shift();
              for (const part of parts) {
                const match = part.match(/^([a-z0-9-]+)(?:\\:nth-of-type\\((\\d+)\\))?$/i);
                if (!match) return null;
                const tag = match[1].toLowerCase();
                const index = Number(match[2] || "1");
                const matches = Array.from(node.children).filter((child) => child.tagName.toLowerCase() === tag);
                node = matches[index - 1];
                if (!node) return null;
              }
              return node.innerText || node.textContent || null;
            }
            """,
            dom_path,
        )
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None
