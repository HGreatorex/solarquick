"""STATS: parsing SWPC daily products, the statistics, and the endpoints — offline."""
import json
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from solarapp.core import httpclient, stats
from solarapp.server.app import app

EVENTS = """:Product: 20250601events.txt
:Created: 2025 Jun 04 0357 UT
:Date: 2025 06 01
#Event    Begin    Max       End  Obs  Q  Type  Loc/Frq   Particulars       Reg#
#-------------------------------------------------------------------------------
7680 +     0000   ////      0002  SAG  C   RSP  036-180   CTM/1
7690 +     0012   0018      0021  G18  5   XRA  1-8A      C4.0    1.5E-03   4099
7690 +     0012   0018      0021  G19  5   XRA  1-8A      C4.1    1.5E-03   4099
7740 +     0419   0430      0437  G18  5   XRA  1-8A      M1.8    6.0E-04   4100
7740 +     0425   0425      0430  SVI  3   FLA  N10W08    SF                4100
7950       0404   0411      0419  G10  5   XRA  1-8A      C5.0    4.1E-03
8080 +     B2350  0005      0040  G12  5   XRA  1-8A      X17.            0486
8090 +     2355   2359      0010  G15  5   XRA  1-8A      X28+    2.3E00    0486
"""

RSGA = """:Product: 1231RSGA.txt
:Issued: 2025 Dec 31 2200 UTC
III.  Event probabilities 01 Jan-03 Jan
Class M    65/55/45
Class X    15/10/05
Proton     99/75/60
"""


def test_parse_events():
    ev = stats.parse_events(EVENTS, date(2025, 6, 1))
    by = {e.cls: e for e in ev}
    assert [e.cls for e in ev] == ["C4.0", "M1.8", "C5.0", "X17.0", "X28.0"]  # one per event number
    assert by["C4.0"].sat == 18 and by["C4.0"].region == 4099
    assert by["C5.0"].region is None and by["C5.0"].sat == 10
    x = by["X17.0"]       # "B2350" began before; peak after midnight → next day
    assert x.begin.hour == 23 and x.peak == datetime(2025, 6, 2, 0, 5, tzinfo=timezone.utc)
    assert x.region == 486                     # no integrated flux, region still read
    assert by["X28.0"].end.day == 2
    assert stats.class_flux("M1.8") == pytest.approx(1.8e-5)
    assert stats.flux_label(5e-5) == "M5.0" and stats.flux_label(3.2e-6) == "C3.2"


def test_rescale_only_old_satellites():
    ev = {e.cls: e for e in stats.parse_events(EVENTS, date(2025, 6, 1))}
    assert ev["C4.0"].flux_on(True) == ev["C4.0"].flux                  # GOES-18
    assert ev["C5.0"].flux_on(True) == pytest.approx(5e-6 / 0.7)        # GOES-10


def test_parse_rsga_reads_its_own_dates_across_new_year():
    rows = stats.parse_rsga(RSGA)
    assert rows[0] == (date(2025, 12, 31), date(2026, 1, 1), 1, 65, 15)
    assert rows[2][1:] == (date(2026, 1, 3), 3, 45, 5)
    old = RSGA.replace("Event probabilities", "EVENT PROBABILITIES").replace("Class", "CLASS").replace("Jan", "JAN")
    assert stats.parse_rsga(old) == rows
    assert stats.parse_rsga(":Issued: 2025 Dec 31 2200 UTC\nnothing") == []


def test_power_law_recovers_alpha():
    rng = np.random.default_rng(3)
    tail = stats._sample_power_law(2.0, 3e-6, 3000, rng)
    body = rng.uniform(1e-6, 3e-6, 1500)            # an incomplete body below x_min
    fit = stats.fit_power_law(np.concatenate([tail, body]))
    assert fit["alpha"] == pytest.approx(2.0, abs=0.08)
    assert 2e-6 < fit["xmin"] < 5e-6
    with pytest.raises(ValueError):
        stats.fit_power_law(tail[:10])


