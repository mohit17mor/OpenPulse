import asyncio

from openpulse.browser import BrowserController, _looks_like_transient_http_ok_shell, extract_from_page


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
