"""Fermi GBM summary lightcurves (CTIME, one NaI detector).

Daily CTIME files via sunpy/Fido, rebinned by sunpy into broad energy bands.
GBM is an all-sky monitor: the Sun is not always in a given detector's field
of view, and detector choice matters. Default is NaI detector n5; override
with e.g. "fermi gbm n3 ...". Coverage from 2008-08-12.

Query examples: "fermi gbm 2017-09-06", "gbm n3 for 2022-04-20".
"""
from __future__ import annotations

import re
from datetime import datetime

from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import check_mission_span, clip_df, df_to_series, downsample_df, fetch_files

DEFAULT_DETECTOR = "n5"


class FermiGbm(DataSource):
    name = "fermi_gbm"
    display_name = "Fermi GBM (X-ray/gamma lightcurves)"
    keywords = ["fermi", "gbm", "fermi gbm"]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        m = re.search(r"\b(n[0-9ab])\b", query.lower())
        if m:
            return {"detector": m.group(1)}, [m.group(0)]
        return {}, []

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        check_mission_span(start, end, "2008-08-12", None, "Fermi GBM")
        detector = extras.get("detector", DEFAULT_DETECTOR)

        from sunpy import timeseries as ts
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        q = Fido.search(a.Time(start, end), a.Instrument("gbm"),
                        a.Resolution("ctime"), a.Detector(detector))
        if sum(len(r) for r in q) == 0:
            raise RuntimeError(f"No Fermi GBM CTIME files found for detector {detector}.")
        files = fetch_files(q, "Fermi GBM")
        tseries = ts.TimeSeries(files, concatenate=True)
        df = downsample_df(clip_df(tseries.to_dataframe(), start, end), start, end)

        series = df_to_series(df, {c: c for c in df.columns})
        return {
            "title": f"Fermi GBM ({detector})  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Count rate (s⁻¹)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "sampling": df.attrs.get("sampling"),
            "provenance": f"Fermi GBM CTIME, detector {detector}, via sunpy/Fido "
                          "(all-sky monitor — solar pointing not guaranteed)",
        }
