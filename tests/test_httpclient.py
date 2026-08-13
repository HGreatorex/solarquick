"""Pooled session and the rolling-feed TTL cache."""
import pytest

from solarapp.core import httpclient


@pytest.fixture(autouse=True)
def _clean():
    httpclient.clear_feeds()
    yield
    httpclient.clear_feeds()


def test_session_is_reused():
    assert httpclient.session() is httpclient.session()


def test_session_pools_connections():
    adapter = httpclient.session().get_adapter("https://services.swpc.noaa.gov/")
    assert adapter._pool_maxsize > 1
    assert adapter._pool_connections > 1


def test_feed_is_fetched_once_within_ttl(monkeypatch):
    calls = []

    class FakeResponse:
        def json(self):
            return {"payload": len(calls)}

    monkeypatch.setattr(httpclient, "session",
                        lambda: type("S", (), {"get": lambda self, url, timeout=None: (
                            calls.append(url), FakeResponse())[1]})())
    a = httpclient.get_json_cached("https://feed/x")
    b = httpclient.get_json_cached("https://feed/x")
    assert a == b
    assert len(calls) == 1, "the 4.6 MB SWPC feed must not be pulled twice in a row"


def test_feed_refetched_after_ttl(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(httpclient.time, "monotonic", lambda: clock[0])

    class FakeResponse:
        def json(self):
            return {"n": len(calls)}

    monkeypatch.setattr(httpclient, "session",
                        lambda: type("S", (), {"get": lambda self, url, timeout=None: (
                            calls.append(url), FakeResponse())[1]})())
    httpclient.get_json_cached("https://feed/x", ttl=60)
    clock[0] += 61
    httpclient.get_json_cached("https://feed/x", ttl=60)
    assert len(calls) == 2


def test_different_feeds_cached_separately(monkeypatch):
    calls = []

    class FakeResponse:
        def json(self):
            return {"n": len(calls)}

    monkeypatch.setattr(httpclient, "session",
                        lambda: type("S", (), {"get": lambda self, url, timeout=None: (
                            calls.append(url), FakeResponse())[1]})())
    httpclient.get_json_cached("https://feed/a")
    httpclient.get_json_cached("https://feed/b")
    assert len(calls) == 2


def test_a_failed_fetch_is_not_cached(monkeypatch):
    calls = []

    def boom(self, url, timeout=None):
        calls.append(url)
        raise RuntimeError("network down")

    monkeypatch.setattr(httpclient, "session",
                        lambda: type("S", (), {"get": boom})())
    for _ in range(2):
        with pytest.raises(RuntimeError):
            httpclient.get_json_cached("https://feed/x")
    assert len(calls) == 2, "an outage must not be cached as a result"


def test_no_direct_requests_calls_remain_outside_httpclient():
    # every source should share the pool; a stray requests.get reintroduces a
    # TLS handshake per call
    import pathlib
    import re
    root = pathlib.Path(httpclient.__file__).parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "httpclient.py":
            continue
        if re.search(r"\brequests\.(get|post|Session)\(", path.read_text()):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"direct requests calls in {offenders}"
