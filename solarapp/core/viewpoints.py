"""Multi-viewpoint geometry (SPEC_LIVE_STATS Phase 4).

Where a flare sits for Earth, Solar Orbiter and STEREO-A: heliocentric angle
from each observer's sub-observer point, whether the site is on the disk, near
the limb, just behind it (loop tops can still show) or on the far side; and
what the STIX flare list says about flares in the window, with the GOES class
STIX estimates — the only class there is for a flare Earth cannot see.

The flare site arrives as helioprojective arcsec seen from Earth (HEK or the
region-summary estimate) and is inverted orthographically to Stonyhurst
lat/lon — parallax at 1 AU is < 0.3°, far inside a catalogue position's error.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from solarapp.core import httpclient
from solarapp.sources import solar_regions as sr

UTC = timezone.utc
AU_KM = 149_597_870.7
C_KM_S = 299_792.458
RSUN_KM = 695_700.0
RSUN_AU = RSUN_KM / AU_KM
RSUN_ARCSEC_1AU = 959.63
ON_DISK_MAX = 70.0    # heliocentric angle (deg) below which a site is plainly on the disk
BEHIND_MAX = 20.0     # degrees past the limb still called "behind the limb" (loop tops ≲ 45 Mm)
STIX_ERA = datetime(2020, 4, 14, tzinfo=UTC)
STIX_FLARES = "https://datacenter.stix.i4ds.net/api/request/flare-list"
HV_CLOSEST = "https://api.helioviewer.org/v2/getClosestImage/"
EARTH = ("Earth", "#00ffff")
# shown name → (JPL Horizons name, first day, colour, image companion (source, extras, Helioviewer id))
SPACECRAFT = {
    "Solar Orbiter": ("Solar Orbiter", datetime(2020, 2, 11, tzinfo=UTC), "#00ff00",
                      ("solo_eui", {"channel": "fsi_174"}, 84)),
    "STEREO-A": ("STEREO-A", datetime(2006, 10, 27, tzinfo=UTC), "#ff00ff",
                 ("stereo_euvi", {"channel": "euvi_195"}, 21)),
}


# -- observers ------------------------------------------------------------------


@lru_cache(maxsize=128)
def _body(name: str, slot: int) -> tuple[float, float, float]:
    """(Stonyhurst lon, lat, distance AU) at the 10-minute slot; cached per slot."""
    from astropy.time import Time
    from sunpy.coordinates import get_body_heliographic_stonyhurst, get_horizons_coord
    t = Time(datetime.fromtimestamp(slot * 600, UTC).replace(tzinfo=None))
    c = (get_body_heliographic_stonyhurst("earth", t) if name == EARTH[0]
         else get_horizons_coord(SPACECRAFT[name][0], t))
    return float(c.lon.deg), float(c.lat.deg), float(c.radius.au)


def observers(t: datetime, body=None) -> tuple[list[dict], list[str]]:
    """Earth plus every spacecraft flying at `t`, fetched together."""
    body = body or _body
    slot = int(t.timestamp() // 600)
    names = [EARTH] + [(n, v[2]) for n, v in SPACECRAFT.items() if t >= v[1]]
    out, notes = [], []
    with ThreadPoolExecutor(len(names)) as pool:
        futs = [(n, c, pool.submit(body, n, slot)) for n, c in names]
        for n, colour, fut in futs:
            try:
                lon, lat, r = fut.result()
            except Exception as e:  # noqa: BLE001 — that observer is left out, and said so
                notes.append(f"{n}: no ephemeris ({type(e).__name__})")
                continue
            out.append({"name": n, "lon": lon, "lat": lat, "r_au": r, "color": colour})
    return out, notes


# -- geometry ---------------------------------------------------------------------


def hgs_from_hpc(x: float, y: float, b0: float, r_au: float = 1.0) -> tuple[float, float, bool]:
    """Helioprojective arcsec from Earth → Stonyhurst (lat, lon), and whether the
    site was at or past the apparent limb (then put on it: its longitude is a bound)."""
    rapp = RSUN_ARCSEC_1AU / r_au
    X, Y = x / rapp, y / rapp
    rho = math.hypot(X, Y)
    beyond = rho >= 1.0
    if beyond:
        X, Y = X / rho, Y / rho
    Z = math.sqrt(max(0.0, 1 - X * X - Y * Y))
    b = math.radians(b0)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, Y * math.cos(b) + Z * math.sin(b)))))
    lon = math.degrees(math.atan2(X, Z * math.cos(b) - Y * math.sin(b)))
    return lat, lon, beyond


def _unit(lat: float, lon: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    return math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)


def classify(lat: float, lon: float, obs: dict) -> dict:
    """Heliocentric angle θ of the site from `obs`, and what that observer sees.

    The visible limb of an observer at distance D (solar radii) is θ_L = acos(1/D).
    A point a height h above a site at θ > θ_L clears the limb when
    1 + h/R > 1 / cos(θ − θ_L) (both tangents meet at the limb).
    """
    n, u = _unit(lat, lon), _unit(obs["lat"], obs["lon"])
    theta = math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(n, u))))))
    limb = math.degrees(math.acos(RSUN_AU / obs["r_au"]))
    past = theta - limb
    h = None
    if past <= 0:
        status = "on disk" if theta < ON_DISK_MAX else "near limb"
    else:
        status = "behind limb" if past < BEHIND_MAX else "far side"
        if past < 80:
            h = round(RSUN_KM / 1000 * (1 / math.cos(math.radians(past)) - 1), 1)
    return {"theta": round(theta, 1), "limb": round(limb, 2), "status": status, "visible_above_mm": h}


def light_lead_s(obs: dict, earth_r: float) -> float:
    """Seconds by which light from the Sun reaches `obs` before it reaches Earth."""
    return round((earth_r - obs["r_au"]) * AU_KM / C_KM_S, 1)


# -- what the spacecraft saw --------------------------------------------------------


def stix_flares(t0: datetime, t1: datetime, lead_s: float) -> list[dict]:
    """STIX Data Center flare list for an Earth-time window; its times are Solar
    Orbiter's own, so the query and every time returned are shifted by `lead_s`."""
    shift = timedelta(seconds=lead_s)
    r = httpclient.session().post(STIX_FLARES, data={
        "start_utc": f"{t0 - shift:%Y-%m-%dT%H:%M:%S}", "end_utc": f"{t1 - shift:%Y-%m-%dT%H:%M:%S}",
        "sort": "time", "limit": 50}, timeout=(5, 40))
    r.raise_for_status()
    out = []
    for f in r.json():
        at = lambda k: (datetime.fromisoformat(f[k]).replace(tzinfo=UTC) + shift).isoformat() if f.get(k) else None  # noqa: E731
        out.append({"id": f.get("flare_id"), "peak": at("peak_UTC"), "start": at("start_UTC"), "end": at("end_UTC"),
                    "peak_solo": f.get("peak_UTC"), "goes_seen": f.get("GOES_class"),
                    "est": f.get("goes_estimated_mean_class"), "est_min": f.get("goes_estimated_min_class"),
                    "est_max": f.get("goes_estimated_max_class"), "att_in": bool(f.get("att_in")),
                    "counts_4_10": f.get("LC0_PEAK_COUNTS_4S")})
    return out


