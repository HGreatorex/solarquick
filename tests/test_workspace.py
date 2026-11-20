"""The two-pane form's backend: default view, image options, structured runs.

HEK and Helioviewer are stubbed; nothing touches the network.
"""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core import hek, helioviewer, pipeline, workspace
from solarapp.core.parser import SourceRequest
from solarapp.core.registry import DataSource, Registry, get_registry
from solarapp.sources import solar_regions
from solarapp.sources.sdo_images import SdoImages

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from solarapp.server import app as server  # noqa: E402

REG = get_registry()
client = TestClient(server.app)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def flare(x=515.0, y=-232.0):
    peak = utc(2017, 9, 6, 12, 2)
    return hek.Flare("X9.3", peak - timedelta(minutes=9), peak,
                     peak + timedelta(minutes=8), x, y, 12673)


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr(helioviewer.HelioviewerImageSource, "_closest_image",
                        lambda self, t, sid: (t + timedelta(seconds=5), f"{sid}-{t:%H%M%S}"))
    monkeypatch.setenv("SOLARQUICK_CACHE", "0")
    monkeypatch.setenv("SOLARQUICK_TIMINGS", "0")


# -- default view --------------------------------------------------------

def sources(reqs):
    return [(r.source, r.extras.get("channel")) for r in reqs]


def test_default_view_sdo_era():
    reqs = workspace.default_requests(REG, utc(2017, 9, 6), utc(2017, 9, 7))
    assert sources(reqs) == [("goes_xrs", None), ("flares", None),
                             ("sdo_images", "aia_171"), ("sdo_images", "aia_304"),
                             ("positions", None)]


def test_default_view_pre_sdo_uses_eit():
    reqs = workspace.default_requests(REG, utc(2003, 10, 28), utc(2003, 10, 29))
    assert ("soho_images", "eit_171") in sources(reqs)
    assert ("soho_images", "eit_304") in sources(reqs)
    assert not any(s == "sdo_images" for s, _ in sources(reqs))


# -- image options -------------------------------------------------------

def image_reqs():
    return [SourceRequest("sdo_images", {"channel": "aia_171"}),
            SourceRequest("soho_images", {"channel": "lasco_c2"}),
            SourceRequest("goes_xrs", {})]


def test_frames_set_only_on_image_requests():
    reqs = image_reqs()
    workspace.apply_image_options(reqs, REG, utc(2017, 9, 6), utc(2017, 9, 7), frames=24)
    assert [r.extras.get("frames") for r in reqs] == [24, 24, None]


def test_crop_centres_on_flare_and_skips_coronagraph(monkeypatch):
    monkeypatch.setattr(hek, "biggest_flare", lambda s, e: flare())
    reqs = image_reqs()
    note = workspace.apply_image_options(reqs, REG, utc(2017, 9, 6), utc(2017, 9, 7),
                                         crop_fov=250)
    assert reqs[0].extras | {} == {"channel": "aia_171", "center_x": 515.0,
                                   "center_y": -232.0, "fov": 250.0}
    assert "fov" not in reqs[1].extras
    assert "X9.3" in note


def test_crop_keeps_an_existing_centre(monkeypatch):
    monkeypatch.setattr(hek, "biggest_flare", lambda s, e: pytest.fail("no lookup needed"))
    reqs = [SourceRequest("sdo_images", {"channel": "aia_131", "center_x": 1.5,
                                         "center_y": 2.0, "fov": 500.0})]
    workspace.apply_image_options(reqs, REG, utc(2017, 9, 6), utc(2017, 9, 7), crop_fov=1000)
    assert reqs[0].extras["fov"] == 1000.0 and reqs[0].extras["center_x"] == 1.5


def test_crop_without_flare_position_falls_back_to_full_disk(monkeypatch):
    monkeypatch.setattr(hek, "biggest_flare", lambda s, e: flare(x=0.0))
    monkeypatch.setattr(solar_regions, "load_days", lambda days, today: ({}, None, {}))
    reqs = image_reqs()
    note = workspace.apply_image_options(reqs, REG, utc(2017, 9, 6), utc(2017, 9, 7),
                                         crop_fov=500)
    assert "fov" not in reqs[0].extras and "full disk" in note


