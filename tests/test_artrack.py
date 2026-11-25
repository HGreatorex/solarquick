"""AR tracker extras (Phase 3): flare history, mag timeline, SHARP, cut-outs — offline."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from solarapp.core import artrack, stats
from solarapp.sources import solar_regions as sr

UTC = timezone.utc


def _rows(start=date(2017, 9, 4), lons=(16, 30, 45), lat=-9.0, mags=("Beta-Gamma", "Beta-Gamma-Delta", "Beta-Gamma-Delta")):
    out = []
    for i, (lon, mag) in enumerate(zip(lons, mags)):
        d = start + timedelta(days=i)
        reg = sr.Region("2673", sr.SPOTS, d, location="S09W16", lat=lat, lon=float(lon),
                        area=600 + 100 * i, mcintosh="Ekc", spots=20 + i, mag=mag)
        out.append(sr._row(reg, d))
    return out


def test_mag_timeline_and_delta_onset():
    line, onset = artrack.mag_timeline(_rows())
    assert [r["mag"] for r in line] == ["Beta-Gamma", "Beta-Gamma-Delta", "Beta-Gamma-Delta"]
    assert onset == "2017-09-05" and line[1]["delta"] and not line[0]["delta"]


def test_position_rotates_west_and_is_none_behind_the_limb():
    rows = _rows()
    x0, _ = artrack.position_at(rows, datetime(2017, 9, 5, 0, tzinfo=UTC))    # the first fix itself
    x1, _ = artrack.position_at(rows, datetime(2017, 9, 5, 12, tzinfo=UTC))
    assert x0 == pytest.approx(rows[0]["x"], abs=1e-3) and x1 > x0
    assert artrack.position_at(rows, datetime(2017, 9, 12, tzinfo=UTC)) == (None, None)   # 45° + 4 d


def test_flare_history_keeps_the_regions_flares_and_places_them():
    def ev(day, hh, cls, region):
        pk = datetime(2017, 9, day, hh, tzinfo=UTC)
        return stats.Event(pk - timedelta(minutes=10), pk, pk + timedelta(minutes=30), cls, 15, region)
    evs = [ev(4, 20, "M5.5", 2673), ev(6, 12, "X9.3", 2673), ev(6, 9, "X2.2", 2673),
           ev(5, 1, "C3.0", 2674), ev(5, 2, "C1.0", None)]
    days = [date(2017, 9, 4) + timedelta(days=i) for i in range(3)]
    cat = stats.Catalogue(evs, days, set(days), [], [], [])
    got = []
    fh = artrack.flare_history("2673", _rows(), [days[0] - timedelta(days=1), days[-1]],
                               lambda a, b: got.append((a, b)) or cat)
    assert got == [(date(2017, 9, 3), date(2017, 9, 6))]
    assert fh["counts"] == {"B": 0, "C": 0, "M": 1, "X": 2} and fh["largest"] == "X9.3"
    assert fh["fi"] == pytest.approx(100 * (9.3 + 2.2) + 10 * 5.5)
    assert all(f["x"] is not None for f in fh["list"])


def _jsoc_reply(rows):
    keys = ["HARPNUM", "T_REC", "NOAA_ARS", "QUALITY", "LON_FWT", *artrack.SHARP_KEYS]
    cols = {k: [] for k in keys}
    for r in rows:
        for k in keys:
            cols[k].append(str(r.get(k, "1.0")))
    return {"keywords": [{"name": k, "values": v} for k, v in cols.items()], "count": len(rows), "status": 0}


def _rec(harp, t, lon, ars="12673", usflux="3e22", q="0x00000000"):
    return {"HARPNUM": harp, "T_REC": f"{t:%Y.%m.%d_%H:%M:%S}_TAI", "NOAA_ARS": ars, "QUALITY": q,
            "LON_FWT": lon, "USFLUX": usflux}


def test_sharp_definitive_then_near_real_time():
    days = [date(2017, 9, 4) + timedelta(days=i) for i in range(3)]
    t0 = datetime(2017, 9, 3, tzinfo=UTC)
    calls = []

    def fetch(ds, keys):
        calls.append(ds)
        if ds.startswith("hmi.sharp_cea_720s_nrt"):
            return _jsoc_reply([_rec(9001, t0 + timedelta(hours=h), 50 + h, q="0x00000400") for h in (40, 41, 42)])
        recs = [_rec(7115, t0 + timedelta(hours=h), -70 + 3 * h) for h in range(0, 30)]
        recs[5]["USFLUX"] = "0.000000e+00"                    # no active pixels: no values
        recs += [_rec(7116, t0, 0, ars="12674,12673"), _rec(7117, t0, 0, ars="126730")]
        return _jsoc_reply(recs)

    s = artrack.sharp("2673", days, fetch)
    assert "[? NOAA_ARS like '%12673%' ?]" in calls[0] and "@1h" in calls[0]
    assert calls[1].startswith("hmi.sharp_cea_720s_nrt")
    assert s["harp"] == 7115 and s["series"] == "definitive + near-real-time"
    assert s["other_harps"] == [7116] and s["shared_with"] == []
    assert "126730" not in json.dumps(s)                     # substring match dropped
    assert s["times"][0] == "2017-09-02T23:59:23+00:00"      # TAI − 37 s
    usf = next(p for p in s["params"] if p["key"] == "USFLUX")["values"]
    assert usf[5] is None and usf[0] == 3e22
    assert s["far"][0][0] == "2017-09-02T23:29:23+00:00"      # |lon| > 60 at the start
    assert sum(1 for q in s["quality"] if q) == 3 and "3 of" in s["note"]
    json.dumps(s, allow_nan=False)


def test_sharp_is_absent_before_sdo_and_says_when_jsoc_has_nothing():
    assert artrack.sharp("0486", [date(2003, 10, 28)], lambda ds, k: pytest.fail("no query")) is None
    with pytest.raises(ValueError, match="no SHARP"):
        artrack.sharp("4561", [date(2026, 11, 20)], lambda ds, k: _jsoc_reply([]))


def test_cutouts_follow_the_region_and_pick_the_era():
    c = artrack.cutouts(_rows())
    f = c["frames"]
    assert [x["day"] for x in f] == ["2017-09-04", "2017-09-05", "2017-09-06"]
    assert "AIA,AIA,171" in f[0]["images"][0]["url"] and "HMI,HMI,magnetogram" in f[0]["images"][1]["url"]
    assert f"x0={f[0]['x']:.1f}" in f[0]["images"][0]["url"] and f"y0={-f[0]['y']:.1f}" in f[0]["images"][0]["url"]
    assert "width=256" in f[0]["images"][0]["url"] and "width=1024" in f[0]["images"][0]["full_url"]
    old = artrack.cutouts(_rows(start=date(2003, 10, 26)))
    assert "EIT,EIT,195" in old["frames"][0]["images"][0]["url"]
    behind = _rows(lons=(80, 95, 100))
    assert len(artrack.cutouts(behind)["frames"]) == 1


def test_extend_isolates_a_failing_block():
    spec = {}
    def boom(*a, **k):
        raise RuntimeError("NGDC down")
    artrack.extend(spec, "2673", _rows(), catalogue=boom,
                   fetch_json=lambda ds, k: _jsoc_reply([_rec(7115, datetime(2017, 9, 4, tzinfo=UTC), 10)]))
    assert spec["flares"] == {"error": "NGDC down"}
    assert spec["sharp"]["harp"] == 7115 and spec["delta_onset"] == "2017-09-05"
    assert len(spec["cutouts"]["frames"]) == 3
    json.dumps(spec, allow_nan=False)
