"""LIVE mode: the current space-weather picture from NOAA SWPC's open feeds.

A view, not a DataSource: it has no time window and never goes through the
parser. `collect(panels)` builds whole pages (`now`, `regions`, `flares`, `geo`)
for `GET /api/live`; the browser polls it. Each page is a dict of independent
blocks, each of which can fail alone (`{"error": ...}`), so one dead feed greys
one block, never the page.

Feed TTLs (below) sit under the browser's poll interval on purpose: the poll
decides how fresh the page is, the TTL only stops two polls in one minute (or
two open tabs) from hitting NOAA twice.

Positions: SWPC's solar_regions.json carries locations about a day *ahead* of
the Solar Region Summary text (checked 2026-11-25: AR 4579 N15W76 in the JSON,
N15W62 valid at 24/2400Z in the SRS). So positions come from the SRS, the file
the rest of the app already validates, rotated to now; the JSON supplies
everything else (area, class, flare counts, per-region flare probabilities).
"""
from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from solarapp.core import hek, httpclient
from solarapp.sources import solar_regions as sr
from solarapp.sources.goes_xrs import FLARE_BANDS

BASE = "https://services.swpc.noaa.gov"
HV = "https://api.helioviewer.org/v2"

# key: (path, ttl seconds)
FEEDS: dict[str, tuple[str, float]] = {
    "scales": ("/products/noaa-scales.json", 120),
    "xray6h": ("/json/goes/primary/xrays-6-hour.json", 60),
    "flare_latest": ("/json/goes/primary/xray-flares-latest.json", 60),
    "flares7d": ("/json/goes/primary/xray-flares-7-day.json", 300),
    "alerts": ("/products/alerts.json", 120),
    "regions": ("/json/solar_regions.json", 900),
    "probs": ("/json/solar_probabilities.json", 3600),
    "kp": ("/products/noaa-planetary-k-index.json", 300),
    "protons": ("/json/goes/primary/integral-protons-3-day.json", 300),
    "wind": ("/json/rtsw/rtsw_wind_1m.json", 300),
    "mag": ("/json/rtsw/rtsw_mag_1m.json", 300),
}
ALERT_HOURS = 24
MAX_POINTS = 400

_memo_store: dict[str, tuple[float, Any]] = {}
_memo_lock = threading.Lock()


def _memo(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """Cache a derived result (SRS text, HEK lookup). Failures are not cached."""
    now = time.monotonic()
    with _memo_lock:
        hit = _memo_store.get(key)
        if hit is not None and now < hit[0]:
            return hit[1]
    value = fn()
    with _memo_lock:
        _memo_store[key] = (now + ttl, value)
    return value


def clear() -> None:
    with _memo_lock:
        _memo_store.clear()


def _feed(key: str) -> Any:
    path, ttl = FEEDS[key]
    try:
        return httpclient.get_json_cached(BASE + path, ttl=ttl)
    except Exception as e:  # a 404 page is not JSON; say which feed, not "Expecting value"
        raise RuntimeError(f"{path.rsplit('/', 1)[-1]}: {type(e).__name__}") from e


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as e:
        return {"error": str(e) or type(e).__name__}


def _t(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _stride(rows: list, limit: int = MAX_POINTS) -> list:
    step = math.ceil(len(rows) / limit) if len(rows) > limit else 1
    return rows[::step]


def flux_class(flux: float | None) -> str | None:
    """GOES class string for a long-channel flux (W m⁻²): 2.3e-6 → "C2.3"."""
    if not flux or flux <= 0:
        return None
    for letter, lo in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7)):
        if flux >= lo:
            return f"{letter}{flux / lo:.1f}"
    return f"A{flux / 1e-8:.1f}"


# -- page: now ------------------------------------------------------------


def _scales() -> dict:
    raw = _feed("scales")
    out = {}
    for key, v in raw.items():
        r, s, g = v.get("R") or {}, v.get("S") or {}, v.get("G") or {}
        out[key] = {
            "date": v.get("DateStamp"), "time": v.get("TimeStamp"),
            "R": {"scale": r.get("Scale"), "text": r.get("Text"),
                  "minor": r.get("MinorProb"), "major": r.get("MajorProb")},
            "S": {"scale": s.get("Scale"), "text": s.get("Text"), "prob": s.get("Prob")},
            "G": {"scale": g.get("Scale"), "text": g.get("Text")},
        }
    return out  # "-1" past 24 h max · "0" now · "1".."3" forecast days


