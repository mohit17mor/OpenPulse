from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from openpulse.checker import ExtractedValue


ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parent / "static"

_FOCUS_TRACKER_SCRIPT = """
(() => {
  if (window.__openPulseFocusTracker) return;
  window.__openPulseFocusTracker = true;
  const notify = () => {
    if (!window.openPulsePageFocused) return;
    window.openPulsePageFocused({
      url: window.location.href,
      visibilityState: document.visibilityState
    });
  };
  window.addEventListener("focus", notify, true);
  window.addEventListener("pageshow", notify, true);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") notify();
  });
  setTimeout(notify, 0);
})();
"""


def _binding_page(source: Any) -> Page | None:
    if isinstance(source, dict):
        page = source.get("page")
    else:
        page = getattr(source, "page", None)
    return page if page is not None else None


class BrowserController:
    def __init__(self):
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._prepared_page_ids: set[int] = set()
        self._internal_page_ids: set[int] = set()
        self._ignore_new_pages = 0
        self._bindings_exposed = False
        self.latest_selection: dict[str, Any] | None = None

    async def launch(self) -> dict[str, str]:
        if self.has_active_session():
            self.page = self._latest_open_page()
            if self.page is None and self.context is not None:
                self.page = await self.context.new_page()
                await self._prepare_page(self.page)
                return {"status": "opened"}
            if self.page is not None:
                await self._prepare_page(self.page)
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
        await self._expose_bindings()
        await self.context.add_init_script(script=(STATIC_DIR / "overlay.js").read_text())
        await self.context.add_init_script(script=_FOCUS_TRACKER_SCRIPT)
        self.context.on("page", lambda page: self._schedule_prepare_page(page))
        self.page = await self.context.new_page()
        await self._prepare_page(self.page)
        return {"status": "opened"}

    def has_active_session(self) -> bool:
        return self.context is not None and self.browser is not None and self.browser.is_connected()

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
        await self._disable_monitor_mode_except(page)
        await page.evaluate("window.OpenPulseOverlay.enable()")
        return {"status": "monitor_mode_enabled"}

    async def close(self) -> None:
        if self.browser is not None:
            with suppress(Exception):
                await self.browser.close()
        if self.playwright is not None:
            with suppress(Exception):
                await self.playwright.stop()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._prepared_page_ids.clear()
        self._internal_page_ids.clear()
        self._ignore_new_pages = 0
        self._bindings_exposed = False

    async def extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        if self.context is None:
            return ExtractedValue(found=False, value=None, details={"reason": "no_browser_session"})
        self._ignore_new_pages += 1
        page = await self.context.new_page()
        self._internal_page_ids.add(id(page))
        try:
            return await extract_from_page(page, url, target, source="browser_session")
        finally:
            await page.close()
            self._internal_page_ids.discard(id(page))

    async def ensure_page(self) -> Page:
        if self.page is not None and not self.page.is_closed():
            await self._prepare_page(self.page)
            return self.page

        self.page = self._latest_open_page()
        if self.page is not None:
            await self._prepare_page(self.page)
            return self.page

        if self.context is not None:
            self.page = await self.context.new_page()
            await self._prepare_page(self.page)
            return self.page

        if self.page is None:
            await self.launch()
        assert self.page is not None
        return self.page

    def _latest_open_page(self) -> Page | None:
        if self.context is None:
            return None
        for page in reversed(self.context.pages):
            if not page.is_closed():
                return page
        return None

    def _schedule_prepare_page(self, page: Page) -> None:
        activate = True
        if self._ignore_new_pages > 0:
            self._ignore_new_pages -= 1
            activate = False
            self._internal_page_ids.add(id(page))
        if activate:
            self._activate_page(page)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._prepare_page(page, activate=activate))

    async def _prepare_page(self, page: Page, *, activate: bool = True) -> None:
        if page.is_closed():
            return
        if activate:
            self._activate_page(page)
        page_id = id(page)
        if page_id not in self._prepared_page_ids:
            self._prepared_page_ids.add(page_id)
            page.on("close", lambda _page: self._handle_page_closed(page))

    def _handle_page_closed(self, closed_page: Page) -> None:
        self._prepared_page_ids.discard(id(closed_page))
        self._internal_page_ids.discard(id(closed_page))
        if self.page is closed_page:
            self.page = self._latest_open_page()

    def _activate_page(self, page: Page) -> None:
        if page.is_closed() or id(page) in self._internal_page_ids:
            return
        self.page = page

    async def _disable_monitor_mode_except(self, active_page: Page) -> None:
        if self.context is None:
            return
        for page in self.context.pages:
            if page is active_page or page.is_closed() or id(page) in self._internal_page_ids:
                continue
            with suppress(Exception):
                await page.evaluate("window.OpenPulseOverlay?.disable?.()")

    async def _expose_bindings(self) -> None:
        if self.context is None or self._bindings_exposed:
            return

        async def selection_binding(_source: Any, selection: dict[str, Any]) -> None:
            page = _binding_page(_source)
            if page is not None:
                self._activate_page(page)
            self.latest_selection = selection

        async def focus_binding(_source: Any, _payload: dict[str, Any] | None = None) -> None:
            page = _binding_page(_source)
            if page is not None:
                self._activate_page(page)

        async def monitor_mode_binding(_source: Any, _payload: dict[str, Any] | None = None) -> None:
            page = _binding_page(_source)
            if page is None:
                return
            self._activate_page(page)
            await self._disable_monitor_mode_except(page)

        await self.context.expose_binding("openPulseSelectCandidate", selection_binding)
        await self.context.expose_binding("openPulsePageFocused", focus_binding)
        await self.context.expose_binding("openPulseMonitorModeEnabled", monitor_mode_binding)
        self._bindings_exposed = True

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
                return await extract_from_page(page, url, target, source="headless")
            finally:
                await browser.close()


