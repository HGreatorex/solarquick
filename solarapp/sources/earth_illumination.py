"""Earth illumination: day/night terminator map with twilight bands.

Everything is computed from the subsolar point (astropy: solar RA/Dec and
Greenwich apparent sidereal time — good to ~0.1°, plenty for a map):

- the terminator (solar zenith angle 90°) and the civil / nautical /
  astronomical twilight boundaries (96° / 102° / 108°) via the standard
  sunrise equation, drawn as cumulative shading bands along parallels;
- the subsolar point (Sun overhead); for multi-day queries, its track.

The map itself (coastlines, land/ocean) is Plotly's built-in world
geography — this plugin only ships geometry.

Query examples: "terminator today", "earth illumination 2026-06-21",
"day night map from 2026-06-01 to 2026-07-01".
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from solarapp.core.registry import DataSource, PlotSpec

# zenith angle → cumulative shading band (label, extra opacity)
TWILIGHT_LEVELS = [
    (90.0, "night (SZA > 90°)", 0.18),
    (96.0, "civil twilight ends", 0.16),
    (102.0, "nautical twilight ends", 0.16),
    (108.0, "astronomical twilight ends", 0.16),
]
LAT_STEP = 2.0   # shading band spacing (deg)
LON_STEP = 5.0   # sampling along each band (deg)
MAX_TRACK = 25   # subsolar-track samples for range queries


def subsolar_point(dt: datetime) -> tuple[float, float]:
    """(lat, lon) of the point where the Sun is overhead, in degrees."""
    from astropy.coordinates import get_sun
    from astropy.time import Time

    t = Time(dt.replace(tzinfo=None))
    s = get_sun(t)
    gast = t.sidereal_time("apparent", "greenwich").deg
    lat = s.dec.deg
    lon = ((s.ra.deg - gast + 180.0) % 360.0) - 180.0
    return lat, lon


def _wrap(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def night_band(dec: float, lon_ss: float, zenith: float,
               lat_step: float = LAT_STEP, lon_step: float = LON_STEP,
               ) -> dict[str, list]:
    """Null-separated polyline segments shading SZA > zenith, per parallel.

    For each latitude the sunrise equation gives the hour-angle half-width
    h0 of the sunlit cap at this zenith angle; darkness spans the rest,
    centred on the antisolar meridian. Segments are sampled every lon_step
    so they follow the parallel, and split at the dateline.
    """
    zr, dr = math.radians(zenith), math.radians(dec)
    lons: list[float | None] = []
    lats: list[float | None] = []

    lat = -90.0 + lat_step
    while lat < 90.0:
        lr = math.radians(lat)
        denom = math.cos(lr) * math.cos(dr)
        if abs(denom) < 1e-9:  # pole
            lat += lat_step
            continue
        x = (math.cos(zr) - math.sin(lr) * math.sin(dr)) / denom
        if x <= -1.0:  # sun never below this zenith angle → no shading (midnight sun)
            lat += lat_step
            continue
        if x >= 1.0:  # sun never above it → dark all day (polar night)
            spans = [(-180.0, 180.0)]
        else:
            h0 = math.degrees(math.acos(x))  # sunlit half-width around noon
            a = _wrap(lon_ss + h0)           # darkness runs a → b eastwards
            b = _wrap(lon_ss - h0)
            spans = [(a, b)] if a <= b else [(a, 180.0), (-180.0, b)]
        for lo, hi in spans:
            if hi - lo < 1e-6:
                continue
            n = max(2, int((hi - lo) / lon_step) + 1)
            for i in range(n):
                lons.append(lo + (hi - lo) * i / (n - 1))
                lats.append(lat)
            lons.append(None)
            lats.append(None)
        lat += lat_step
    return {"lon": lons, "lat": lats}


def terminator_curve(dec: float, lon_ss: float) -> dict[str, list]:
    """The SZA = 90° great circle, as lon/lat arrays split at the dateline."""
    dr = math.radians(dec)
    if abs(dec) < 0.05:  # equinox: terminator is the lon_ss ± 90° meridian pair
        lons, lats = [], []
        for lon in (_wrap(lon_ss + 90), _wrap(lon_ss - 90)):
            for k in range(-90, 91, 3):
                lons.append(lon)
                lats.append(float(k))
            lons.append(None)
            lats.append(None)
        return {"lon": lons, "lat": lats}

    pts = []
    for h in range(-180, 181, 2):  # hour angle from subsolar meridian
        lat = math.degrees(math.atan(-math.cos(math.radians(h)) / math.tan(dr)))
        pts.append((_wrap(lon_ss + h), lat))
    pts.sort()  # by longitude → single west-to-east polyline, no dateline jump
    return {"lon": [p[0] for p in pts], "lat": [p[1] for p in pts]}


class EarthIllumination(DataSource):
    name = "earth_illumination"
    display_name = "Earth illumination (day/night map)"
    keywords = [
        "terminator", "illumination", "day night", "day/night", "daylight",
        "insolation", "subsolar", "twilight", "sza", "sunlit", "earth map",
        "solar zenith angle", "where is it dark", "where is it light",
        "night side", "dayside",
    ]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        span = end - start
        epoch = start + span / 2
        lat_ss, lon_ss = subsolar_point(epoch)

        shading = []
        for zenith, label, opacity in TWILIGHT_LEVELS:
            band = night_band(lat_ss, lon_ss, zenith)
            shading.append({**band, "label": label, "opacity": opacity})

        spec: PlotSpec = {
            "kind": "earthmap",
            "title": f"Earth illumination  {epoch:%Y-%m-%d %H:%M} UT  ·  "
                     f"subsolar {abs(lat_ss):.1f}°{'N' if lat_ss >= 0 else 'S'} "
                     f"{abs(lon_ss):.1f}°{'E' if lon_ss >= 0 else 'W'}",
            "shading": shading,
            "lines": [{"name": "terminator", **terminator_curve(lat_ss, lon_ss)}],
            "points": [{"name": "subsolar", "lat": lat_ss, "lon": lon_ss}],
            "provenance": "Subsolar point & twilight geometry from astropy "
                          "(apparent Sun; SZA 90/96/102/108°)",
        }

        if span > timedelta(days=1, hours=1):  # add the subsolar track
            # one sample per day at the epoch's UT hour — the subsolar point
            # circles the globe daily, so free-running samples would alias
            # into meaningless zigzags; fixed-hour sampling shows the slow
            # seasonal drift instead (an analemma segment).
            step = max(1, math.ceil(span.days / MAX_TRACK))
            t = datetime.combine(start.date(), epoch.timetz())
            if t < start:
                t += timedelta(days=1)
            track = []
            while t <= end:
                track.append(subsolar_point(t))
                t += timedelta(days=step)
            if len(track) > 1:
                spec["track"] = {
                    "lat": [p[0] for p in track],
                    "lon": [p[1] for p in track],
                    "name": f"subsolar at {epoch:%H:%M} UT, {start:%m-%d} → {end:%m-%d}",
                }
                spec["note"] = (
                    f"Shading shows the midpoint epoch; dots track the subsolar "
                    f"point at {epoch:%H:%M} UT each day across the range."
                )
        return spec
