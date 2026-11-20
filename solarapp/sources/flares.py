"""Solar flare events from the HEK catalog (SWPC/GOES flare list).

Three query shapes, resolved with core/events.py:
- list:        "flares yesterday", "x-class flares in july 2026"
- superlative: "biggest flare since 2014" → single event, auto-composed view
- pinpoint:    "flare at 2017-09-06 12:02" (event-list rows click through here)

The returned PlotSpec (kind "events") carries `markers` — flare peak times —
which the frontend overlays on any timeseries plotted in the same request
("goes xrs and flares yesterday").
"""
from __future__ import annotations

import re
from datetime import datetime

from solarapp.core import hek
from solarapp.sources.solar_regions import flare_sites_from_regions
from solarapp.core.registry import DataSource, PlotSpec, Variable

_ABOVE = re.compile(r"\b(?:above|over|>=?)\s*([abcmx])\s*(\d+(?:\.\d+)?)?", re.I)
_SUPERLATIVE = re.compile(r"\b(biggest|largest|strongest|most powerful)\b", re.I)
_AT = re.compile(r"\bflare\s+at\b", re.I)


class Flares(DataSource):
    name = "flares"
    display_name = "Solar flares (HEK/SWPC catalog)"
    keywords = ["flare", "flares", "flare list", "solar flares",
                "flare catalog", "flare catalogue", "goes flare", "eruption"]
    coverage = ("1996-01-01", None)  # HEK's SWPC list
    summary_rank = 20  # what happened, in one table

    # Class thresholds are read by parse_extras below (they carry a number:
    # "above M5"), so the declared variables only cover the bare letters.
    variable_key = "min_class"
    variables = [
        Variable(c, f"{c}-class and above", [f"{c} class"])
        for c in ("X", "M", "C", "B", "A")
    ]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        # the declared variables cover the bare letters ("x-class"); a
        # threshold that carries a number ("above M5") needs the regex, and
        # wins because it is the more specific statement
        extras, consumed = self.variable_extras(query)
        m = _ABOVE.search(query)
        if m:
            extras["min_class"] = (m.group(1) + (m.group(2) or "")).upper()
            consumed.append(m.group(0))
        if _SUPERLATIVE.search(query):
            extras["superlative"] = True
        if _AT.search(query):
            extras["at"] = True
        return extras, consumed

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        min_class = extras.get("min_class")
        flares = hek.search_flares(start, end, min_class=min_class)
        pin = extras.get("pin_peak")

        try:  # an SRS outage must not cost the flare list
            placed = flare_sites_from_regions(flares)
        except Exception:
            placed = {}

        events, markers = [], []
        for i, f in enumerate(flares):
            est = placed.get(i)
            events.append({
                "class": f.goes_class or "?",
                "start": f.start.isoformat(),
                "peak": f.peak.isoformat(),
                "end": f.end.isoformat(),
                "x": round(f.hpc_x, 1) if f.has_position else (est[0] if est else None),
                "y": round(f.hpc_y, 1) if f.has_position else (est[1] if est else None),
                # set when the position is estimated rather than catalogued
                "position_from": est[2] if est else None,
                "ar": f.ar or None,
                "query": f"flare at {f.peak:%Y-%m-%d %H:%M}",
                "selected": pin is not None and f.peak.isoformat() == pin,
            })
            markers.append({"t": f.peak.isoformat(), "label": f.goes_class or "?"})

        what = f"{min_class}-class flares" if min_class else "Flares"
        spec: PlotSpec = {
            "kind": "events",
            "title": f"{what}  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "events": events,
            "markers": markers,
            "provenance": "HEK (LMSAL) — SWPC/GOES flare list",
        }
        if placed:
            spec["note"] = (f"{len(placed)} flare position(s) estimated from the "
                            "flaring region in the Solar Region Summary (marked ≈).")
        if not events:
            spec["note"] = "No flares in the catalog for this interval/class."
        return spec
