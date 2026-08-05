"""GOES XRS soft X-ray flux data source.

Two backends, chosen automatically:
- Recent data (within ~7 days): NOAA SWPC realtime JSON (no sunpy download needed).
- Archival data: NOAA NCEI science netCDF files via sunpy Fido (cached locally).

Template for new plugins: subclass DataSource, set name/display_name/keywords,
implement fetch(start, end) -> PlotSpec.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import requests

from solarapp.core.registry import DataSource, PlotSpec

SWPC_7DAY = "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json"

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
    keywords = [
        "goes xrs", "goes sxr", "goes x-ray", "goes xray",
        "goes", "xrs", "sxr", "soft x-ray", "x-ray flux",
    ]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        now = datetime.now(timezone.utc)
        if start > now - timedelta(days=7):
            series = self._fetch_swpc(start, end)
            provenance = "NOAA SWPC realtime (primary GOES)"
        else:
            series = self._fetch_ncei(start, end)
            provenance = "NOAA NCEI science data via sunpy/Fido"

        return {
            "title": f"GOES XRS  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Flux (W m⁻²)", "type": "log", "range": [1e-9, 1e-3]},
            "xaxis": {"label": "Time (UT)"},
            "bands": FLARE_BANDS,
            "provenance": provenance,
        }

    # -- backends ---------------------------------------------------------

    def _fetch_swpc(self, start: datetime, end: datetime) -> list[dict]:
        rows = requests.get(SWPC_7DAY, timeout=30).json()
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

    def _fetch_ncei(self, start: datetime, end: datetime) -> list[dict]:
        # heavy imports kept local so the app starts fast
        from sunpy import timeseries as ts
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        q = Fido.search(a.Time(start, end), a.Instrument("XRS"), a.Resolution("avg1m"))
        if sum(len(r) for r in q) == 0:
            raise RuntimeError(
                "No GOES XRS files found on NCEI for this interval. "
                "Note: the science archive lags real time; very recent dates "
                "are served from SWPC instead."
            )
        files = Fido.fetch(q)
        tseries = ts.TimeSeries(files, concatenate=True)
        df = tseries.to_dataframe()
        df = df[(df.index >= start.replace(tzinfo=None)) & (df.index <= end.replace(tzinfo=None))]

        def col(name: str) -> dict:
            times, values = [], []
            if name in df:
                for t, v in df[name].items():
                    if v is not None and not math.isnan(v) and v > 0:
                        times.append(t.isoformat() + "+00:00")
                        values.append(float(v))
            return {"times": times, "values": values}

        return [
            {"name": "XRS-A 0.5–4 Å", **col("xrsa")},
            {"name": "XRS-B 1–8 Å", **col("xrsb")},
        ]
