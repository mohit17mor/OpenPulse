from openpulse.browser import SessionFirstExtractor
from openpulse.checker import ExtractedValue


class FakeSessionExtractor:
    def __init__(self, available, result):
        self.available = available
        self.result = result
        self.calls = 0

    def has_active_session(self):
        return self.available

    async def extract(self, url, target):
        self.calls += 1
        return self.result


class FakeFallbackExtractor:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def extract(self, url, target):
        self.calls += 1
        return self.result


async def test_session_first_extractor_uses_active_browser_session():
    session = FakeSessionExtractor(True, ExtractedValue(True, "₹7,250,000.00", {"source": "browser_session"}))
    fallback = FakeFallbackExtractor(ExtractedValue(False, None, {"source": "headless"}))
    extractor = SessionFirstExtractor(session, fallback)

    result = await extractor.extract("https://example.test", {"selector": "#price"})

    assert result.value == "₹7,250,000.00"
    assert result.details["source"] == "browser_session"
    assert session.calls == 1
    assert fallback.calls == 0


async def test_session_first_extractor_falls_back_without_browser_session():
    session = FakeSessionExtractor(False, ExtractedValue(True, "unused", {"source": "browser_session"}))
    fallback = FakeFallbackExtractor(ExtractedValue(True, "$99.00", {"source": "headless"}))
    extractor = SessionFirstExtractor(session, fallback)

    result = await extractor.extract("https://example.test", {"selector": "#price"})

    assert result.value == "$99.00"
    assert result.details["source"] == "headless"
    assert session.calls == 0
    assert fallback.calls == 1

