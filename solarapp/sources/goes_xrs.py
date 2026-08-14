"""GOES XRS soft X-ray flux data source.

Two backends, chosen automatically:
- Recent data (within ~7 days): NOAA SWPC realtime JSON (no sunpy download needed).
- Archival data: NOAA NCEI science netCDF files via sunpy Fido (cached locally).

The archival backend serves the whole fleet back to GOES-8 (1995), in either of
the NCEI science products:
- `avg1m` (default) — 1-minute averages, one point per minute.
- `flx1s` — the high-resolution irradiance files, 1 s on GOES-R, 2 s on
  GOES 13-15 and 3 s below that. Ask for it by saying "high resolution"
  ("goes xrs high resolution 2014-06-10"); a day of it is ~40 000 samples,
  so it is mean-binned for the browser and the binning is disclosed in exports.

Both are science-quality reprocessed data, so the fluxes are *true*
irradiances rather than the historical SWPC-scaled values the flare classes
were defined on — see the note on FLARE_BANDS below.

Template for new plugins: subclass DataSource, set name/display_name/keywords,
implement fetch(start, end) -> PlotSpec.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from solarapp.core import httpclient
from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import (
    clip_df, downsample_df, fetch_files, pick_satellite, satellite_in_query,
)

SWPC_7DAY = "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json"

# words that ask for the high-cadence NCEI product instead of 1-min averages
HIRES_RE = re.compile(
    r"\b(?:high|full)[\s-]*(?:resolution|res|cadence)\b|\bhi[\s-]*res\b"
    r"|\bflx1s\b|\b[123][\s-]*(?:second|sec|s)\b",
    re.I,
)

# NOTE — these bands are the operational GOES flare classes, which were defined
# on SWPC's scaled fluxes. NCEI's science-quality archive reports true
# irradiances: for GOES 13-15 the reprocessed XRS-B peak of the 2014-06-10 X2.2
# flare is 3.3e-4 (an "X3.3"), ~1.5x the class it was given. So for pre-GOES-R
# dates the bands locate a flare on the plot but overstate its class by roughly
# half a letter step. This is not new here — the existing avg1m backend has
# always served the same science-quality files — but it is worth knowing before
# quoting a class off this figure.
FLARE_BANDS = [
    {"label": "A", "y0": 1e-8, "y1": 1e-7},
    {"label": "B", "y0": 1e-7, "y1": 1e-6},
    {"label": "C", "y0": 1e-6, "y1": 1e-5},
    {"label": "M", "y0": 1e-5, "y1": 1e-4},
    {"label": "X", "y0": 1e-4, "y1": 1e-3},
]


class GoesXRS(DataSource):
    name = "goes_xrs"
    display_name = "GOES XRS (soft X-ray flux)"
    # keyword matching is tolerant of hyphens, spaces and plurals (see
    # registry.keyword_regex), so "x-ray" alone covers "xray", "x rays", ...
    # A bare "x-ray" belongs here rather than to RHESSI/STIX/GBM: in solar
    # physics "the X-ray light curve" means GOES unless a band is named, and
    # those sources outscore this one whenever theirs is ("hard x-ray", "stix").
    keywords = [
        "goes xrs", "goes sxr", "goes x-ray",
        "goes", "xrs", "sxr", "soft x-ray", "x-ray", "x-ray flux",
    ]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        extras: dict = {}
        consumed: list[str] = []

        sat, text = satellite_in_query(query, 8, 19)
        if sat is not None:
            extras["satellite"] = sat
            consumed.append(text)

        m = HIRES_RE.search(query)
        if m:
            extras["resolution"] = "flx1s"
            # consumed so "2 second" can't be read as a day-of-month
            consumed.append(m.group(0))
        return extras, consumed

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        now = datetime.now(timezone.utc)
        sat = extras.get("satellite")
        resolution = extras.get("resolution", "avg1m")
        sampling = None

        # the realtime feed is one primary-satellite, 1-min product, so any
        # request that names a spacecraft or asks for high cadence has to go to
        # the archive even when the date is recent enough for SWPC
        recent = start > now - timedelta(days=7)
        if recent and sat is None and resolution == "avg1m":
            series = self._fetch_swpc(start, end)
            provenance = "NOAA SWPC realtime (primary GOES)"
            which = "GOES"
        else:
            series, sampling, sat = self._fetch_ncei(start, end, sat, resolution, recent)
            cadence = "high-resolution" if resolution == "flx1s" else "1-min average"
            which = f"GOES-{sat}" if sat else "GOES"
            provenance = (f"{which} XRS {cadence} science data (NOAA NCEI) "
                          f"via sunpy/Fido")

        return {
            "title": f"{which} XRS  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Flux (W m⁻²)", "type": "log", "range": [1e-9, 1e-3]},
            "xaxis": {"label": "Time (UT)"},
            "bands": FLARE_BANDS,
            "provenance": provenance,
            **({"sampling": sampling} if sampling else {}),
        }

    # -- backends ---------------------------------------------------------

    def _fetch_swpc(self, start: datetime, end: datetime) -> list[dict]:
        # one ~4.6 MB rolling feed serves every recent-GOES query, so it is
        # briefly cached rather than re-downloaded per window
        rows = httpclient.get_json_cached(SWPC_7DAY)
        out = {"0.05-0.4nm": {"t": [], "v": []}, "0.1-0.8nm": {"t": [], "v": []}}
        for r in rows:
            t = datetime.fromisoformat(r["time_tag"].replace("Z", "+00:00"))
            if start <= t <= end and r["energy"] in out and r["flux"] > 0:
                out[r["energy"]]["t"].append(t.isoformat())
                out[r["energy"]]["v"].append(r["flux"])
        return [
            {"name": "XRS-A 0.5–4 Å", "times": out["0.05-0.4nm"]["t"], "values": out["0.05-0.4nm"]["v"]},
            {"name": "XRS-B 1–8 Å", "times": out["0.1-0.8nm"]["t"], "values": out["0.1-0.8nm"]["v"]},
        ]

    def _fetch_ncei(self, start: datetime, end: datetime, sat: int | None,
                    resolution: str, recent: bool = False
                    ) -> tuple[list[dict], str | None, int | None]:
        # heavy imports kept local so the app starts fast
        from sunpy import timeseries as ts
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        search = [a.Time(start, end), a.Instrument("XRS"), a.Resolution(resolution)]
        if sat is not None:
            search.append(a.goes.SatelliteNumber(sat))
        q = Fido.search(*search)
        if sum(len(r) for r in q) == 0:
            which = f"GOES-{sat} " if sat is not None else ""
            # the plain-date case is almost always archive lag; a named
            # satellite or a named cadence is almost always a coverage gap,
            # and each has its own way out
            if recent:
                hint = ("The science archive lags real time by days to weeks. "
                        "Ask for the default 1-min primary-satellite data and "
                        "recent dates come from SWPC's realtime feed instead.")
            elif sat is not None:
                hint = (f"GOES-{sat} may not have been observing then — omit the "
                        f"satellite to use whichever spacecraft covers the date.")
            elif resolution == "flx1s":
                hint = ("The high-resolution product does not cover every date "
                        "the 1-min averages do — try without 'high resolution'.")
            else:
                hint = ("Note: the science archive lags real time; very recent "
                        "dates are served from SWPC instead.")
            raise RuntimeError(
                f"No {which}XRS {resolution} files found on NCEI for this "
                f"interval. {hint}"
            )
        results = q if sat is not None else pick_satellite(q)
        files = fetch_files(results, "GOES XRS")
        tseries = ts.TimeSeries(files, concatenate=True)
        df = clip_df(tseries.to_dataframe(), start, end)
        # a day of flx1s is ~40 000 samples per channel; avg1m is already coarse
        # enough that this is a no-op for it
        df = downsample_df(df, start, end)

        def col(name: str) -> dict:
            times, values = [], []
            if name in df:
                for t, v in df[name].items():
                    if v is not None and not math.isnan(v) and v > 0:
                        times.append(t.isoformat() + "+00:00")
                        values.append(float(v))
            return {"times": times, "values": values}

        series = [
            {"name": "XRS-A 0.5–4 Å", **col("xrsa")},
            {"name": "XRS-B 1–8 Å", **col("xrsb")},
        ]
        return series, df.attrs.get("sampling"), sat or _satellite_of(results)


def _satellite_of(results) -> int | None:
    """The GOES spacecraft a Fido result came from, for the provenance line.

    Accepts either a UnifiedResponse (several tables) or the single table
    pick_satellite narrowed it to, since the caller may hold either.
    """
    tables = [results] if hasattr(results, "colnames") else list(results)
    for table in tables:
        if len(table) and "SatelliteNumber" in getattr(table, "colnames", ()):
            return int(table[0]["SatelliteNumber"])
    return None
