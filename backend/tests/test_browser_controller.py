import asyncio

from playwright.async_api import async_playwright

from openpulse.browser import BrowserController, extract_from_page


class FakePage:
    def __init__(self, closed=False):
        self.closed = closed
        self.handlers = {}
        self.evaluations = []

    def is_closed(self):
        return self.closed

    def on(self, event, handler):
        self.handlers[event] = handler

    async def evaluate(self, script):
        self.evaluations.append(script)
        return None


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


class FakeResponse:
    status = 200


class FakeLocator:
    async def inner_text(self, timeout=2000):
        return "normal page"

    async def count(self):
        return 0

    @property
    def first(self):
        return self


class FakeExtractionPage:
    def __init__(self, items):
        self.items = items
        self.evaluated_targets = []

    async def goto(self, url, wait_until, timeout):
        return FakeResponse()

    async def wait_for_timeout(self, timeout):
        return None

    async def title(self):
        return "Feed"

    def locator(self, selector):
        return FakeLocator()

    async def evaluate(self, script, target=None):
        self.evaluated_targets.append(target)
        return self.items


def test_browser_controller_uses_latest_open_page():
    first = FakePage()
    second = FakePage()
    controller = BrowserController()
    controller.context = FakeContext([first, second])

    assert controller._latest_open_page() is second


def test_browser_controller_falls_back_when_tracked_page_closes():
    first = FakePage()
    second = FakePage()
    controller = BrowserController()
    controller.context = FakeContext([first, second])
    controller.page = second
    second.closed = True

    controller._handle_page_closed(second)

    assert controller.page is first


async def test_browser_controller_does_not_activate_ignored_internal_page():
    page = FakePage()
    controller = BrowserController()
    controller._ignore_new_pages = 1

    controller._schedule_prepare_page(page)
    await asyncio.sleep(0)

    assert controller.page is None
    assert controller._ignore_new_pages == 0


def test_browser_controller_activates_focused_user_page():
    page = FakePage()
    controller = BrowserController()

    controller._activate_page(page)

    assert controller.page is page


def test_browser_controller_does_not_activate_internal_page():
    page = FakePage()
    controller = BrowserController()
    controller._internal_page_ids.add(id(page))

    controller._activate_page(page)

    assert controller.page is None


async def test_browser_controller_disables_monitor_mode_on_other_user_pages():
    first = FakePage()
    second = FakePage()
    internal = FakePage()
    controller = BrowserController()
    controller.context = FakeContext([first, second, internal])
    controller._internal_page_ids.add(id(internal))

    await controller._disable_monitor_mode_except(second)

    assert len(first.evaluations) == 1
    assert second.evaluations == []
    assert internal.evaluations == []


async def test_extract_from_page_returns_browser_item_list_details():
    target = {
        "sourceType": "website",
        "mode": "items",
        "selector": "main",
        "itemSelector": ":scope > article",
        "selection": {"idField": "id", "displayField": "title", "urlField": "url"},
    }
    page = FakeExtractionPage(
        [
            {"id": "post-a", "item": {"id": "post-a", "title": "A", "url": "https://example.test/a"}},
            {"id": "post-b", "item": {"id": "post-b", "title": "B", "url": "https://example.test/b"}},
        ]
    )

    extracted = await extract_from_page(page, "https://example.test/feed", target, source="browser_session")

    assert extracted.found is True
    assert extracted.value == "2"
    assert extracted.details["items"][1]["id"] == "post-b"
    assert extracted.details["semanticType"] == "item_list"


async def test_overlay_ignores_repeated_controls_inside_single_post():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:8000/fixtures/single_social_post_controls.html")
        await page.add_script_tag(path="backend/src/openpulse/static/overlay.js")

        candidates = await page.evaluate("window.OpenPulseOverlay.getCandidates()")

        await browser.close()
    list_candidates = [candidate for candidate in candidates if candidate["semanticType"] == "item_list"]
    assert list_candidates == []


async def test_overlay_detects_real_social_feed_items():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:8000/fixtures/social_feed.html")
        await page.add_script_tag(path="backend/src/openpulse/static/overlay.js")

        candidates = await page.evaluate("window.OpenPulseOverlay.getCandidates()")

        await browser.close()
    list_candidates = [candidate for candidate in candidates if candidate["semanticType"] == "item_list"]
    assert len(list_candidates) == 1
    assert list_candidates[0]["itemSelector"] == ":scope > article"
    assert len(list_candidates[0]["items"]) == 3
