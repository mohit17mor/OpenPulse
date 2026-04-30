from __future__ import annotations

import asyncio
from contextlib import suppress
from difflib import SequenceMatcher
import re
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from openpulse.checker import ExtractedValue


ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_BROWSER_PROFILE_DIR = ROOT / "data" / "browser-profile"
MIN_CONTAINER_REFIND_CONFIDENCE = 0.62
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_WEAK_TOKENS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "your",
    "you",
    "all",
    "new",
    "old",
    "view",
    "more",
    "less",
    "left",
    "right",
    "available",
    "unavailable",
}

_IDENTITY_CONTAINER_SCRIPT = """
() => {
  const CURRENCY_RE = /(?:[$€£₹¥]\\s?\\d[\\d,.]*|\\d[\\d,.]*\\s?(?:USD|EUR|GBP|INR))/i;
  const CURRENCY_GLOBAL_RE = /(?:[$€£₹¥]\\s?\\d[\\d,.]*|\\d[\\d,.]*\\s?(?:USD|EUR|GBP|INR))/gi;
  const NUMBER_RE = /^[-+]?\\d[\\d,]*(?:\\.\\d+)?(?:\\s?[%x])?$/i;
  const NUMBER_GLOBAL_RE = /[-+]?\\d[\\d,]*(?:\\.\\d+)?(?:\\s?[%x])?/gi;
  const STATUS_RE = /\\b(in stock|out of stock|sold out|available|unavailable|only \\d+ left|preorder|backorder|ships|delivery)\\b/i;
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "META", "LINK", "SVG", "PATH", "HEAD"]);
  const SKIP_LANDMARKS = new Set(["NAV", "FOOTER"]);

  function normalizeText(text) {
    return String(text || "").replace(/\\s+/g, " ").trim();
  }

  function classifyText(text, element) {
    const value = normalizeText(text);
    if (element?.tagName === "BUTTON" || element?.getAttribute("role") === "button") return "button";
    if (element?.tagName === "A") return "link";
    if (CURRENCY_RE.test(value)) return "price";
    if (STATUS_RE.test(value)) return "status";
    if (NUMBER_RE.test(value)) return "number";
    return "text";
  }

  function isHidden(element) {
    if (!element || SKIP_TAGS.has(element.tagName) || SKIP_LANDMARKS.has(element.tagName)) return true;
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return true;
    if (element.getAttribute("aria-hidden") === "true") return true;
    return false;
  }

  function hasUsefulChild(element) {
    return Array.from(element.children).some((child) => {
      if (isHidden(child)) return false;
      const text = normalizeText(child.innerText || child.textContent || "");
      const rect = child.getBoundingClientRect();
      return text && text.length <= 180 && rect.width >= 8 && rect.height >= 8;
    });
  }

  function isUsefulLeafText(text) {
    return text && text.length <= 180 && /[a-zA-Z0-9$€£₹¥]/.test(text) && !/^(home|menu|search|close)$/i.test(text);
  }

  function buildSelector(element) {
    if (element.id) return `#${cssEscape(element.id)}`;
    const attr = ["data-testid", "data-test", "aria-label", "name"].find((name) => element.getAttribute(name));
    if (attr) return `${element.tagName.toLowerCase()}[${attr}="${cssEscape(element.getAttribute(attr))}"]`;
    return buildDomPath(element);
  }

  function buildDomPath(element) {
    const parts = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE && node !== document) {
      const tag = node.tagName.toLowerCase();
      if (tag === "html") {
        parts.unshift("html");
        break;
      }
      const siblings = Array.from(node.parentElement?.children || []).filter((child) => child.tagName === node.tagName);
      const index = siblings.indexOf(node) + 1;
      parts.unshift(`${tag}:nth-of-type(${index})`);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return window.CSS.escape(value);
    return String(value).replace(/["\\\\]/g, "\\\\$&");
  }

  function extractFeatures(text) {
    const normalized = normalizeText(text).slice(0, 2500);
    const prices = Array.from(normalized.matchAll(CURRENCY_GLOBAL_RE)).map((match) => match[0]);
    const numbers = Array.from(normalized.matchAll(NUMBER_GLOBAL_RE)).map((match) => match[0]).slice(0, 40);
    const statuses = Array.from(normalized.matchAll(new RegExp(STATUS_RE.source, "gi"))).map((match) => match[0]);
    const tokens = Array.from(new Set((normalized.toLowerCase().match(/[a-z0-9]+/g) || []).filter((token) => token.length >= 2))).slice(0, 80);
    return { prices, numbers, statuses, tokens };
  }

  function extractFields(container) {
    const fields = [];
    const seen = new Set();
    const elements = [container, ...Array.from(container.querySelectorAll("*"))];
    for (const element of elements) {
      if (isHidden(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (!isUsefulLeafText(text) || hasUsefulChild(element)) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width < 8 || rect.height < 8) continue;
      const semanticType = classifyText(text, element);
      const key = `${semanticType}:${text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      fields.push({
        semanticType,
        text,
        selector: buildSelector(element),
        domPath: buildDomPath(element)
      });
      if (fields.length >= 80) break;
    }
    return fields;
  }

  function hasContainerShape(element, text, fields) {
    const tag = element.tagName;
    const role = element.getAttribute("role") || "";
    const className = String(element.className || "").toLowerCase();
    const id = String(element.id || "").toLowerCase();
    if (["LI", "ARTICLE", "TR"].includes(tag)) return true;
    if (["row", "listitem", "article"].includes(role)) return true;
    if (/(card|item|product|result|row|ticket|issue|quote|listing)/.test(`${className} ${id}`)) return true;
    return text.length >= 35 && fields.length >= 2;
  }

  function scoreContainer(container) {
    const typeScore = { LI: 24, ARTICLE: 24, TR: 20, SECTION: 12, DIV: 8 }[container.tagName] || 5;
    const fieldScore = Math.min(35, container.fields.length * 5);
    const priceScore = container.fields.some((field) => field.semanticType === "price") ? 15 : 0;
    const lengthScore = Math.max(0, 24 - Math.abs(container.text.length - 220) / 18);
    return typeScore + fieldScore + priceScore + lengthScore;
  }

  if (window.openPulseCollectIdentityContainers) {
    return window.openPulseCollectIdentityContainers();
  }

  const containers = [];
  const seen = new Set();
  for (const element of Array.from(document.body.querySelectorAll("body *"))) {
    if (isHidden(element) || ["HTML", "BODY"].includes(element.tagName)) continue;
    const text = normalizeText(element.innerText || element.textContent || "");
    if (text.length < 20 || text.length > 1800) continue;
    const rect = element.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 16) continue;
    const fields = extractFields(element);
    if (!fields.length || !hasContainerShape(element, text, fields)) continue;
    const key = `${Math.round(rect.x)}:${Math.round(rect.y)}:${text.slice(0, 160)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      text: text.slice(0, 1800),
      tagName: element.tagName,
      role: element.getAttribute("role") || "",
      selector: buildSelector(element),
      domPath: buildDomPath(element),
      boundingBox: {
        x: Math.round(rect.x + window.scrollX),
        y: Math.round(rect.y + window.scrollY),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      },
      features: extractFeatures(text),
      fields
    });
  }
  return containers.sort((a, b) => scoreContainer(b) - scoreContainer(a)).slice(0, 350);
}
"""

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

    primary_extraction = await _extract_target_value(page, target)
    extraction = primary_extraction
    if _should_refind_by_identity(target, primary_extraction):
        identity_extraction = await _refind_target_by_identity(page, target)
        if _is_confident_identity_extraction(identity_extraction):
            extraction = identity_extraction
        elif primary_extraction is None or _is_fragile_primary_extraction(target, primary_extraction):
            extraction = identity_extraction
    if extraction is None or extraction.get("value") is None:
        refind_details = extraction.get("details") if isinstance(extraction, dict) else None
        details = {
            "reason": "target_not_found",
            "source": source,
            "selector": target.get("selector"),
            "semanticType": target.get("semanticType"),
            **render_details,
        }
        if refind_details is not None:
            details["refind"] = refind_details
        return ExtractedValue(
            found=False,
            value=None,
            details=details,
        )
    extraction_details = extraction.get("details") or {}
    if extraction_details.get("strategy") == "container_identity":
        extraction_details = {"extractionStrategy": "container_identity", "refind": extraction_details}
    return ExtractedValue(
        found=True,
        value=extraction["value"],
        details={
            "source": source,
            "selector": target.get("selector"),
            "semanticType": target.get("semanticType"),
            **extraction_details,
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


async def _extract_target_value(page: Page, target: dict[str, Any]) -> dict[str, Any] | None:
    selector = target.get("selector")
    if selector:
        locator = page.locator(selector).first
        try:
            if await locator.count() > 0:
                text = await locator.inner_text(timeout=1000)
                if text.strip():
                    return {"value": text.strip(), "details": {"extractionStrategy": "selector"}}
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
            return {"value": text.strip(), "details": {"extractionStrategy": "dom_path"}}
    return None


def _should_refind_by_identity(target: dict[str, Any], primary_extraction: dict[str, Any] | None) -> bool:
    if not isinstance(target.get("targetIdentity"), dict):
        return False
    return primary_extraction is None or _is_fragile_primary_extraction(target, primary_extraction)


def _is_fragile_primary_extraction(target: dict[str, Any], primary_extraction: dict[str, Any]) -> bool:
    strategy = (primary_extraction.get("details") or {}).get("extractionStrategy")
    if strategy == "dom_path":
        return True
    if strategy != "selector":
        return False
    return _looks_like_positional_selector(target.get("selector"))


def _looks_like_positional_selector(selector: Any) -> bool:
    if not isinstance(selector, str):
        return False
    normalized = selector.strip().lower()
    return ":nth-of-type(" in normalized or normalized.startswith(("html >", "body >"))


def _is_confident_identity_extraction(extraction: dict[str, Any] | None) -> bool:
    if not extraction or extraction.get("value") is None:
        return False
    return float(extraction.get("confidence") or 0) >= MIN_CONTAINER_REFIND_CONFIDENCE


async def _refind_target_by_identity(page: Page, target: dict[str, Any]) -> dict[str, Any] | None:
    identity = target.get("targetIdentity")
    if not isinstance(identity, dict):
        return None
    try:
        containers = await page.evaluate(_IDENTITY_CONTAINER_SCRIPT)
    except Exception as exc:
        return {
            "value": None,
            "details": {
                "strategy": "container_identity",
                "confidence": 0,
                "reason": "container_scan_failed",
                "error": str(exc),
            },
        }
    if not isinstance(containers, list):
        containers = []
    attempt = _match_identity_container(target, containers)
    if attempt is None:
        return {
            "value": None,
            "details": {
                "strategy": "container_identity",
                "confidence": 0,
                "reason": "no_candidate_containers",
            },
        }
    if attempt["confidence"] < MIN_CONTAINER_REFIND_CONFIDENCE or attempt["value"] is None:
        details = dict(attempt["details"])
        details["reason"] = "low_confidence"
        return {"value": None, "details": details}
    return attempt


def _match_identity_container(target: dict[str, Any], containers: list[Any]) -> dict[str, Any] | None:
    identity = target.get("targetIdentity") or {}
    saved_container = identity.get("container") or {}
    saved_text = _string_or_empty(saved_container.get("text") or target.get("nearbyText"))
    saved_tokens = _identity_tokens(identity, target)
    if not saved_text and not saved_tokens:
        return None

    token_counts = _candidate_token_counts(containers)
    scored: list[dict[str, Any]] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        score = _score_identity_container(
            saved_text=saved_text,
            saved_tokens=saved_tokens,
            saved_tag=_string_or_empty(saved_container.get("tagName")),
            container=container,
            token_counts=token_counts,
        )
        value = _value_from_identity_container(identity, target, container)
        scored.append(
            {
                "container": container,
                "confidence": score,
                "value": value,
            }
        )
    if not scored:
        return None
    scored.sort(key=lambda item: item["confidence"], reverse=True)
    best = scored[0]
    second_confidence = scored[1]["confidence"] if len(scored) > 1 else 0
    if best["confidence"] - second_confidence < 0.04 and second_confidence >= MIN_CONTAINER_REFIND_CONFIDENCE:
        best["confidence"] = min(best["confidence"], second_confidence)
        best["value"] = None
    container = best["container"]
    details = {
        "strategy": "container_identity",
        "confidence": round(best["confidence"], 3),
        "matchedSelector": container.get("selector"),
        "matchedDomPath": container.get("domPath"),
        "matchedText": _string_or_empty(container.get("text"))[:300],
    }
    return {"value": best["value"], "confidence": best["confidence"], "details": details}


def _score_identity_container(
    *,
    saved_text: str,
    saved_tokens: set[str],
    saved_tag: str,
    container: dict[str, Any],
    token_counts: dict[str, int],
) -> float:
    candidate_text = _string_or_empty(container.get("text"))
    candidate_tokens = _tokens_from_features(container.get("features")) or _token_set(candidate_text)
    coverage = _weighted_coverage(saved_tokens, candidate_tokens, token_counts)
    jaccard = _weighted_jaccard(saved_tokens, candidate_tokens, token_counts)
    similarity = _text_similarity(saved_text, candidate_text)
    fields = container.get("fields") if isinstance(container.get("fields"), list) else []
    field_presence = 1.0 if fields else 0
    tag_bonus = 0.04 if saved_tag and saved_tag.upper() == _string_or_empty(container.get("tagName")).upper() else 0
    score = (coverage * 0.56) + (similarity * 0.24) + (jaccard * 0.10) + (field_presence * 0.06) + tag_bonus
    return max(0.0, min(1.0, score))


def _identity_tokens(identity: dict[str, Any], target: dict[str, Any]) -> set[str]:
    tokens = _tokens_from_features(identity.get("features"))
    if not tokens:
        container = identity.get("container") or {}
        tokens = _token_set(_string_or_empty(container.get("text") or target.get("nearbyText")))
    selected_value = _string_or_empty((identity.get("field") or {}).get("initialValue") or target.get("initialValue"))
    return {token for token in tokens - _token_set(selected_value) if token not in _WEAK_TOKENS}


def _tokens_from_features(features: Any) -> set[str]:
    if not isinstance(features, dict):
        return set()
    tokens = features.get("tokens")
    if not isinstance(tokens, list):
        return set()
    return {token for value in tokens if (token := _normalize_token(value))}


def _token_set(text: str) -> set[str]:
    return {token for token in (_normalize_token(match.group(0)) for match in _TOKEN_RE.finditer(text)) if token}


def _normalize_token(value: Any) -> str:
    token = str(value).lower()
    if len(token) < 2 or token in _WEAK_TOKENS:
        return ""
    return token


def _candidate_token_counts(containers: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for container in containers:
        if not isinstance(container, dict):
            continue
        tokens = _tokens_from_features(container.get("features")) or _token_set(_string_or_empty(container.get("text")))
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
    return counts


def _token_weight(token: str, token_counts: dict[str, int]) -> float:
    count = max(1, token_counts.get(token, 1))
    return 1.0 / count


def _weighted_coverage(saved_tokens: set[str], candidate_tokens: set[str], token_counts: dict[str, int]) -> float:
    if not saved_tokens:
        return 0.0
    total = sum(_token_weight(token, token_counts) for token in saved_tokens)
    if total <= 0:
        return 0.0
    overlap = saved_tokens & candidate_tokens
    return sum(_token_weight(token, token_counts) for token in overlap) / total


def _weighted_jaccard(saved_tokens: set[str], candidate_tokens: set[str], token_counts: dict[str, int]) -> float:
    union = saved_tokens | candidate_tokens
    if not union:
        return 0.0
    overlap = saved_tokens & candidate_tokens
    return sum(_token_weight(token, token_counts) for token in overlap) / sum(_token_weight(token, token_counts) for token in union)


def _text_similarity(saved_text: str, candidate_text: str) -> float:
    saved = _normalize_comparison_text(saved_text)
    candidate = _normalize_comparison_text(candidate_text)
    if not saved or not candidate:
        return 0.0
    return SequenceMatcher(None, saved[:1000], candidate[:1000]).ratio()


def _normalize_comparison_text(text: str) -> str:
    normalized = " ".join(_TOKEN_RE.findall(text.lower()))
    return normalized[:1200]


def _value_from_identity_container(identity: dict[str, Any], target: dict[str, Any], container: dict[str, Any]) -> str | None:
    field_identity = identity.get("field") if isinstance(identity.get("field"), dict) else {}
    semantic_type = _string_or_empty(field_identity.get("semanticType") or target.get("semanticType") or "text")
    index = field_identity.get("indexWithinContainer", 0)
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    fields = container.get("fields") if isinstance(container.get("fields"), list) else []
    matching = [
        field
        for field in fields
        if isinstance(field, dict)
        and _string_or_empty(field.get("semanticType")) == semantic_type
        and _string_or_empty(field.get("text"))
    ]
    if matching:
        selected = matching[min(max(index, 0), len(matching) - 1)]
        return _string_or_empty(selected.get("text")) or None
    if semantic_type == "price":
        prices = (container.get("features") or {}).get("prices") if isinstance(container.get("features"), dict) else []
        if isinstance(prices, list) and prices:
            selected_price = prices[min(max(index, 0), len(prices) - 1)]
            return _string_or_empty(selected_price) or None
    return None


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""
