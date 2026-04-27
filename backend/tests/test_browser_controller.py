import asyncio

from openpulse.browser import BrowserController


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
