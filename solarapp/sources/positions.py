"""Synoptic view: planet & spacecraft positions relative to the Sun.

Ecliptic-plane projection in the Heliographic Stonyhurst frame — the Sun at
the origin and the Sun-Earth line fixed along 0° (+x), the usual synoptic
convention (cf. Solar-MACH). Inner planets come from astropy's built-in
ephemeris (offline); spacecraft come from JPL Horizons via sunpy (network).

A single-day query plots one epoch (the interval midpoint); a longer range
adds motion trails from start to end — motion is relative to the rotating
Sun-Earth line, so Earth stays put by construction.

Query examples: "positions today", "where is psp 2026-07-10",
"spacecraft positions from 2026-06-01 to 2026-08-01".
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from solarapp.core.registry import DataSource, PlotSpec

MAX_SAMPLES = 15

PLANETS = [
    ("Mercury", "#9c8f84"),
    ("Venus", "#e0c47f"),
    ("Earth", "#4c9aff"),
    ("Mars", "#e2604d"),
]

# name shown → (JPL Horizons name, operational since, colour)
SPACECRAFT = {
    "STEREO-A": ("STEREO-A", datetime(2006, 10, 27), "#b083f0"),
    "PSP": ("Parker Solar Probe", datetime(2018, 8, 13), "#f0a832"),
    "BepiColombo": ("BepiColombo", datetime(2018, 10, 20), "#ff9ec4"),
    "Solar Orbiter": ("Solar Orbiter", datetime(2020, 2, 11), "#58d6a3"),
}


def sample_epochs(start: datetime, end: datetime) -> list[datetime]:
    """Midpoint only for a ≤1-day query; otherwise ≤MAX_SAMPLES evenly spaced."""
    span = end - start
    if span <= timedelta(days=1, hours=1):
        return [start + span / 2]
    n = min(MAX_SAMPLES, max(2, span.days + 1))
    return [start + span * i / (n - 1) for i in range(n)]


def to_xy(lon_deg: float, r_au: float) -> tuple[float, float]:
    """Stonyhurst lon/r → ecliptic-plane x/y with Earth on the +x axis."""
    lon = math.radians(lon_deg)
    return r_au * math.cos(lon), r_au * math.sin(lon)


_WHERE_IS = re.compile(r"\bwhere\s+(?:is|are|was|were)\b", re.I)
# Enough to outrank any instrument name that can follow the interrogative
# (the longest keyword in the app is 17 characters).
_INTERROGATIVE_BONUS = 24


class Positions(DataSource):
    name = "positions"
    display_name = "Positions (planets & spacecraft)"
    keywords = [
        "position", "where", "where is", "synoptic",
        "constellation", "ephemeris", "solar system", "spacecraft",
        "psp", "parker", "parker solar probe", "bepicolombo", "bepi",
        "stereo", "planets", "orbit", "heliocentric distance",
        "how far", "separation angle",
    ]

    def score(self, text: str) -> int:
        s = super().score(text)
        if _WHERE_IS.search(text):
            # "where is solar orbiter" asks for a location; without this it
            # ties exactly with EUI's "solar orbiter" and the answer would
            # depend on plugin load order.
            s += _INTERROGATIVE_BONUS
        return s

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        from astropy.time import Time
        from sunpy.coordinates import (
            get_body_heliographic_stonyhurst,
            get_horizons_coord,
        )

        epochs = sample_epochs(start, end)
        t = Time([e.replace(tzinfo=None) for e in epochs])

        bodies, notes = [], []
        for planet, color in PLANETS:
            c = get_body_heliographic_stonyhurst(planet.lower(), t)
            bodies.append(self._body(planet, "planet", color, c, epochs))

        for label, (horizons_name, since, color) in SPACECRAFT.items():
            if end.replace(tzinfo=None) < since:
                continue  # not launched yet at the requested time
            try:
                c = get_horizons_coord(horizons_name, t)
                bodies.append(self._body(label, "spacecraft", color, c, epochs))
            except Exception:
                notes.append(f"{label}: no JPL Horizons ephemeris for this date")

        when = (
            f"{epochs[0]:%Y-%m-%d %H:%M} UT" if len(epochs) == 1
            else f"{start:%Y-%m-%d} – {end:%Y-%m-%d} (trails)"
        )
        spec: PlotSpec = {
            "kind": "positions",
            "title": f"Solar system positions  {when}  ·  Earth-Sun line fixed at 0°",
            "bodies": bodies,
            "provenance": "Planets: astropy ephemeris · Spacecraft: JPL Horizons "
                          "(Stonyhurst frame, ecliptic-plane projection)",
        }
        if notes:
            spec["note"] = " · ".join(notes)
        return spec

    def _body(self, name, kind, color, coord, epochs) -> dict:
        lons = [float(v) for v in coord.lon.deg]
        rs = [float(v) for v in coord.radius.au]
        xy = [to_xy(lo, r) for lo, r in zip(lons, rs)]
        b = {
            "name": name, "type": kind, "color": color,
            "x": xy[-1][0], "y": xy[-1][1],
            "r": rs[-1], "lon_deg": lons[-1],
            "time": epochs[-1].isoformat(),
        }
        if len(xy) > 1:
            b["trail"] = {"x": [p[0] for p in xy], "y": [p[1] for p in xy]}
        return b