def test_verify_scores():
    o = np.array([1, 1, 0, 0, 1, 0, 0, 0, 1, 0])
    perfect = stats.verify(o.astype(float), o)
    assert perfect["brier"] == 0 and perfect["bss"] == 1 and perfect["auc"] == pytest.approx(1)
    clim = stats.verify(np.full(10, 0.4), o)
    assert clim["bss"] == pytest.approx(0) and clim["auc"] == pytest.approx(0.5)
    assert clim["brier"] == pytest.approx(0.24)
    # Murphy: BS = REL − RES + UNC
    p = np.array([.9, .7, .2, .1, .6, .3, .2, .1, .5, .4])
    v = stats.verify(p, o)
    assert v["brier"] == pytest.approx(v["reliability"] - v["resolution"] + v["uncertainty"])
    assert sum(b["n"] for b in v["bins"]) == 10


def _cat(events, days, covered=None):
    return stats.Catalogue(events, days, set(covered if covered is not None else days), [], [], [])


def _ev(day, hh, cls, sat=18, region=4100, rise=10):
    peak = datetime(day.year, day.month, day.day, hh, tzinfo=timezone.utc)
    return stats.Event(peak - timedelta(minutes=rise), peak, peak + timedelta(minutes=20), cls, sat, region)


def test_verification_alignment_and_persistence():
    days = [date(2025, 6, 1) + timedelta(days=i) for i in range(20)]
    flares = [_ev(d, 12, "M2.0") for d in days[::3]] + [_ev(days[1], 5, "C9.9")]
    probs = {(d, 1): (80 if d in days[::3] else 10, 1) for d in days}
    v = stats.verification(_cat(flares, days), probs, 1, "M")
    assert v["n"] == 20 and v["events"] == 7 and v["bss"] > 0.8
    assert v["persistence"]["n"] == 19
    with pytest.raises(ValueError):
        stats.verification(_cat(flares, days[:5]), probs, 1, "M")


def test_rates_and_regions():
    days = [date(2025, 1, 30) + timedelta(days=i) for i in range(4)]
    evs = [_ev(days[0], 1, "C1.0"), _ev(days[1], 2, "M1.0"), _ev(days[3], 3, "X2.0", region=4101),
           _ev(days[3], 4, "B5.0", region=None)]
    r = stats.rates(_cat(evs, days), 1e-6, False, [{"time-tag": "2025-01", "ssn": 0.0, "f10.7": -1}])
    assert r["months"] == ["2025-01", "2025-02"] and r["counts"]["C"] == [1, 0]
    assert r["counts"]["X"] == [0, 1] and "B" not in r["counts"]
    assert r["ssn"][0] == 0.0 and r["f107"][0] is None           # 0 is a value, −1 is not
    g = stats.regions(_cat(evs, days), None)
    assert [t["region"] for t in g["table"]] == [4101, 4100] and g["table"][0]["fi"] == 200
    assert g["unassigned"] == 1


def test_region_day_rates_count_up_through_classes():
    from solarapp.sources import solar_regions as sr
    d = date(2025, 6, 2)
    regs = [sr.Region("4100", sr.SPOTS, d, mcintosh="Ekc", mag="Beta-Gamma-Delta"),
            sr.Region("4101", sr.SPOTS, d, mcintosh="Axx", mag="Alpha")]
    evs = [_ev(d, 3, "X1.0", region=4100), _ev(d, 5, "M1.0", region=4100)]
    res = stats.region_day_rates(_cat(evs, [d]), {d: regs})
    z = {g["group"]: g for g in res["zurich"]}
    assert z["E"]["C"]["rate"] == 2 and z["E"]["M"]["rate"] == 2 and z["E"]["X"]["rate"] == 1
    assert z["A"]["M"]["rate"] == 0
    assert [g["group"] for g in res["mag"]] == ["alpha", "delta"]


