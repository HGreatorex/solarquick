"""PROBA2 LYRA solar UV/EUV irradiance (4 broadband channels).

Level-3 (1-min averaged) daily FITS via sunpy/Fido. Coverage from 2010-01-06.
Note: LYRA channels degrade over the mission; this is quicklook, not
calibrated irradiance.

Query examples: "lyra yesterday", "proba2 lyra 2026-08-01".
"""
from __future__ import annotations

from datetime import datetime

from solarapp.core.registry import DataSource, PlotSpec, Variable
from solarapp.core.tsutil import clip_df, df_to_series, downsample_df, fetch_files

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
    coverage = ("2010-01-06", None)

    # All four channels are plotted unless one is named.
    variables = [
        Variable("CHANNEL1", "Lyman-α 120–123 nm",
                 ["lyman alpha", "lyman", "channel 1", "120-123"]),
        Variable("CHANNEL2", "Herzberg 190–222 nm",
                 ["herzberg", "channel 2", "190-222"]),
        Variable("CHANNEL3", "Aluminium 17–80 nm",
                 ["aluminium", "aluminum", "al channel", "channel 3", "17-80"]),
        Variable("CHANNEL4", "Zirconium 6–20 nm",
                 ["zirconium", "zr", "channel 4", "6-20"]),
    ]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)
        channels = {k: v for k, v in CHANNELS.items()
                    if extras.get("channel") in (None, k)}

        from sunpy import timeseries as ts
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        q = Fido.search(a.Time(start, end), a.Instrument("lyra"), a.Level(3))
        if sum(len(r) for r in q) == 0:
            raise RuntimeError("No LYRA level-3 files found for this interval.")
        # P2SA/VSO also answers this query but ignores the level filter and
        # serves lev1 engineering files — keep only true lev3 products.
        files = [f for f in fetch_files(q, "PROBA2 LYRA") if "lev3" in f]
        if not files:
            raise RuntimeError("No LYRA level-3 files could be downloaded for this interval.")
        tseries = ts.TimeSeries(files, concatenate=True)
        df = downsample_df(clip_df(tseries.to_dataframe(), start, end), start, end)

        series = df_to_series(df, channels)
        label = "PROBA2 LYRA" + (
            f" · {channels[extras['channel']]}" if extras.get("channel") in channels else "")
        return {
            "title": f"{label}  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Irradiance (W m⁻²)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "sampling": df.attrs.get("sampling"),
            "provenance": "PROBA2 LYRA level 3 via sunpy/Fido",
        }