def _xray() -> dict:
    rows = _feed("xray6h")
    ch = {"0.05-0.4nm": ([], []), "0.1-0.8nm": ([], [])}
    for r in rows:
        if r["energy"] in ch and (r.get("flux") or 0) > 0:
            ch[r["energy"]][0].append(_t(r["time_tag"]).isoformat())
            ch[r["energy"]][1].append(r["flux"])
    if not ch["0.1-0.8nm"][0]:
        raise RuntimeError("no long-channel samples in the feed")
    long_t, long_v = ch["0.1-0.8nm"]
    sat = next((r.get("satellite") for r in reversed(rows)), None)
    return {
        "spec": {
            "title": f"GOES{'-' + str(sat) if sat else ''} XRS  last 6 h (SWPC realtime)",
            "series": [
                {"name": "XRS-A 0.5–4 Å", "times": ch["0.05-0.4nm"][0], "values": ch["0.05-0.4nm"][1]},
                {"name": "XRS-B 1–8 Å", "times": long_t, "values": long_v},
            ],
            "yaxis": {"label": "Flux (W m⁻²)", "type": "log", "range": [1e-9, 1e-3]},
            "xaxis": {"label": "Time (UT)"}, "bands": FLARE_BANDS,
        },
        "latest": {"time": long_t[-1], "flux": long_v[-1], "class": flux_class(long_v[-1]),
                   "satellite": sat},
    }


def _flare_latest() -> dict:
    rows = _feed("flare_latest")
    if not rows:
        return {"event": None}
    r = rows[-1]
    end = r.get("end_time")
    return {"event": {
        "begin": r.get("begin_time"), "begin_class": r.get("begin_class"),
        "max": r.get("max_time"), "max_class": r.get("max_class"),
        "end": end if end and end != "Unk" else None, "end_class": r.get("end_class"),
        "current_class": r.get("current_class"),
        # an event with no end time is one the satellite has not seen finish
        "in_progress": not end or end == "Unk",
    }}


_HEADS = ("EXTENDED WARNING", "ALERT", "WARNING", "WATCH", "SUMMARY", "CONTINUED ALERT")


def _headline(message: str) -> str:
    for line in message.replace("\r", "").split("\n"):
        s = line.strip()
        if s.upper().startswith(_HEADS):
            return s
    lines = [x.strip() for x in message.replace("\r", "").split("\n") if x.strip()]
    return lines[-1] if lines else ""


def _alerts() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ALERT_HOURS)
    out = []
    for r in _feed("alerts"):
        issued = _t(r["issue_datetime"])
        if issued < cutoff:
            continue
        out.append({"id": f"{r['product_id']}@{r['issue_datetime']}", "product": r["product_id"],
                    "issued": issued.isoformat(), "headline": _headline(r["message"]),
                    "message": r["message"].replace("\r", "")[:1500]})
    out.sort(key=lambda a: a["issued"], reverse=True)
    return {"alerts": out}


def build_now() -> dict:
    return {"scales": _safe(_scales), "xray": _safe(_xray),
            "flare_latest": _safe(_flare_latest), "alerts": _safe(_alerts)}


# -- page: regions --------------------------------------------------------

_MAG = {"A": "Alpha", "B": "Beta", "BG": "Beta-Gamma", "BD": "Beta-Delta",
        "BGD": "Beta-Gamma-Delta", "G": "Gamma", "GD": "Gamma-Delta", "D": "Delta"}


def _srs_positions(now: datetime) -> dict[int, tuple[float, float, str]]:
    """{AR: (lat, lon_now, nmbr)} from the newest SRS, rotated to `now`."""
    snap = _memo("srs", 900, sr._load_live)
    if snap is None:
        return {}
    valid = datetime(snap.valid.year, snap.valid.month, snap.valid.day,
                     tzinfo=timezone.utc) + timedelta(days=1)  # "valid at 2400Z"
    out = {}
    for r in snap.section(sr.SPOTS) + snap.section(sr.PLAGE):
        if r.lat is None or r.lon is None:
            continue
        lon = r.lon + sr.synodic_rate(r.lat) * (now - valid).total_seconds() / 86400
        out[sr._ar_number(r.nmbr)] = (r.lat, lon, r.nmbr)
    return out