def test_crop_zero_clears_an_event_crop():
    reqs = [SourceRequest("sdo_images", {"channel": "aia_131", "center_x": 1.0,
                                         "center_y": 2.0, "fov": 500.0})]
    workspace.apply_image_options(reqs, REG, utc(2017, 9, 6), utc(2017, 9, 7), crop_fov=0)
    assert reqs[0].extras == {"channel": "aia_131"}


# -- helioviewer timeline ------------------------------------------------

def test_even_times_span_window_inclusive():
    t = helioviewer.even_times(utc(2017, 9, 6, 11), utc(2017, 9, 6, 12), 5)
    assert t[0] == utc(2017, 9, 6, 11) and t[-1] == utc(2017, 9, 6, 12) and len(t) == 5


def test_frames_extra_gives_a_minute_timeline(no_network):
    spec = SdoImages().fetch(utc(2017, 9, 6, 11, 40), utc(2017, 9, 6, 12, 20),
                             channel="aia_171", frames=24)
    assert len(spec["images"]) == 24
    im = spec["images"][0]
    assert im["t_req"] == "2017-09-06T11:40:00+00:00"
    assert im["time"] == "2017-09-06T11:40:05+00:00"
    assert "width=512" in im["grid_url"] and spec["label"] == "SDO AIA 171 Å"


def test_frame_at(no_network):
    fr = SdoImages().frame_at(utc(2017, 9, 6, 12, 2), channel="aia_131",
                              center_x=515.0, center_y=-232.0, fov=500.0)
    assert fr["time"] == "2017-09-06T12:02:05+00:00"
    assert "x0=515.0&y0=232.0" in fr["url"]


# -- structured runs -----------------------------------------------------

class Stub(DataSource):
    name = "stub_source"
    display_name = "Stub"
    keywords = ["stubsource"]

    def fetch(self, start, end, **extras):
        return {"title": f"stub {start:%H:%M}-{end:%H:%M}", "series": [],
                "seen": dict(extras)}


def stub_registry():
    reg = Registry()
    reg._sources = {"stub_source": Stub()}
    return reg


def drain(body, reg):
    return list(pipeline.run_structured(body, reg))


def test_structured_run_fetches_the_ticked_requests(no_network):
    evs = drain({"start": "2017-09-06T11:00:00Z", "end": "2017-09-06T13:00:00Z",
                 "requests": [{"source": "stub_source", "extras": {"a": 1}}]},
                stub_registry())
    assert [e["event"] for e in evs] == ["parsing", "parsed", "source_done", "result"]
    spec = evs[-1]["spec"]
    assert spec["title"] == "stub 11:00-13:00" and spec["seen"] == {"a": 1}
    assert spec["request"] == {"source": "stub_source", "extras": {"a": 1}}


@pytest.mark.parametrize("body, msg", [
    ({"start": "nope", "end": "2017-09-06T13:00:00Z"}, "ISO"),
    ({"start": "2017-09-06T13:00:00Z", "end": "2017-09-06T11:00:00Z"}, "after"),
    ({"start": "2017-09-06T11:00:00Z", "end": "2017-09-06T13:00:00Z",
      "requests": [{"source": "nope"}]}, "Unknown source"),
])
def test_structured_run_refusals(body, msg):
    ev = drain(body, stub_registry())[-1]
    assert ev["event"] == "error" and msg in ev["errors"][0]


def test_run_stream_endpoint(no_network, monkeypatch):
    monkeypatch.setattr(server, "get_registry", stub_registry)
    r = client.post("/api/run/stream", json={
        "start": "2017-09-06T11:00:00Z", "end": "2017-09-06T13:00:00Z",
        "requests": [{"source": "stub_source", "extras": {}}]})
    evs = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert evs[-1]["event"] == "result"


# -- parse / flare / frame endpoints --------------------------------------

def test_parse_fills_the_form_from_an_event_query(monkeypatch):
    for mod in (hek,):
        monkeypatch.setattr(mod, "flare_near", lambda t, **kw: flare())
    body = client.post("/api/parse", json={"q": "flare at 2017-09-06 12:02"}).json()
    names = [r["source"] for r in body["requests"]]
    assert names[:2] == ["flares", "goes_xrs"] and "fism" in names
    assert body["crop_fov"] == 500.0
    assert body["start"] < "2017-09-06T11:53" < body["end"]


def test_parse_bare_date_is_the_default_view():
    body = client.post("/api/parse", json={"q": "2017-09-06"}).json()
    assert body["requests"] == []
    assert body["start"].startswith("2017-09-06T00:00")


