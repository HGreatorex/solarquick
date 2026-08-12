"""RHESSI observing-summary count rates (hard X-ray / gamma lightcurves).

Daily observing-summary files via sunpy/Fido, cached locally. RHESSI observed
2002-02-12 – 2018-04-11 (mission ended); band count rates are corrected
counts s⁻¹ detector⁻¹.

Query examples: "rhessi for 2003-10-28", "rhessi 2017-09-06 to 2017-09-11".
"""
from __future__ import annotations

from datetime import datetime

from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import check_mission_span, clip_df, df_to_series, downsample_df, fetch_files


class Rhessi(DataSource):
    name = "rhessi"
    display_name = "RHESSI (hard X-ray count rates)"
    keywords = ["rhessi", "hard x-ray", "hxr"]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        check_mission_span(start, end, "2002-02-12", "2018-04-11", "RHESSI")

        from sunpy import timeseries as ts
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        q = Fido.search(a.Time(start, end), a.Instrument("rhessi"),
                        a.Physobs("summary_lightcurve"))
        if sum(len(r) for r in q) == 0:
            raise RuntimeError("No RHESSI observing-summary files found for this interval.")
        files = fetch_files(q, "RHESSI")
        tseries = ts.TimeSeries(files, concatenate=True)
        df = downsample_df(clip_df(tseries.to_dataframe(), start, end), start, end)

        series = df_to_series(df, {c: c for c in df.columns})
        return {
            "title": f"RHESSI observing summary  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Corrected count rate (s⁻¹ det⁻¹)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "provenance": "RHESSI observing summary via sunpy/Fido",
        }