def test_daily_cache_and_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "CACHE_DIR", tmp_path)
    calls = []

    def fake(url):
        calls.append(url)
        return None if "20200101" in url else EVENTS
    monkeypatch.setattr(stats, "_get_text", fake)
    today = date(2025, 6, 30)
    assert stats.daily_text("events", date(2025, 6, 1), today) == EVENTS
    stats.daily_text("events", date(2025, 6, 1), today)
    assert len(calls) == 1                                   # settled: from disk
    assert stats.daily_text("events", date(2020, 1, 1), today) is None
    stats.daily_text("events", date(2020, 1, 1), today)
    assert len(calls) == 2                                   # an old 404 is remembered
    url = stats.URLS["srs"].format(d=date(2025, 6, 2))
    stats.daily_text("srs", date(2025, 6, 1), today)          # SRS valid D is issued D+1
    assert calls[-1] == url


def test_archive_failure_is_not_a_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(stats.time, "sleep", lambda s: None)

    def boom(url):
        raise ValueError("HTML courtesy page")
    monkeypatch.setattr(stats, "_get_text", boom)
    texts, absent, failed = stats.load_days("events", [date(2025, 6, 1)])
    assert failed == [date(2025, 6, 1)] and not absent


def test_epoch_with_fake_curves():
    days = [date(2025, 6, 1)]
    a, b = _ev(days[0], 6, "M2.0"), _ev(days[0], 12, "M5.0")
    near = _ev(days[0], 13, "M3.0")                         # inside b's window, > half its flux

    def fetch(source, t0, t1, extras):
        t = [t0 + timedelta(minutes=i) for i in range(int((t1 - t0).total_seconds() // 60))]
        def f(x):
            pk = min((a.peak, b.peak, near.peak), key=lambda p: abs((x - p).total_seconds()))
            amp = {a.peak: 2e-5, b.peak: 5e-5, near.peak: 3e-5}[pk]
            dt = (x - pk).total_seconds() / 60
            return 1e-6 + amp * (math.exp(-dt / 15) if dt >= 0 else math.exp(dt / 5))
        return {"series": [{"times": [x.isoformat() for x in t], "values": [f(x) / 3 for x in t]},
                           {"times": [x.isoformat() for x in t], "values": [f(x) for x in t]}]}

    out = stats.epoch(_cat([a, b, near], days), class_min="M1.0", before_min=90, after_min=60,
                      fetch=fetch)
    assert out["overlaps_dropped"] == 2 and out["used"] == 1   # b and near clash; a is clean
    assert out["flares"][0]["class"] == "M2.0"
    med = out["stats"]["bgsub"]["median"]
    i0 = out["minutes"].index(0)
    assert med[i0] == pytest.approx(1, abs=0.02)
    assert out["flares"][0]["decay_efold_min"] == pytest.approx(15, abs=1.5)
    json.dumps(out, allow_nan=False)
    with pytest.raises(ValueError):
        stats.epoch(_cat([a], days), channel="nope", fetch=fetch)


def test_endpoints_validate_and_stream(monkeypatch):
    c = TestClient(app)
    assert c.post("/api/stats/stream", json={"start": "2025-01-02", "end": "2025-01-01"}).status_code == 422
    assert c.post("/api/stats/stream", json={"start": "1990-01-01", "end": "2025-01-01"}).status_code == 422
    assert c.post("/api/stats/stream", json={"start": "2025-01-01", "end": "2025-01-02", "floor": "Q1"}).status_code == 422

    def fake_run(progress=None, **kw):
        progress("events", 1, 1)
        return {"panels": {}, "kw": sorted(kw)}
    monkeypatch.setattr(stats, "run", fake_run)
    r = c.post("/api/stats/stream", json={"start": "2025-01-01", "end": "2025-01-02"})
    lines = [json.loads(x) for x in r.text.strip().split("\n")]
    assert lines[0]["event"] == "progress" and lines[-1]["event"] == "result"
    assert "floor_cls" in lines[-1]["kw"]
