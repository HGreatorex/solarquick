"""LIVE mode: SWPC feeds -> page blocks. Everything runs offline on small
fixtures that mirror the real feed shapes (checked 2026-11-25)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from solarapp.core import httpclient, live
from solarapp.server.app import app
from solarapp.sources import solar_regions as sr

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def z(dt):  # SWPC's "Z" timestamps
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def naive(dt):  # SWPC's un-suffixed timestamps (Kp, solar wind)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def xray_rows(flux_long=2e-7):
    rows = []
    for i in range(30):
        t = z(NOW - timedelta(minutes=30 - i))
        rows.append({"time_tag": t, "satellite": 18, "flux": 5e-9, "energy": "0.05-0.4nm"})
        rows.append({"time_tag": t, "satellite": 18, "flux": flux_long, "energy": "0.1-0.8nm"})
    return rows


SRS = f""":Product: 0907SRS.txt
:Issued: {NOW:%Y} {NOW:%b} {NOW:%d} 0030 UTC
#
Joint USAF/NOAA Solar Region Summary
SRS Number 1 Issued at 0030Z on {NOW:%d} {NOW:%b} {NOW:%Y}
Report compiled from data received at SWO on 06 Sep
I.  Regions with Sunspots.  Locations Valid at {(NOW - timedelta(days=1)):%d}/2400Z
Nmbr Location  Lo  Area  Z   LL   NN Mag Type
4579 N15W62   351  0130 Hsx  02   01 Alpha
IA. H-alpha Plages without Spots.  Locations Valid at {(NOW - timedelta(days=1)):%d}/2400Z Nov
Nmbr  Location  Lo
None
II. Regions Due to Return 25 Nov to 27 Nov
Nmbr Lat    Lo
4569 S06    179
"""


def region_row(num, loc, **kw):
    base = {"observed_date": NOW.date().isoformat(), "region": num, "latitude": 15, "longitude": -76,
            "location": loc, "carrington_longitude": 351, "area": 60, "spot_class": "Hsx",
            "number_spots": 1, "mag_class": "A", "c_xray_events": 0, "m_xray_events": 0,
            "x_xray_events": 0, "c_flare_probability": 10, "m_flare_probability": 1,
            "x_flare_probability": 1}
    return {**base, **kw}


@pytest.fixture
def feeds(monkeypatch):
    """Patch the one network door; tests set data by key."""
    data = {
        "scales": {"0": {"DateStamp": "2026-11-25", "TimeStamp": "10:54:00",
                         "R": {"Scale": "0", "Text": "none", "MinorProb": None, "MajorProb": None},
                         "S": {"Scale": "0", "Text": "none", "Prob": None},
                         "G": {"Scale": "1", "Text": "minor"}},
                   "1": {"DateStamp": "2026-11-26", "TimeStamp": "10:54:00",
                         "R": {"Scale": None, "Text": None, "MinorProb": "1", "MajorProb": "1"},
                         "S": {"Scale": None, "Text": None, "Prob": "1"}, "G": {"Scale": "1", "Text": "minor"}}},
        "xray6h": xray_rows(),
        "flare_latest": [{"time_tag": z(NOW), "satellite": 18, "current_class": "C2.0",
                          "begin_time": z(NOW - timedelta(minutes=9)), "begin_class": "B9.0",
                          "max_time": z(NOW - timedelta(minutes=4)), "max_class": "C5.1",
                          "end_time": None, "end_class": None}],
        "flares7d": [],
        "alerts": [
            {"product_id": "A20F", "issue_datetime": NOW.strftime("%Y-%m-%d %H:%M:%S.000"),
             "message": "Space Weather Message Code: WATA20\r\nSerial Number: 1\r\n\r\n"
                        "WATCH: Geomagnetic Storm Category G1 Predicted\r\nmore"},
            {"product_id": "OLD", "issue_datetime": (NOW - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S.000"),
             "message": "ALERT: ancient"},
        ],
        "regions": [region_row(4579, "N15W76"),
                    region_row(4582, "N19E37", m_flare_probability=20, mag_class="BGD")],
        "probs": [{"date": "2026-11-23T00:00:00", "c_class_1_day": 1, "c_class_2_day": 1, "c_class_3_day": 1,
                   "m_class_1_day": 1, "m_class_2_day": 1, "m_class_3_day": 1, "x_class_1_day": 1,
                   "x_class_2_day": 1, "x_class_3_day": 1, "10mev_protons_1_day": 1,
                   "10mev_protons_2_day": 1, "10mev_protons_3_day": 1, "polar_cap_absorption": "green"},
                  {"date": "2026-11-24T00:00:00", "c_class_1_day": 20, "c_class_2_day": 20, "c_class_3_day": 15,
                   "m_class_1_day": 5, "m_class_2_day": 4, "m_class_3_day": 3, "x_class_1_day": 1,
                   "x_class_2_day": 1, "x_class_3_day": 1, "10mev_protons_1_day": 1,
                   "10mev_protons_2_day": 1, "10mev_protons_3_day": 1, "polar_cap_absorption": "green"}],
        "kp": [{"time_tag": naive(NOW - timedelta(hours=3 * i)), "Kp": 2.33, "a_running": 9, "station_count": 8}
               for i in range(5)] + [{"time_tag": naive(NOW - timedelta(days=5)), "Kp": 9.0}],
        "protons": [{"time_tag": z(NOW), "satellite": 18, "flux": f, "energy": e}
                    for e, f in ((">=10 MeV", 0.2), (">=100 MeV", 0.1), (">=1 MeV", 3.0))],
        "wind": [{"time_tag": naive(NOW - timedelta(minutes=i)), "active": i % 2 == 0,
                  "proton_speed": 400.0 + i, "proton_density": 3.0} for i in range(10)],
        "mag": [{"time_tag": naive(NOW - timedelta(minutes=i)), "active": True, "bt": 6.0, "bz_gsm": -4.0}
                for i in range(10)],
    }
    paths = {live.BASE + p: k for k, (p, _) in live.FEEDS.items()}
    broken = set()

    def fake(url, timeout=30, ttl=60):
        key = paths[url]
        if key in broken:
            raise ValueError("Expecting value")
        return data[key]

    monkeypatch.setattr(httpclient, "get_json_cached", fake)
    monkeypatch.setattr(sr, "_load_live", lambda: sr.parse_srs(SRS))
    monkeypatch.setattr(live.hek, "search_flares", lambda *a, **k: [])
    live.clear()
    live._hek_backoff_until = 0.0
    data["_broken"] = broken
    return data


def test_flux_class():
    assert live.flux_class(2.3e-6) == "C2.3"
    assert live.flux_class(5e-5) == "M5.0"
    assert live.flux_class(1.2e-4) == "X1.2"
    assert live.flux_class(4e-9) == "A0.4"
    assert live.flux_class(None) is None


def test_now_page(feeds):
    p = live.collect(["now"])["panels"]["now"]
    assert p["xray"]["latest"]["class"] == "B2.0"
    assert [s["name"][:5] for s in p["xray"]["spec"]["series"]] == ["XRS-A", "XRS-B"]
    assert p["scales"]["0"]["G"]["scale"] == "1" and p["scales"]["1"]["R"]["minor"] == "1"
    # the open-ended event is a flare in progress
    assert p["flare_latest"]["event"]["in_progress"] is True
    # only the last day's alerts, headline picked from the message
    assert [a["product"] for a in p["alerts"]["alerts"]] == ["A20F"]
    assert p["alerts"]["alerts"][0]["headline"].startswith("WATCH: Geomagnetic")


def test_finished_flare_is_not_in_progress(feeds):
    feeds["flare_latest"][0]["end_time"] = z(NOW)
    assert not live.collect(["now"])["panels"]["now"]["flare_latest"]["event"]["in_progress"]


def test_one_dead_feed_greys_one_block(feeds):
    feeds["_broken"].add("alerts")
    p = live.collect(["now"])["panels"]["now"]
    assert p["alerts"]["error"].startswith("alerts.json")
    assert "spec" in p["xray"] and "0" in p["scales"]


def test_unknown_panel_rejected(feeds):
    with pytest.raises(ValueError):
        live.collect(["nope"])


def test_regions_use_srs_position_rotated_to_now(feeds):
    p = live.collect(["regions"])["panels"]["regions"]["regions"]
    by = {r["nmbr"]: r for r in p["regions"]}
    r = by["4579"]
    # SRS says W62 a day ago, not the JSON's W76; rotation adds ~13°/day * elapsed
    elapsed = (NOW - (datetime(NOW.year, NOW.month, NOW.day, tzinfo=timezone.utc))).total_seconds() / 86400
    assert r["lon"] == pytest.approx(62 + sr.synodic_rate(15) * elapsed, abs=0.2)
    assert not r["approx"] and r["location"].startswith("N15W")
    assert 0 < r["x"] < 1
    # not in the SRS (new region): falls back to the feed's own location string
    assert by["4582"]["approx"] and by["4582"]["location"] == "N19E37" and by["4582"]["x"] < 0
    assert by["4582"]["complexity"] == "delta" and by["4582"]["mag"] == "Beta-Gamma-Delta"
    # most flare-prone first
    assert p["regions"][0]["nmbr"] == "4582"
    assert p["regions"][0]["prob"] == {"c": 10, "m": 20, "x": 1}
    assert p["regions"][0]["query"].startswith("ar 4582 from ")
    assert p["disk_image"]["url"].startswith("https://api.helioviewer.org/v2/takeScreenshot/")


def test_regions_survive_srs_outage(feeds, monkeypatch):
    def boom():
        raise OSError("SRS down")
    monkeypatch.setattr(sr, "_load_live", boom)
    live.clear()
    p = live.collect(["regions"])["panels"]["regions"]["regions"]
    assert p["position_source"] is None and all(r["approx"] for r in p["regions"])
    assert all(r["x"] is not None for r in p["regions"])


def test_disk_probs_take_newest_day(feeds):
    p = live.collect(["regions"])["panels"]["regions"]["probs"]
    assert p["date"].startswith("2026-11-24") and p["m"] == [5, 4, 3]


def test_flares_list_and_hek_outage(feeds, monkeypatch):
    feeds["flares7d"] = [
        {"time_tag": z(NOW), "begin_time": z(NOW - timedelta(hours=2, minutes=10)), "begin_class": "B9.0",
         "max_time": z(NOW - timedelta(hours=2)), "max_class": "M1.2", "end_time": z(NOW - timedelta(hours=1, minutes=50)),
         "end_class": "C3.0", "satellite": 18},
        {"time_tag": z(NOW), "begin_time": z(NOW - timedelta(days=9)), "begin_class": "B1.0",
         "max_time": z(NOW - timedelta(days=9)), "max_class": "C1.0", "end_time": None, "end_class": None, "satellite": 18},
    ]

    def down(*a, **k):
        raise RuntimeError("HEK down")
    monkeypatch.setattr(live.hek, "search_flares", down)
    live.clear()
    live._hek_backoff_until = 0.0
    f = live.collect(["flares"])["panels"]["flares"]["flares"]
    assert [e["class"] for e in f["events"]] == ["M1.2"]          # the 9-day-old one is dropped
    assert f["events"][0]["query"].startswith("flare at ")
    assert "HEK" in f["ar_error"] and f["events"][0]["ar"] is None  # the list survives without sites


def test_flares_get_site_and_ar_from_hek(feeds, monkeypatch):
    peak = NOW - timedelta(hours=2)
    feeds["flares7d"] = [{"time_tag": z(NOW), "begin_time": z(peak), "begin_class": "B9.0", "max_time": z(peak),
                          "max_class": "M1.2", "end_time": z(NOW), "end_class": "C3.0", "satellite": 18}]
    from solarapp.core.hek import Flare
    fl = Flare("M1.2", peak, peak, NOW, 500.0, 120.0, 4579)
    monkeypatch.setattr(live.hek, "search_flares", lambda *a, **k: [fl])
    live.clear()
    e = live.collect(["flares"])["panels"]["flares"]["flares"]["events"][0]
    assert e["ar"] == 4579 and (e["x"], e["y"]) == (500.0, 120.0)


def test_geo_page(feeds):
    g = live.collect(["geo"])["panels"]["geo"]
    assert len(g["kp"]["kp"]) == 5                       # the 5-day-old sample is outside 3 days
    assert len(g["wind"]["speed"]["values"]) == 5        # only the active source
    assert g["wind"]["bz"]["values"][0] == -4.0
    assert [s["name"] for s in g["protons"]["series"]] == [">=10 MeV protons", ">=100 MeV protons"]


def test_endpoint(feeds):
    c = TestClient(app)
    r = c.get("/api/live?panels=now,geo")
    assert r.status_code == 200 and set(r.json()["panels"]) == {"now", "geo"}
    assert r.headers["cache-control"] == "no-store"
    assert c.get("/api/live?panels=bogus").status_code == 422
    assert set(c.get("/api/live").json()["panels"]) == {"now"}