def _regions() -> dict:
    now = datetime.now(timezone.utc)
    rows = _feed("regions")
    latest = max(r["observed_date"] for r in rows)
    mine = [r for r in rows if r["observed_date"] == latest and r.get("region") is not None]
    try:
        srs, source = _srs_positions(now), "Solar Region Summary rotated to now"
    except Exception:
        srs, source = {}, None
    b0 = sr.b0_degrees(now)
    day = now.date()
    out = []
    for r in mine:
        ar = r["region"] % 10000
        pos = srs.get(ar)
        approx = pos is None
        lat, lon = (pos[0], pos[1]) if pos else (r.get("latitude"), r.get("longitude"))
        x = y = None
        behind = False
        if lat is not None and lon is not None:
            if approx and r.get("location"):  # JSON sign convention differs; trust the string
                parsed = sr._parse_location(r["location"])
                lat, lon = parsed if parsed else (lat, lon)
            x, y = sr.project(lat, lon, b0)
            behind = abs(lon) > 90
            if behind:
                radius = math.hypot(x, y) or 1.0
                x, y = round(x / radius, 4), round(y / radius, 4)
        mag = _MAG.get(r.get("mag_class") or "", None)
        location = r.get("location")
        if not approx:  # the position shown is the rotated one, so the label must be too
            location = (f"{'N' if lat >= 0 else 'S'}{abs(round(lat)):02d}"
                        f"{'W' if lon >= 0 else 'E'}{abs(round(lon)):02d}")
        reg = sr.Region(nmbr=str(r["region"]), section=sr.SPOTS, valid=day, mcintosh=r.get("spot_class"),
                        mag=mag, location=location, lat=lat, lon=lon,
                        carrington=r.get("carrington_longitude"), area=r.get("area"),
                        spots=r.get("number_spots"))
        out.append({
            "nmbr": reg.nmbr, "section": sr.SPOTS, "valid": day.isoformat(),
            "location": location, "lat": lat, "lon": None if lon is None else round(lon, 1),
            "x": x, "y": y, "behind": behind, "approx": approx,
            "carrington": reg.carrington, "area": reg.area, "mcintosh": reg.mcintosh,
            "spots": reg.spots, "mag": mag, "delta": reg.delta, "complexity": reg.complexity,
            "events": {k: r.get(f"{k}_xray_events") or 0 for k in "cmx"},
            "prob": {k: r.get(f"{k}_flare_probability") for k in "cmx"},
            "query": f"ar {reg.nmbr} from {day - timedelta(days=6):%Y-%m-%d} to {day:%Y-%m-%d}",
        })
    out.sort(key=lambda g: tuple(-(g["prob"][k] or 0) for k in "xmc") + (-(g["area"] or 0),))
    return {
        "observed": latest, "regions": out, "position_source": source,
        "disk_image": {
            "url": (f"{HV}/takeScreenshot/?date={now:%Y-%m-%dT%H:%M:%SZ}&imageScale=2.25"
                    "&layers=[SDO,AIA,AIA,171,1,100]&x0=0&y0=0&width=1024&height=1024"
                    "&display=true&watermark=false"),
            "half": 1.2, "time": now.isoformat(),  # 2.25″/px × 1024 = ±1.2 R☉ at 960″
        },
    }


def _disk_probs() -> dict:
    rows = sorted(_feed("probs"), key=lambda r: r["date"], reverse=True)
    r = rows[0]
    g = lambda p: [r.get(f"{p}_{d}_day") for d in (1, 2, 3)]  # noqa: E731
    return {"date": r["date"], "c": g("c_class"), "m": g("m_class"), "x": g("x_class"),
            "protons": g("10mev_protons"), "pca": r.get("polar_cap_absorption")}


def build_regions() -> dict:
    return {"regions": _safe(_regions), "probs": _safe(_disk_probs)}


# -- page: flares ---------------------------------------------------------


_hek_backoff_until = 0.0


def _hek_week(start: datetime, end: datetime) -> tuple[list[hek.Flare], str | None]:
    """HEK's SWPC list for AR numbers and sites. Slow and sometimes down, so a
    failure is reported, backed off for a minute, and never blocks the feed."""
    global _hek_backoff_until
    if time.monotonic() < _hek_backoff_until:
        return [], "HEK unavailable (retrying shortly)"
    try:
        return _memo("hek_week", 300,
                     lambda: hek.search_flares(start, end, min_class="C1.0")), None
    except Exception as e:
        _hek_backoff_until = time.monotonic() + 60
        return [], f"HEK: {type(e).__name__}: {e}"[:160]


