"""The query pipeline as a stream of progress events.

Everything here runs against a fake source, so no archive is touched. The
point of these tests is that the streaming path and the plain path are the
same code: whatever /api/query returns is the `result` event.
"""
import json
import time

import pytest

from solarapp.core import pipeline, resultcache, timings
from solarapp.core.registry import DataSource, Registry, Variable


class Fast(DataSource):
    name = "fast_source"
    display_name = "Fast (test source)"
    keywords = ["fastsource"]
    variables = [Variable("blue", "Blue channel", ["blue"])]

    def fetch(self, start, end, **extras):
        time.sleep(0.003)  # long enough to be measured in whole milliseconds
        return {"title": "fast", "series": [], "provenance": "test"}


class Broken(DataSource):
    name = "broken_source"
    display_name = "Broken (test source)"
    keywords = ["brokensource"]

    def fetch(self, start, end, **extras):
        raise RuntimeError("the archive is down")


@pytest.fixture
def reg():
    r = Registry()
    r._sources = {"fast_source": Fast(), "broken_source": Broken()}
    return r


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """A private timings file and a clean result cache per test."""
    monkeypatch.setattr(timings, "_timings", timings.Timings(tmp_path / "timings.json"))
    resultcache.get_cache().clear()
    yield


def events_for(query, reg):
    return list(pipeline.run(query, reg))


def test_the_events_arrive_in_order(reg):
    evs = events_for("fastsource yesterday", reg)
    assert [e["event"] for e in evs] == ["parsing", "parsed", "source_done", "result"]


def test_the_plan_names_the_sources_and_their_options(reg):
    plan = events_for("fastsource blue yesterday", reg)[1]
    assert plan["sources"] == [{
        "name": "fast_source", "display": "Fast (test source)",
        "options": "Blue channel", "estimate_ms": None,
        "confident": False, "cached": False,
    }]
    assert plan["start"].startswith("20") and plan["note"] is None


def test_an_estimate_appears_once_the_source_has_been_timed(reg):
    events_for("fastsource yesterday", reg)
    resultcache.get_cache().clear()  # force a real fetch again
    plan = events_for("fastsource today", reg)[1]
    assert plan["sources"][0]["estimate_ms"] is not None
    # one observation is not yet a measurement
    assert plan["sources"][0]["confident"] is False


def test_a_cached_source_is_announced_as_cached_not_estimated(reg):
    events_for("fastsource yesterday", reg)
    evs = events_for("fastsource yesterday", reg)
    assert evs[1]["sources"][0]["cached"] is True
    assert evs[1]["sources"][0]["estimate_ms"] is None
    assert evs[2]["cached"] is True
    assert evs[-1]["spec"]["cached"] == ["fast_source"]


def test_a_failing_source_reports_itself_and_does_not_sink_the_rest(reg):
    evs = events_for("fastsource and brokensource yesterday", reg)
    done = {e["source"]: e for e in evs if e["event"] == "source_done"}
    assert done["fast_source"]["ok"] is True
    assert done["broken_source"]["ok"] is False
    assert "the archive is down" in done["broken_source"]["error"]
    spec = evs[-1]["spec"]
    assert spec["kind"] == "multi" and len(spec["items"]) == 1
    assert "Some sources failed" in spec["note"]


def test_every_source_failing_is_a_502(reg):
    evs = events_for("brokensource yesterday", reg)
    assert evs[-1]["event"] == "error"
    assert evs[-1]["status"] == 502


def test_a_parse_failure_never_reaches_a_source(reg):
    evs = events_for("brokensource with no date", reg)
    assert [e["event"] for e in evs] == ["parsing", "error"]
    assert evs[-1]["status"] == 422


def test_collect_returns_what_the_result_event_carries(reg):
    spec, errors, status = pipeline.collect("fastsource yesterday", reg)
    assert status == 200 and not errors
    assert spec["title"] == "fast"
    assert spec["timings"]["total"] >= 0
    assert spec["parsed"]["sources"] == ["fast_source"]


def test_every_event_is_json_serialisable(reg):
    """The stream endpoint writes these straight out as NDJSON."""
    for ev in events_for("fastsource and brokensource yesterday", reg):
        json.loads(json.dumps(ev))


def test_timings_are_learned_and_persisted(tmp_path):
    store = timings.Timings(tmp_path / "t.json")
    store.record("goes_xrs", 1000)
    assert store.estimate("goes_xrs") == (1000, False)
    for _ in range(3):
        store.record("goes_xrs", 2000)
    ms, confident = store.estimate("goes_xrs")
    assert confident and 1000 < ms <= 2000  # moved towards the new value, not to it
    assert timings.Timings(tmp_path / "t.json").estimate("goes_xrs") == (ms, True)
    assert store.estimate("never_run") is None


def test_a_broken_timings_file_is_not_fatal(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{not json")
    assert timings.Timings(path).estimate("goes_xrs") is None


def test_peeking_at_the_cache_does_not_disturb_it():
    cache = resultcache.ResultCache()
    from datetime import datetime, timezone
    end = datetime(2020, 1, 1, tzinfo=timezone.utc)
    cache.put("k", {"a": 1}, end)
    before = cache.stats()
    assert cache.peek("k") is True
    assert cache.peek("nope") is False
    assert cache.stats() == before  # neither a hit nor a miss