def has_image(source_id: int, t: datetime, within: timedelta = timedelta(hours=1)) -> bool:
    """Does Helioviewer hold an image from this instrument within `within` of `t`?"""
    try:
        r = httpclient.session().get(HV_CLOSEST, params={"date": f"{t:%Y-%m-%dT%H:%M:%SZ}",
                                                         "sourceId": source_id}, timeout=15)
        r.raise_for_status()
        got = datetime.fromisoformat(r.json()["date"].replace(" ", "T")).replace(tzinfo=UTC)
        return abs(got - t) <= within
    except Exception:  # noqa: BLE001
        return False


def _site(hpc, hgs, earth) -> tuple[float, float, bool]:
    if hgs is not None:
        return float(hgs[0]), float(hgs[1]), False
    return hgs_from_hpc(float(hpc[0]), float(hpc[1]), earth["lat"], earth["r_au"])


def companions(t: datetime, *, hpc=None, hgs=None, body=None, image_check=has_image) -> list[tuple[str, dict]]:
    """Image sources to add to an event view: each spacecraft that can see the site
    (on the disk, near or just behind its limb) and has an image near the peak.
    The site is `hpc` (x″, y″ from Earth) or `hgs` (Stonyhurst lat, lon)."""
    if hpc is None and hgs is None:
        return []
    obs, _ = observers(t, body)
    earth = next((o for o in obs if o["name"] == EARTH[0]), None)
    if earth is None:
        return []
    lat, lon, _ = _site(hpc, hgs, earth)
    out = []
    for o in obs:
        if o["name"] not in SPACECRAFT or classify(lat, lon, o)["status"] == "far side":
            continue
        source, extras, hv_id = SPACECRAFT[o["name"]][3]
        if image_check(hv_id, t):
            out.append((source, dict(extras)))
    return out