def _flares() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    feed = []
    for r in _feed("flares7d"):
        if not r.get("max_time") or _t(r["max_time"]) < cutoff:
            continue
        feed.append(r)
    feed.sort(key=lambda r: r["max_time"], reverse=True)
    now = datetime.now(timezone.utc)
    hek_list, ar_error = _hek_week(now - timedelta(days=7), now)
    try:
        placed = sr.flare_sites_from_regions(hek_list)
    except Exception:
        placed = {}

    def match(peak: datetime):
        best = None
        for i, f in enumerate(hek_list):
            d = abs((f.peak - peak).total_seconds())
            if d <= 360 and (best is None or d < best[0]):
                best = (d, i, f)
        return best

    events = []
    for r in feed:
        peak = _t(r["max_time"])
        x = y = ar = src = None
        m = match(peak)
        if m:
            _, i, f = m
            ar = f.ar or None
            if f.has_position:
                x, y = round(f.hpc_x, 1), round(f.hpc_y, 1)
            elif i in placed:
                x, y, src = placed[i]
        end = r.get("end_time")
        events.append({
            "class": r.get("max_class") or "?", "start": r.get("begin_time"), "peak": peak.isoformat(),
            "end": end if end and end != "Unk" else None, "begin_class": r.get("begin_class"),
            "end_class": r.get("end_class"), "satellite": r.get("satellite"),
            "x": x, "y": y, "ar": ar, "position_from": src,
            "query": f"flare at {peak:%Y-%m-%d %H:%M}",
        })
    return {"events": events, "ar_error": ar_error}


def build_flares() -> dict:
    return {"flares": _safe(_flares)}


# -- page: geo ------------------------------------------------------------


def _kp() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    rows = [{"time": _t(r["time_tag"]).isoformat(), "kp": r["Kp"]}
            for r in _feed("kp") if _t(r["time_tag"]) >= cutoff]
    return {"kp": rows}


def _wind() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

    def pick(rows):
        rows = [r for r in rows if _t(r["time_tag"]) >= cutoff]
        active = [r for r in rows if r.get("active")]
        return sorted(active or rows, key=lambda r: r["time_tag"])

    plasma, mag = pick(_feed("wind")), pick(_feed("mag"))
    plasma = _stride([r for r in plasma if r.get("proton_speed") is not None])
    mag = _stride([r for r in mag if r.get("bz_gsm") is not None])
    return {
        "speed": {"times": [_t(r["time_tag"]).isoformat() for r in plasma],
                  "values": [r["proton_speed"] for r in plasma]},
        "density": {"times": [_t(r["time_tag"]).isoformat() for r in plasma],
                    "values": [r["proton_density"] for r in plasma]},
        "bz": {"times": [_t(r["time_tag"]).isoformat() for r in mag],
               "values": [r["bz_gsm"] for r in mag]},
        "bt": {"times": [_t(r["time_tag"]).isoformat() for r in mag],
               "values": [r["bt"] for r in mag]},
    }


def _protons() -> dict:
    series = {}
    for r in _feed("protons"):
        if r["energy"] in (">=10 MeV", ">=100 MeV") and (r.get("flux") or 0) > 0:
            t, v = series.setdefault(r["energy"], ([], []))
            t.append(_t(r["time_tag"]).isoformat())
            v.append(r["flux"])
    if ">=10 MeV" not in series:
        raise RuntimeError("no >=10 MeV samples in the feed")
    return {"series": [{"name": f"{e} protons", "times": t, "values": v}
                       for e, (t, v) in sorted(series.items())],
            "s1_threshold": 10}


def build_geo() -> dict:
    return {"kp": _safe(_kp), "wind": _safe(_wind), "protons": _safe(_protons)}


# -- the endpoint's one entry point ----------------------------------------

PANELS: dict[str, Callable[[], dict]] = {
    "now": build_now, "regions": build_regions, "flares": build_flares, "geo": build_geo,
}


def collect(panels: list[str]) -> dict:
    unknown = [p for p in panels if p not in PANELS]
    if unknown:
        raise ValueError(f"unknown panel(s): {', '.join(unknown)}")
    wanted = list(dict.fromkeys(panels)) or ["now"]
    with ThreadPoolExecutor(len(wanted)) as pool:
        futures = {p: pool.submit(PANELS[p]) for p in wanted}
        result = {p: _safe(f.result) for p, f in futures.items()}
    return {"server_time": _iso(datetime.now(timezone.utc)), "panels": result}
