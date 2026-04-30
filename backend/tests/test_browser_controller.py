import asyncio
from pathlib import Path

import openpulse.browser as browser_module
from openpulse.browser import BrowserController, PlaywrightExtractor, _looks_like_transient_http_ok_shell, extract_from_page


class FakePage:
    def __init__(self, closed=False):
        self.closed = closed
        self.handlers = {}
        self.evaluations = []
        self.body_text = "Actual page"

    def is_closed(self):
        return self.closed

    def on(self, event, handler):
        self.handlers[event] = handler

    async def evaluate(self, script, arg=None):
        self.evaluations.append(script)
        return None

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return FakeResponse(200)

    async def wait_for_timeout(self, timeout):
        return None

    async def title(self):
        return ""

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeContext:
    def __init__(self, pages):
        self.pages = pages

    async def expose_binding(self, name, callback):
        return None

    async def add_init_script(self, script):
        return None

    def on(self, event, handler):
        return None

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self):
        return None


class FakeChromium:
    def __init__(self, context):
        self.context = context
        self.persistent_calls = []

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.persistent_calls.append((user_data_dir, kwargs))
        return self.context


class FakePlaywright:
    def __init__(self, context):
        self.chromium = FakeChromium(context)
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakeAsyncPlaywright:
    def __init__(self, playwright):
        self.playwright = playwright

    async def start(self):
        return self.playwright


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        if self.selector == "body":
            return 1
        return 0 if self.page.body_text == "200-Ok" else 1

    async def inner_text(self, timeout=None):
        if self.selector == "body":
            return self.page.body_text
        return "$99.00"


class FakeTransientPage:
    def __init__(self):
        self.body_text = "200-Ok"
        self.reload_count = 0

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return FakeResponse(200)

    async def reload(self, wait_until=None, timeout=None):
        self.reload_count += 1
        self.body_text = "Actual page"
        return FakeResponse(200)

    async def wait_for_timeout(self, timeout):
        return None

    async def title(self):
        return ""

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeScrolledLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        if self.selector == "body":
            return 1
        return 1 if self.page.scrolled else 0

    async def inner_text(self, timeout=None):
        if self.selector == "body":
            return "Actual page"
        return "₹3,300"


class FakeScrolledPage:
    def __init__(self):
        self.scrolled = False
        self.scroll_calls = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return FakeResponse(200)

    async def reload(self, wait_until=None, timeout=None):
        return FakeResponse(200)

    async def wait_for_timeout(self, timeout):
        return None

    async def title(self):
        return ""

    async def evaluate(self, script, arg=None):
        if "window.scrollTo" in script:
            self.scrolled = True
            self.scroll_calls.append(arg["target"])
        return None

    def locator(self, selector):
        return FakeScrolledLocator(self, selector)


class FakeNavigationErrorPage:
    async def goto(self, url, wait_until=None, timeout=None):
        raise RuntimeError("net::ERR_HTTP2_PROTOCOL_ERROR")


class FakeIdentityLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        if self.selector == "body":
            return 1
        return 1 if self.selector in self.page.selector_texts else 0

    async def inner_text(self, timeout=None):
        if self.selector == "body":
            return "Actual page"
        return self.page.selector_texts.get(self.selector, "")


class FakeIdentityPage:
    def __init__(self, containers, selector_texts=None):
        self.containers = containers
        self.selector_texts = selector_texts or {}

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return FakeResponse(200)

    async def reload(self, wait_until=None, timeout=None):
        return FakeResponse(200)

    async def wait_for_timeout(self, timeout):
        return None

    async def title(self):
        return ""

    async def evaluate(self, script, arg=None):
        if "window.scrollTo" in script:
            return None
        if "openPulseCollectIdentityContainers" in script:
            return self.containers
        if "path.split" in script:
            return None
        return None

    def locator(self, selector):
        return FakeIdentityLocator(self, selector)


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


async def test_browser_controller_launch_uses_persistent_openpulse_profile(tmp_path, monkeypatch):
    context = FakeContext([])
    playwright = FakePlaywright(context)
    monkeypatch.setattr(browser_module, "async_playwright", lambda: FakeAsyncPlaywright(playwright))
    profile_dir = tmp_path / "browser-profile"
    controller = BrowserController(profile_dir=profile_dir)

    result = await controller.launch()

    assert result == {"status": "opened"}
    assert playwright.chromium.persistent_calls
    user_data_dir, kwargs = playwright.chromium.persistent_calls[0]
    assert user_data_dir == str(profile_dir)
    assert kwargs["headless"] is False
    assert kwargs["viewport"] == {"width": 1440, "height": 1000}
    assert profile_dir.is_dir()