def build(t: datetime, label: str, *, hpc=None, hgs=None, estimated: str | None = None,
          window: tuple[datetime, datetime] | None = None, body=None, stix=stix_flares) -> dict:
    """The VIEWPOINTS block. `estimated`: how the site was placed, when not catalogued."""
    obs, notes = observers(t, body)
    earth = next((o for o in obs if o["name"] == EARTH[0]), None)
    if earth is None:
        raise RuntimeError("no ephemeris for Earth at the flare time")
    placed = hpc is not None or hgs is not None
    lat, lon, beyond = _site(hpc, hgs, earth) if placed else (None, None, False)
    rows = [o | (classify(lat, lon, o) if placed else {"theta": None, "status": None, "visible_above_mm": None})
            | {"light_lead_s": light_lead_s(o, earth["r_au"])} for o in obs]
    where = (f"site {'N' if lat >= 0 else 'S'}{abs(lat):.0f} {'W' if lon >= 0 else 'E'}{abs(lon):.0f} (Stonyhurst)"
             if placed else "site unknown")
    if not placed:
        notes.append("The catalogue gives this flare no position or region, so where it sat for each "
                     "observer is unknown; positions and STIX are shown.")
    if beyond:
        notes.append("The catalogue puts the site at or past Earth's limb; it is placed on the limb, "
                     "so its longitude is a lower bound.")
    if estimated:
        notes.append(f"Site estimated: {estimated}.")
    spec = {
        "kind": "viewpoints",
        "title": f"VIEWPOINTS · {label} · {where}",
        "peak": t.isoformat(),
        "flare": {"label": label, "hpc": list(hpc) if hpc is not None else None,
                  "lat": round(lat, 2) if placed else None, "lon": round(lon, 2) if placed else None,
                  "at_limb": beyond, "estimated": estimated},
        "observers": rows,
        "provenance": "Earth: astropy ephemeris · spacecraft: JPL Horizons (Stonyhurst frame)",
    }
    solo = next((o for o in rows if o["name"] == "Solar Orbiter"), None)
    if solo is not None and t >= STIX_ERA:
        t0, t1 = window or (t - timedelta(hours=1), t + timedelta(hours=1))
        try:
            spec["stix"] = {"flares": stix(t0, t1, solo["light_lead_s"]), "lead_s": solo["light_lead_s"],
                            "note": (f"STIX Data Center flare list. Its times are Solar Orbiter's; shown "
                                     f"here shifted by {solo['light_lead_s']:+.0f} s to Earth. 'GOES seen' "
                                     "is what GOES recorded at that time; 'STIX estimate' is the GOES class "
                                     "inferred from STIX counts — the only class for a flare Earth cannot see.")}
        except Exception as e:  # noqa: BLE001
            spec["stix"] = {"error": str(e) or type(e).__name__}
        spec["provenance"] += " · STIX Data Center flare list (i4ds)"
    note = ("θ = angle of the site from each observer's disk centre. Behind the limb: footpoints "
            "hidden; emission above the given height clears the limb.")
    spec["note"] = " ".join([note, *notes])
    return spec
