"""The streaming query endpoint and the declared-metadata listing.

No archive is touched: the queries here fail to parse on purpose, which is
enough to prove the transport (NDJSON, one JSON object per line) and the
shape of what the browser reads.
"""
import json

import pytest

pytest.importorskip("httpx", reason="install with '.[dev]' to run the API tests")
fastapi_testclient = pytest.importorskip("fastapi.testclient")

from solarapp.server.app import app  # noqa: E402

client = fastapi_testclient.TestClient(app)


def lines(query):
    r = client.post("/api/query/stream", json={"q": query})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def test_a_parse_failure_streams_an_error_event():
    evs = lines("qqqqqqq zzzzzzz")
    assert [e["event"] for e in evs] == ["parsing", "error"]
    assert evs[-1]["status"] == 422
    assert "No instrument or quantity" in evs[-1]["errors"][0]


def test_the_plan_is_streamed_before_any_fetching(monkeypatch):
    """The browser must be able to draw the loading list from one early line.

    Served by a stub registry: the suite never touches a real archive, and
    what is being checked here is the transport, not the data.
    """
    from solarapp.core.registry import DataSource, Registry, Variable
    from solarapp.server import app as server

    class Stub(DataSource):
        name = "stub_source"
        display_name = "Stub (test source)"
        keywords = ["stubsource"]
        variables = [Variable("blue", "Blue channel", ["blue"])]

        def fetch(self, start, end, **extras):
            return {"title": "stub", "series": []}

    reg = Registry()
    reg._sources = {"stub_source": Stub()}
    monkeypatch.setattr(server, "get_registry", lambda: reg)

    evs = lines("stubsource blue yesterday")
    assert [e["event"] for e in evs] == ["parsing", "parsed", "source_done", "result"]
    plan = evs[1]
    assert plan["parallel"] >= 1
    assert plan["sources"][0]["display"] == "Stub (test source)"
    assert plan["sources"][0]["options"] == "Blue channel"
    assert set(plan["sources"][0]) == {"name", "display", "options", "estimate_ms",
                                       "confident", "cached"}
    assert evs[-1]["spec"]["title"] == "stub"


def test_the_plain_endpoint_still_answers_json():
    r = client.post("/api/query", json={"q": "qqqqqqq zzzzzzz"})
    assert r.status_code == 422
    assert "errors" in r.json()


def test_sources_endpoint_publishes_what_the_plugins_declare():
    rows = {s["name"]: s for s in client.get("/api/sources").json()}
    xrs = rows["goes_xrs"]
    assert xrs["coverage"] == ["1995-01-01", None]
    assert xrs["in_summary"] is True
    groups = {o["group"] for o in xrs["options"]}
    assert groups == {"channel", "resolution"}
    labels = [o["label"] for o in xrs["options"]]
    assert any("long channel" in label for label in labels)
    assert rows["hinode_sot"]["in_summary"] is False


def test_timings_endpoint_reports_what_has_been_measured():
    body = client.get("/api/timings").json()
    assert set(body) == {"enabled", "sources"}
