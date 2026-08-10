"""PROBA2 LYRA solar UV/EUV irradiance (4 broadband channels).

Level-3 (1-min averaged) daily FITS via sunpy/Fido. Coverage from 2010-01-06.
Note: LYRA channels degrade over the mission; this is quicklook, not
calibrated irradiance.

Query examples: "lyra yesterday", "proba2 lyra 2026-08-01".
"""
from __future__ import annotations

from datetime import datetime

from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import check_mission_span, clip_df, df_to_series, downsample_df

CHANNELS = {
    "CHANNEL1": "Lyman-α 120–123 nm",
    "CHANNEL2": "Herzberg 190–222 nm",
    "CHANNEL3": "Aluminium 17–80 nm",
    "CHANNEL4": "Zirconium 6–20 nm",
}


class Proba2Lyra(DataSource):
    name = "proba2_lyra"
    display_name = "PROBA2 LYRA (UV/EUV irradiance)"
    keywords = ["lyra", "proba2 lyra"]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        check_mission_span(start, end, "2010-01-06", None, "PROBA2 LYRA")

        from sunpy import timeseries as ts
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        q = Fido.search(a.Time(start, end), a.Instrument("lyra"), a.Level(3))
        if sum(len(r) for r in q) == 0:
            raise RuntimeError("No LYRA level-3 files found for this interval.")
        # P2SA/VSO also answers this query but ignores the level filter and
        # serves lev1 engineering files — keep only true lev3 products.
        files = [f for f in Fido.fetch(q) if "lev3" in f]
        if not files:
            raise RuntimeError("No LYRA level-3 files could be downloaded for this interval.")
        tseries = ts.TimeSeries(files, concatenate=True)
        df = downsample_df(clip_df(tseries.to_dataframe(), start, end), start, end)

        series = df_to_series(df, CHANNELS)
        return {
            "title": f"PROBA2 LYRA  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Irradiance (W m⁻²)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "provenance": "PROBA2 LYRA level 3 via sunpy/Fido",
        }