async def test_playwright_extractor_uses_persistent_profile_for_fallback_checks(tmp_path, monkeypatch):
    context = FakeContext([])
    playwright = FakePlaywright(context)
    monkeypatch.setattr(browser_module, "async_playwright", lambda: FakeAsyncPlaywright(playwright))
    profile_dir = tmp_path / "browser-profile"
    extractor = PlaywrightExtractor(profile_dir=profile_dir)

    extracted = await extractor.extract("https://example.test/product", {"selector": "#price"})

    assert extracted.found is True
    assert playwright.chromium.persistent_calls
    user_data_dir, kwargs = playwright.chromium.persistent_calls[0]
    assert user_data_dir == str(profile_dir)
    assert kwargs["headless"] is True
    assert extracted.details["source"] == "profile_headless"


async def test_extract_from_page_recovers_from_transient_http_ok_shell():
    page = FakeTransientPage()

    extracted = await extract_from_page(page, "https://example.test/bus", {"selector": "#price"}, source="test")

    assert extracted.found is True
    assert extracted.value == "$99.00"
    assert page.reload_count == 1
    assert extracted.details["renderRecovery"] == {"transientHttpOkReloads": 1}


def test_transient_http_ok_shell_detection_is_narrow():
    assert _looks_like_transient_http_ok_shell("200-Ok")
    assert _looks_like_transient_http_ok_shell("200 OK")
    assert not _looks_like_transient_http_ok_shell("200 OK seats found")
    assert not _looks_like_transient_http_ok_shell("OK, actual content loaded")


async def test_extract_from_page_scrolls_near_saved_bounding_box_before_extracting():
    page = FakeScrolledPage()
    target = {
        "selector": "#fare",
        "boundingBox": {"x": 1273, "y": 1444, "width": 55, "height": 24},
    }

    extracted = await extract_from_page(page, "https://example.test/bus", target, source="test")

    assert extracted.found is True
    assert extracted.value == "₹3,300"
    assert page.scroll_calls == [{"x": 1273, "y": 1444, "width": 55, "height": 24}]
    assert extracted.details["scrollRestored"] is True


async def test_extract_from_page_returns_navigation_failure_instead_of_raising():
    extracted = await extract_from_page(
        FakeNavigationErrorPage(),
        "https://example.test/bus",
        {"selector": "#fare"},
        source="headless",
    )

    assert extracted.found is False
    assert extracted.value is None
    assert extracted.details["reason"] == "navigation_failed"
    assert extracted.details["source"] == "headless"
    assert "ERR_HTTP2_PROTOCOL_ERROR" in extracted.details["error"]


async def test_extract_from_page_refinds_price_by_container_identity_when_selector_changes():
    page = FakeIdentityPage(
        [
            {
                "text": "Morning Star Travels Pune to Bangalore AC Sleeper ₹3,100 4 seats left",
                "tagName": "LI",
                "selector": "#bus-a",
                "domPath": "html > body > ul > li:nth-of-type(1)",
                "features": {
                    "tokens": ["morning", "star", "travels", "pune", "bangalore", "ac", "sleeper", "seats", "left"],
                    "prices": ["₹3,100"],
                    "numbers": ["4"],
                },
                "fields": [
                    {"semanticType": "text", "text": "Morning Star Travels"},
                    {"semanticType": "price", "text": "₹3,100"},
                ],
            },
            {
                "text": "Orange Tours Pune to Bangalore AC Sleeper ₹3,450 2 seats left",
                "tagName": "LI",
                "selector": "#bus-b",
                "domPath": "html > body > ul > li:nth-of-type(2)",
                "features": {
                    "tokens": ["orange", "tours", "pune", "bangalore", "ac", "sleeper", "seats", "left"],
                    "prices": ["₹3,450"],
                    "numbers": ["2"],
                },
                "fields": [
                    {"semanticType": "text", "text": "Orange Tours"},
                    {"semanticType": "price", "text": "₹3,450"},
                ],
            },
        ]
    )
    target = {
        "semanticType": "price",
        "initialValue": "₹3,300",
        "selector": "#old-price",
        "domPath": "html > body > ul > li:nth-of-type(6) > p:nth-of-type(1)",
        "targetIdentity": {
            "container": {
                "text": "Orange Tours Pune to Bangalore AC Sleeper ₹3,300 2 seats left",
                "selector": "#old-card",
                "domPath": "html > body > ul > li:nth-of-type(6)",
            },
            "features": {
                "tokens": ["orange", "tours", "pune", "bangalore", "ac", "sleeper", "seats", "left"],
                "prices": ["₹3,300"],
                "numbers": ["2"],
            },
            "field": {
                "semanticType": "price",
                "initialValue": "₹3,300",
                "indexWithinContainer": 0,
            },
        },
    }

    extracted = await extract_from_page(page, "https://example.test/buses", target, source="test")

    assert extracted.found is True
    assert extracted.value == "₹3,450"
    assert extracted.details["refind"]["strategy"] == "container_identity"
    assert extracted.details["refind"]["matchedSelector"] == "#bus-b"
    assert extracted.details["refind"]["confidence"] >= 0.65


