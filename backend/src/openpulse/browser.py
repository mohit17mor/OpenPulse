from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from openpulse.checker import ExtractedValue


ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_BROWSER_PROFILE_DIR = ROOT / "data" / "browser-profile"

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
    def __init__(self, *, profile_dir: str | Path = DEFAULT_BROWSER_PROFILE_DIR):
        self.profile_dir = Path(profile_dir)
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
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                channel="chrome",
                headless=False,
                viewport={"width": 1440, "height": 1000},
                bypass_csp=True,
            )
        except Exception:
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=False,
                viewport={"width": 1440, "height": 1000},
                bypass_csp=True,
            )
        self.browser = getattr(self.context, "browser", None)
        await self._expose_bindings()
        await self.context.add_init_script(script=(STATIC_DIR / "overlay.js").read_text())
        await self.context.add_init_script(script=_FOCUS_TRACKER_SCRIPT)
        self.context.on("page", lambda page: self._schedule_prepare_page(page))
        self.page = self._latest_open_page() or await self.context.new_page()
        await self._prepare_page(self.page)
        return {"status": "opened"}

    def has_active_session(self) -> bool:
        return self.context is not None and (self.browser is None or self.browser.is_connected())

    async def navigate(self, url: str) -> dict[str, str]:
        page = await self.ensure_page()
        if not url.startswith(("http://", "https://", "file://")):
            url = f"https://{url}"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(500)
        await _recover_transient_http_ok_shell(page)
        await self.inject_overlay(page)
        return {"status": "navigated", "url": page.url}

    async def enable_monitor_mode(self) -> dict[str, str]:
        page = await self.ensure_page()
        await self.inject_overlay(page)
        await self._disable_monitor_mode_except(page)
        await page.evaluate("window.OpenPulseOverlay.enable()")
        return {"status": "monitor_mode_enabled"}

    async def close(self) -> None:
        if self.context is not None:
            with suppress(Exception):
                await self.context.close()
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
    def __init__(self, *, profile_dir: str | Path | None = None):
        self.profile_dir = Path(profile_dir) if profile_dir is not None else None
        self._profile_lock = asyncio.Lock()

    async def extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        if self.profile_dir is not None:
            async with self._profile_lock:
                return await self._extract(url, target)
        return await self._extract(url, target)

    async def _extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        playwright = await async_playwright().start()
        try:
            if self.profile_dir is not None:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                try:
                    context = await playwright.chromium.launch_persistent_context(
                        str(self.profile_dir),
                        channel="chrome",
                        headless=True,
                        viewport={"width": 1440, "height": 1000},
                        bypass_csp=True,
                    )
                except Exception:
                    context = await playwright.chromium.launch_persistent_context(
                        str(self.profile_dir),
                        headless=True,
                        viewport={"width": 1440, "height": 1000},
                        bypass_csp=True,
                    )
                page = await context.new_page()
                try:
                    return await extract_from_page(page, url, target, source="profile_headless")
                finally:
                    await context.close()
            try:
                browser = await playwright.chromium.launch(channel="chrome", headless=True)
            except Exception:
                browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                return await extract_from_page(page, url, target, source="headless")
            finally:
                await browser.close()
        finally:
            await playwright.stop()


class SessionFirstExtractor:
    def __init__(self, session_extractor: Any, fallback_extractor: Any):
        self.session_extractor = session_extractor
        self.fallback_extractor = fallback_extractor

    async def extract(self, url: str, target: dict[str, Any]) -> ExtractedValue:
        if self.session_extractor.has_active_session():
            return await self.session_extractor.extract(url, target)
        return await self.fallback_extractor.extract(url, target)


async def extract_from_page(page: Page, url: str, target: dict[str, Any], *, source: str) -> ExtractedValue:
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        return ExtractedValue(
            found=False,
            value=None,
            details={
                "reason": "navigation_failed",
                "source": source,
                "url": url,
                "error": str(exc),
            },
        )
    await page.wait_for_timeout(2500)
    transient_reloads = await _recover_transient_http_ok_shell(page)
    render_details = {}
    if transient_reloads:
        render_details["renderRecovery"] = {"transientHttpOkReloads": transient_reloads}
    if await _scroll_near_saved_target(page, target):
        render_details["scrollRestored"] = True
    blocker = await _detect_security_verification(page, response.status if response else None)
    if blocker is not None:
        blocker["source"] = source
        blocker.update(render_details)
        return ExtractedValue(found=False, value=None, details=blocker)

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
                **render_details,
            },
        )
    return ExtractedValue(
        found=True,
        value=value,
        details={
            "source": source,
            "selector": target.get("selector"),
            "semanticType": target.get("semanticType"),
            **render_details,
        },
    )


async def _recover_transient_http_ok_shell(page: Page, *, max_reloads: int = 3) -> int:
    reloads = 0
    while reloads < max_reloads and await _is_transient_http_ok_shell(page):
        await page.reload(wait_until="domcontentloaded", timeout=45000)
        reloads += 1
        await page.wait_for_timeout(1500)
    return reloads


async def _scroll_near_saved_target(page: Page, target: dict[str, Any]) -> bool:
    bounding_box = target.get("boundingBox")
    if not isinstance(bounding_box, dict):
        return False
    x = _number_or_none(bounding_box.get("x"))
    y = _number_or_none(bounding_box.get("y"))
    if y is None:
        return False
    scroll_y = max(0, int(y - 350))
    try:
        await page.evaluate(
            """
            ({ x, y }) => {
              window.scrollTo(x, y);
            }
            """,
            {"x": 0, "y": scroll_y, "target": bounding_box},
        )
        await page.wait_for_timeout(1000)
        return True
    except Exception:
        return False


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


async def _is_transient_http_ok_shell(page: Page) -> bool:
    try:
        body = await page.locator("body").inner_text(timeout=1500)
    except Exception:
        return False
    return _looks_like_transient_http_ok_shell(body)


def _looks_like_transient_http_ok_shell(body: str) -> bool:
    compact = body.strip().lower().replace("_", "-").replace(" ", "-")
    compact = "-".join(part for part in compact.splitlines() if part.strip())
    return compact in {"200-ok", "200", "ok"}


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