class SessionFirstExtractor:
    def __init__(self, session_extractor: Any, fallback_extractor: Any):
        self.session_extractor = session_extractor
        self.fallback_extractor = fallback_extractor

    async def extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        if self.session_extractor.has_active_session():
            return await self.session_extractor.extract(url, target)
        return await self.fallback_extractor.extract(url, target)


async def extract_from_page(page: Page, url: str, target: dict[str, Any], *, source: str) -> ExtractedValue:
    response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(2500)
    blocker = await _detect_security_verification(page, response.status if response else None)
    if blocker is not None:
        blocker["source"] = source
        return ExtractedValue(found=False, value=None, details=blocker)

    if target.get("sourceType") == "website" and target.get("mode") == "items":
        items = await _extract_target_items(page, target)
        if not items:
            return ExtractedValue(
                found=False,
                value=None,
                details={
                    "reason": "target_not_found",
                    "source": source,
                    "selector": target.get("selector"),
                    "semanticType": "item_list",
                },
            )
        return ExtractedValue(
            found=True,
            value=str(len(items)),
            details={
                "source": source,
                "selector": target.get("selector"),
                "semanticType": "item_list",
                "items": items,
            },
        )

    value = await _extract_target_value(page, target)
    if value is None:
        return ExtractedValue(
            found=False,
            value=None,
            details={
                "reason": "target_not_found",
                "source": source,
                "selector": target.get("selector"),
                "semanticType": target.get("semanticType"),
            },
        )
    return ExtractedValue(
        found=True,
        value=value,
        details={
            "source": source,
            "selector": target.get("selector"),
            "semanticType": target.get("semanticType"),
        },
    )


async def _detect_security_verification(page: Page, status: int | None) -> dict[str, Any] | None:
    title = ""
    body = ""
    try:
        title = await page.title()
        body = (await page.locator("body").inner_text(timeout=2000))[:1000]
    except Exception:
        pass
    fingerprint = f"{title}\n{body}".lower()
    phrases = [
        "just a moment",
        "security verification",
        "verify you are not a bot",
        "cloudflare",
        "access denied",
        "unusual traffic",
    ]
    if status in {401, 403, 429} and any(phrase in fingerprint for phrase in phrases):
        return {
            "reason": "security_verification",
            "httpStatus": status,
            "title": title,
        }
    return None


async def _extract_target_items(page: Page, target: dict[str, Any]) -> list[dict[str, Any]]:
    items = await page.evaluate(
        """
        (target) => {
          const normalizeText = (text) => String(text || "").replace(/\\s+/g, " ").trim();
          const isVisible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width >= 8 && rect.height >= 8 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0;
          };
          const absoluteUrl = (href) => {
            if (!href) return "";
            try {
              return new URL(href, window.location.href).href;
            } catch (_error) {
              return href;
            }
          };
          const textFrom = (element, selector) => {
            const found = selector ? element.querySelector(selector) : null;
            return normalizeText((found || element).innerText || (found || element).textContent || "");
          };
          const container = target.selector ? document.querySelector(target.selector) : document.body;
          if (!container) return [];
          let elements = [];
          if (target.itemSelector) {
            try {
              elements = Array.from(container.querySelectorAll(target.itemSelector));
            } catch (_error) {
              elements = [];
            }
          }
          if (elements.length === 0) {
            elements = Array.from(container.children);
          }
          return elements
            .filter(isVisible)
            .map((element, index) => {
              const link = element.matches("a[href]") ? element : element.querySelector("a[href]");
              const url = absoluteUrl(link?.getAttribute("href") || "");
              const title = textFrom(element, "h1,h2,h3,[role='heading'],a[href]").slice(0, 180);
              const text = normalizeText(element.innerText || element.textContent || "").slice(0, 500);
              const attrId = element.getAttribute("data-id") || element.getAttribute("data-testid") || element.id || "";
              const id = url || attrId || title || text || `item-${index + 1}`;
              return {
                id: String(id),
                item: {
                  id: String(id),
                  title: title || text.slice(0, 120) || String(id),
                  url,
                  text,
                  index: index + 1
                }
              };
            })
            .filter((item) => item.id && item.item.text)
            .slice(0, 100);
        }
        """,
        target,
    )
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


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