def test_parse_error_is_422():
    assert client.post("/api/parse", json={"q": "qqqqqqq zzzzzzz"}).status_code == 422


def test_flare_endpoint(monkeypatch):
    monkeypatch.setattr(hek, "biggest_flare", lambda s, e: flare())
    f = client.get("/api/flare", params={"start": "2017-09-06T00:00:00Z",
                                         "end": "2017-09-07T00:00:00Z"}).json()["flare"]
    assert f["class"] == "X9.3" and f["peak"].startswith("2017-09-06T12:02") and f["x"] == 515.0


def test_frame_endpoint(no_network):
    r = client.post("/api/frame", json={"source": "sdo_images",
                                        "extras": {"channel": "aia_304"},
                                        "time": "2017-09-06T12:02:00Z"})
    assert r.status_code == 200 and r.json()["time"] == "2017-09-06T12:02:05+00:00"
    bad = client.post("/api/frame", json={"source": "goes_xrs", "extras": {},
                                          "time": "2017-09-06T12:02:00Z"})
    assert bad.status_code == 422


def test_sources_publish_display_and_option_extras():
    rows = {s["name"]: s for s in client.get("/api/sources").json()}
    assert rows["sdo_images"]["display"] == "images"
    assert rows["hinode_sot"]["display"] == "images"
    assert rows["goes_xrs"]["display"] == "plot"
    aia171 = next(o for o in rows["sdo_images"]["options"] if o["key"] == "aia_171")
    assert aia171["extras"] == {"channel": "aia_171"}


# -- flare sites estimated from the region summary --------------------------

def srs_with(nmbr="2673", lat=0.0, lon=0.0, valid=None):
    valid = valid or datetime(2017, 9, 5).date()
    snap = solar_regions.Snapshot(valid=valid, issued=valid + timedelta(days=1))
    snap.regions.append(solar_regions.Region(nmbr=nmbr, section=solar_regions.SPOTS,
                                             valid=valid, lat=lat, lon=lon))
    return {valid: snap}


def test_region_estimate_rotates_to_the_peak(monkeypatch):
    monkeypatch.setattr(solar_regions, "load_days", lambda days, today: (srs_with(), None, {}))
    f = flare(x=0.0)
    f.peak = utc(2017, 9, 6, 12, 0)
    x, y, label = solar_regions.flare_sites_from_regions([f])[0]
    # half a day at ~13.4°/day from central meridian, B0 ≈ +7.2° in September
    assert x == pytest.approx(960 * math.sin(math.radians(6.69)), abs=2)
    assert y == pytest.approx(-960 * math.sin(math.radians(7.2)), abs=4)
    assert "AR 2673" in label


def test_region_estimate_skips_far_side_and_unknown_regions(monkeypatch):
    monkeypatch.setattr(solar_regions, "load_days",
                        lambda days, today: (srs_with(lon=88.0), None, {}))
    assert solar_regions.flare_sites_from_regions([flare(x=0.0)]) == {}
    monkeypatch.setattr(solar_regions, "load_days",
                        lambda days, today: (srs_with(nmbr="9999"), None, {}))
    assert solar_regions.flare_sites_from_regions([flare(x=0.0)]) == {}
    assert solar_regions.flare_sites_from_regions([flare()]) == {}  # already placed


def test_crop_uses_the_region_estimate(monkeypatch):
    monkeypatch.setattr(hek, "biggest_flare", lambda s, e: flare(x=0.0))
    monkeypatch.setattr(solar_regions, "load_days", lambda days, today: (srs_with(), None, {}))
    reqs = image_reqs()
    workspace.apply_image_options(reqs, REG, utc(2017, 9, 6), utc(2017, 9, 7), crop_fov=500)
    assert reqs[0].extras["fov"] == 500.0 and reqs[0].extras["center_x"] > 0


def test_flare_list_marks_estimated_positions(monkeypatch):
    from solarapp.sources.flares import Flares
    monkeypatch.setattr(hek, "search_flares", lambda s, e, min_class=None: [flare(x=0.0)])
    monkeypatch.setattr(solar_regions, "load_days", lambda days, today: (srs_with(), None, {}))
    spec = Flares().fetch(utc(2017, 9, 6), utc(2017, 9, 7))
    ev = spec["events"][0]
    assert ev["x"] is not None and ev["position_from"].startswith("AR 2673")
    assert "estimated" in spec["note"]
