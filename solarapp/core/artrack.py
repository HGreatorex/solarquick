"""AR tracker extras (SPEC_LIVE_STATS Phase 3) for one NOAA region.

Added to the `regions` track spec by `extend`; each block fails alone as
{"error": ...} so a JSOC or NGDC outage never costs the rest of the track.

- flares:        the region's flares from the SWPC Edited Events (region number
                 as SWPC assigned it), each placed on the track at its peak.
- mag_timeline:  daily Hale (Mount Wilson) and McIntosh class; delta onset.
- sharp:         HMI SHARP summary parameters, hourly, from JSOC; definitive
                 series, topped up from near-real-time where definitive lags.
- cutouts:       a daily image pair centred on the region at each SRS instant.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from solarapp.core import httpclient, stats
from solarapp.core.helioviewer import screenshot_url
from solarapp.sources import solar_regions as sr

UTC = timezone.utc
JSOC = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info"
SHARP_DEF, SHARP_NRT = "hmi.sharp_cea_720s", "hmi.sharp_cea_720s_nrt"
SHARP_FIRST = date(2010, 5, 1)
SDO_FIRST = datetime(2010, 5, 13, tzinfo=UTC)
FAR_LON = 60.0          # |central-meridian distance| past which SHARPs are unreliable
CUTOUT_FOV = 400.0      # arcsec
CUTOUT_PX = 256
# key: (label, unit, log axis?)
SHARP_KEYS = {
    "USFLUX": ("Total unsigned flux", "Mx", True),
    "TOTUSJH": ("Total unsigned current helicity", "G² m⁻¹", True),
    "TOTPOT": ("Total photospheric free-energy density", "erg cm⁻¹", True),
    "R_VALUE": ("R: flux near the polarity inversion line", "log₁₀ Mx", False),
    "MEANSHR": ("Mean shear angle", "deg", False),
    "AREA_ACR": ("Area of active pixels", "μHem", False),
}
# TAI − UTC over the SDO era
_LEAPS = [(datetime(2017, 1, 1, tzinfo=UTC), 37), (datetime(2015, 7, 1, tzinfo=UTC), 36),
          (datetime(2012, 7, 1, tzinfo=UTC), 35), (datetime(2009, 1, 1, tzinfo=UTC), 34)]
_LAYERS = {   # Helioviewer layers for the cut-out pair, SDO era and before
    "sdo": [("AIA 171 Å", "[SDO,AIA,AIA,171,1,100]"), ("HMI magnetogram", "[SDO,HMI,HMI,magnetogram,1,100]")],
    "soho": [("EIT 195 Å", "[SOHO,EIT,EIT,195,1,100]"), ("MDI magnetogram", "[SOHO,MDI,MDI,magnetogram,1,100]")],
}


def _guard(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001 — one block's failure is that block's
        return {"error": str(e) or type(e).__name__}


def extend(spec: dict, ar: str, rows: list[dict], window: tuple[date, date] | None = None, *,
           fetch_json=None, catalogue=None) -> dict:
    """Add flares, mag_timeline, delta_onset, sharp and cutouts to a track spec.

    `window`: the summary days that were read. Flares and SHARPs cover all of it
    plus a day each side — a region's limb flares come after its last SRS row.
    """
    seen = sorted({date.fromisoformat(r["valid"]) for r in rows})
    lo, hi = window or (seen[0], seen[-1])
    days = [min(lo, seen[0]) - timedelta(days=1), max(hi, seen[-1]) + timedelta(days=1)]
    spec["mag_timeline"], spec["delta_onset"] = mag_timeline(rows)
    with ThreadPoolExecutor(2) as pool:
        fl = pool.submit(_guard, flare_history, ar, rows, days, catalogue or stats.catalogue)
        sh = pool.submit(_guard, sharp, ar, days, fetch_json or _jsoc)
        spec["flares"] = fl.result()
        s = sh.result()
    if s is not None:
        spec["sharp"] = s
    spec["cutouts"] = _guard(cutouts, rows)
    return spec


# -- magnetic class ------------------------------------------------------------


def mag_timeline(rows: list[dict]) -> tuple[list[dict], str | None]:
    spotted = sorted((r for r in rows if r["section"] == sr.SPOTS), key=lambda r: r["valid"])
    line = [{"valid": r["valid"], "mag": r["mag"], "mcintosh": r["mcintosh"],
             "delta": bool(r["delta"]), "area": r["area"], "spots": r["spots"]} for r in spotted]
    onset = next((r["valid"] for r in line if r["delta"]), None)
    return line, onset


# -- flares -----------------------------------------------------------------------


def _instant(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)


def position_at(rows: list[dict], t: datetime) -> tuple[float | None, float | None]:
    """Disk x, y (solar radii) of the region at `t`, rotated from the nearest SRS fix.

    None when the region is past the limb at `t`.
    """
    fixes = [r for r in rows if r["lat"] is not None and r["lon"] is not None]
    if not fixes:
        return None, None
    r = min(fixes, key=lambda r: abs((_instant(date.fromisoformat(r["valid"])) - t).total_seconds()))
    dt = (t - _instant(date.fromisoformat(r["valid"]))).total_seconds() / 86400
    lon = r["lon"] + sr.synodic_rate(r["lat"]) * dt
    if abs(lon) > 90:
        return None, None
    x, y = sr.project(r["lat"], lon, sr.b0_degrees(t))
    return round(x, 4), round(y, 4)


def flare_history(ar: str, rows: list[dict], days: list[date], catalogue=stats.catalogue) -> dict:
    start = max(days[0], stats.FIRST_DAY)
    end = min(days[-1], datetime.now(UTC).date() - timedelta(days=1))
    if end < start:
        raise ValueError("the region's dates are before the SWPC event archive (1996-07-31)")
    cat = catalogue(start, end)
    n = int(ar)
    evs = [e for e in cat.events if e.region == n]
    letters = {k: sum(1 for e in evs if e.cls[0] == k) for k in "BCMX"}
    out = []
    for e in evs:
        x, y = position_at(rows, e.peak)
        out.append({"peak": e.peak.isoformat(), "begin": e.begin.isoformat(),
                    "end": e.end.isoformat() if e.end else None, "class": e.cls,
                    "flux": e.flux, "sat": e.sat, "x": x, "y": y})
    largest = max(evs, key=lambda e: e.flux, default=None)
    notes = ["Flares SWPC assigned to this region in its Edited Events (GOES 1–8 Å, published "
             "classes); flares over the limb or not yet attributed are not counted."]
    if cat.realtime_days:
        notes.append(f"The last {len(cat.realtime_days)} day(s) come from the real-time list, "
                     "which carries no region numbers yet.")
    if cat.failed:
        notes.append(f"{len(cat.failed)} day(s) the archive did not serve.")
    return {"list": out, "counts": letters, "fi": round(stats.flare_index(evs), 1),
            "largest": largest.cls if largest else None,
            "largest_peak": largest.peak.isoformat() if largest else None,
            "start": start.isoformat(), "end": end.isoformat(), "note": " ".join(notes)}


# -- SHARP ----------------------------------------------------------------------------


def _jsoc(ds: str, keys: list[str]) -> dict:
    r = httpclient.session().get(JSOC, params={"op": "rs_list", "ds": ds, "key": ",".join(keys)},
                                 timeout=(5, 60))
    r.raise_for_status()
    j = r.json()
    if j.get("status") not in (0, None):
        raise RuntimeError(f"JSOC: {j.get('error') or j.get('status')}")
    return j


def _tai(s: str) -> datetime:
    t = datetime.strptime(s[:19], "%Y.%m.%d_%H:%M:%S").replace(tzinfo=UTC)
    return t - timedelta(seconds=next((n for since, n in _LEAPS if t >= since), 34))


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _qual(s: str) -> int:
    try:
        return int(s, 16) if s.startswith("0x") else int(float(s))
    except (TypeError, ValueError):
        return 0


def _query(series: str, noaa: int, t0: datetime, t1: datetime, fetch) -> list[dict]:
    fmt = "%Y.%m.%d_%H:%M"
    ds = (f"{series}[][{t0:{fmt}}_TAI-{t1:{fmt}}_TAI@1h]"
          f"[? NOAA_ARS like '%{noaa}%' ?]")
    keys = ["HARPNUM", "T_REC", "NOAA_ARS", "QUALITY", "LON_FWT", *SHARP_KEYS]
    j = fetch(ds, keys)
    cols = {k["name"]: k["values"] for k in j.get("keywords", [])}
    out = []
    for i in range(int(j.get("count") or 0)):
        ars = [a.strip() for a in cols["NOAA_ARS"][i].split(",")]
        if str(noaa) not in ars:          # "like" matched a substring of another number
            continue
        rec = {"harp": int(cols["HARPNUM"][i]), "t": _tai(cols["T_REC"][i]), "ars": ars,
               "quality": _qual(cols["QUALITY"][i]),
               "lon": _num(cols["LON_FWT"][i])}
        empty = not (_num(cols["USFLUX"][i]) or 0)     # no active pixels: at the limb, or lost
        for k in SHARP_KEYS:
            rec[k] = None if empty else _num(cols[k][i])
        out.append(rec)
    return out


def _main_harp(recs: list[dict]) -> tuple[list[dict], list[int]]:
    by: dict[int, list[dict]] = {}
    for r in recs:
        by.setdefault(r["harp"], []).append(r)
    if not by:
        return [], []
    main = max(by, key=lambda h: len(by[h]))
    return by[main], sorted(h for h in by if h != main)


def _far_intervals(times: list[datetime], lons: list[float | None]) -> list[list[str]]:
    out, run = [], None
    half = timedelta(minutes=30)
    for t, lon in zip(times, lons):
        far = lon is not None and abs(lon) > FAR_LON
        if far and run is None:
            run = [t - half, t + half]
        elif far:
            run[1] = t + half
        elif run is not None:
            out.append(run); run = None
    if run is not None:
        out.append(run)
    return [[a.isoformat(), b.isoformat()] for a, b in out]


def sharp(ar: str, days: list[date], fetch=_jsoc) -> dict | None:
    """Hourly SHARP parameters for NOAA 1`ar`; None before SDO."""
    if days[-1] < SHARP_FIRST:
        return None
    noaa = 10000 + int(ar)        # SRS prints four digits; numbering passed 10000 in 2002
    t0 = datetime.combine(max(days[0], SHARP_FIRST), datetime.min.time(), UTC)
    t1 = _instant(days[-1])
    definitive, others = _main_harp(_query(SHARP_DEF, noaa, t0, t1, fetch))
    recs, series, nrt_from, nrt_harp = list(definitive), "definitive", None, None
    last = max((r["t"] for r in definitive), default=None)
    if last is None or last < t1 - timedelta(days=1):
        # definitive SHARPs lag weeks; the near-real-time series has its own HARP numbers
        start = last + timedelta(minutes=30) if last else t0
        nrt, nrt_others = _main_harp(_query(SHARP_NRT, noaa, start, t1, fetch))
        if nrt:
            recs += nrt
            nrt_from, nrt_harp = nrt[0]["t"].isoformat(), nrt[0]["harp"]
            series = "definitive + near-real-time" if definitive else "near-real-time"
            others += nrt_others
    if not recs:
        raise ValueError(f"JSOC has no SHARP for NOAA {noaa} between {t0:%Y-%m-%d} and "
                         f"{t1:%Y-%m-%d} (not yet linked to a HARP, or never tracked)")
    recs.sort(key=lambda r: r["t"])
    times = [r["t"] for r in recs]
    lons = [r["lon"] for r in recs]
    shared = sorted({a for r in recs for a in r["ars"]} - {str(noaa)})
    flagged = sum(1 for r in recs if r["quality"])
    notes = [f"HMI SHARP ({SHARP_DEF}{', _nrt' if nrt_from else ''}), sampled hourly. Shaded: "
             f"flux-weighted centre more than {FAR_LON:.0f}° from central meridian, where "
             "projection makes the parameters unreliable."]
    if nrt_from:
        notes.append(f"From {nrt_from[:16].replace('T', ' ')} UT the values are near-real-time "
                     f"(HARP {nrt_harp}); definitive data replace them weeks later.")
    if shared:
        notes.append("This HARP also holds AR " + ", ".join(shared) + ": the values cover all of them.")
    if others:
        notes.append("Other HARPs carrying this region: " + ", ".join(map(str, others)) + ".")
    if flagged:
        notes.append(f"{flagged} of {len(recs)} records have a non-zero QUALITY flag (kept).")
    return {
        "harp": definitive[0]["harp"] if definitive else nrt_harp, "noaa": str(noaa),
        "series": series, "nrt_from": nrt_from, "shared_with": shared, "other_harps": others,
        "times": [t.isoformat() for t in times], "lon": lons,
        "quality": [r["quality"] for r in recs],
        "params": [{"key": k, "label": lab, "unit": unit, "log": log, "values": [r[k] for r in recs]}
                   for k, (lab, unit, log) in SHARP_KEYS.items()],
        "far": _far_intervals(times, lons), "far_lon": FAR_LON, "note": " ".join(notes),
    }


# -- cut-outs ------------------------------------------------------------------------------


def cutouts(rows: list[dict], fov: float = CUTOUT_FOV, px: int = CUTOUT_PX) -> dict:
    """One image pair per SRS day, centred where the summary puts the region (2400 UT)."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["valid"]):
        if r["valid"] in seen or r["x"] is None or r["behind"]:
            continue
        seen.add(r["valid"])
        t = _instant(date.fromisoformat(r["valid"]))
        cx, cy = round(r["x"] * sr.RSUN_ARCSEC, 1), round(r["y"] * sr.RSUN_ARCSEC, 1)
        layers = _LAYERS["sdo" if t >= SDO_FIRST else "soho"]
        out.append({"day": r["valid"], "time": t.isoformat(), "x": cx, "y": cy, "lon": r["lon"],
                    "images": [{"label": lab,
                                "url": screenshot_url(t, {"layer": layer, "scale": 2.4}, (cx, cy, fov), px),
                                "full_url": screenshot_url(t, {"layer": layer, "scale": 2.4}, (cx, cy, fov), 1024)}
                               for lab, layer in layers]})
    return {"fov": fov, "frames": out,
            "note": (f"{fov:.0f}″ square, centred on the Solar Region Summary position at 2400 UT "
                     "each day (Helioviewer renders; nearest image to that instant).")}