async def test_extract_from_page_prefers_identity_refind_over_fragile_positional_selector():
    positional_selector = "html > body:nth-of-type(1) > ul:nth-of-type(1) > li:nth-of-type(6) > p:nth-of-type(1)"
    page = FakeIdentityPage(
        [
            {
                "text": "Orange Tours Pune to Bangalore AC Sleeper ₹3,450 2 seats left",
                "tagName": "LI",
                "selector": "#bus-b",
                "domPath": "html > body > ul > li:nth-of-type(2)",
                "features": {
                    "tokens": ["orange", "tours", "pune", "bangalore", "ac", "sleeper", "seats", "left"],
                    "prices": ["₹3,450"],
                    "numbers": ["2"],
                },
                "fields": [
                    {"semanticType": "text", "text": "Orange Tours"},
                    {"semanticType": "price", "text": "₹3,450"},
                ],
            }
        ],
        selector_texts={positional_selector: "₹9,999"},
    )
    target = {
        "semanticType": "price",
        "initialValue": "₹3,300",
        "selector": positional_selector,
        "targetIdentity": {
            "container": {"text": "Orange Tours Pune to Bangalore AC Sleeper ₹3,300 2 seats left"},
            "features": {
                "tokens": ["orange", "tours", "pune", "bangalore", "ac", "sleeper", "seats", "left"],
                "prices": ["₹3,300"],
                "numbers": ["2"],
            },
            "field": {"semanticType": "price", "initialValue": "₹3,300", "indexWithinContainer": 0},
        },
    }

    extracted = await extract_from_page(page, "https://example.test/buses", target, source="test")

    assert extracted.found is True
    assert extracted.value == "₹3,450"
    assert extracted.details["extractionStrategy"] == "container_identity"


async def test_extract_from_page_does_not_refind_when_container_identity_confidence_is_low():
    positional_selector = "html > body:nth-of-type(1) > ul:nth-of-type(1) > li:nth-of-type(6) > p:nth-of-type(1)"
    page = FakeIdentityPage(
        [
            {
                "text": "Completely Different Carrier Mumbai to Delhi Seater ₹1,200 available",
                "tagName": "LI",
                "selector": "#different-card",
                "features": {
                    "tokens": ["completely", "different", "carrier", "mumbai", "delhi", "seater", "available"],
                    "prices": ["₹1,200"],
                },
                "fields": [{"semanticType": "price", "text": "₹1,200"}],
            }
        ],
        selector_texts={positional_selector: "₹9,999"},
    )
    target = {
        "semanticType": "price",
        "initialValue": "₹3,300",
        "selector": positional_selector,
        "targetIdentity": {
            "container": {"text": "Orange Tours Pune to Bangalore AC Sleeper ₹3,300 2 seats left"},
            "features": {
                "tokens": ["orange", "tours", "pune", "bangalore", "ac", "sleeper", "seats", "left"],
                "prices": ["₹3,300"],
                "numbers": ["2"],
            },
            "field": {"semanticType": "price", "initialValue": "₹3,300", "indexWithinContainer": 0},
        },
    }

    extracted = await extract_from_page(page, "https://example.test/buses", target, source="test")

    assert extracted.found is False
    assert extracted.value is None
    assert extracted.details["reason"] == "target_not_found"
    assert extracted.details["refind"]["strategy"] == "container_identity"
    assert extracted.details["refind"]["confidence"] < 0.5
