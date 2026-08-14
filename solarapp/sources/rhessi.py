"""RHESSI observing-summary count rates (hard X-ray / gamma lightcurves).

Daily observing-summary files via sunpy/Fido, cached locally. RHESSI observed
2002-02-12 – 2018-04-11 (mission ended); band count rates are corrected
counts s⁻¹ detector⁻¹.

Query examples: "rhessi for 2003-10-28", "rhessi 2017-09-06 to 2017-09-11".
"""
from __future__ import annotations

from datetime import datetime

from solarapp.core.registry import DataSource, PlotSpec, Variable
from solarapp.core.tsutil import (
    clip_df, df_to_series, downsample_df, fetch_files, select_bands,
)

# The observing-summary energy bands, in keV. Column labels vary in spacing
# between sunpy versions ("6 - 12 keV"), so bands are matched on their numbers.
BANDS = ["3-6", "6-12", "12-25", "25-50", "50-100", "100-300",
         "300-800", "800-7000", "7000-20000"]
HARD_FROM_KEV = 25  # what "hard X-ray" means when someone says it of RHESSI


class Rhessi(DataSource):
    name = "rhessi"
    display_name = "RHESSI (hard X-ray count rates)"
    keywords = ["rhessi", "hard x-ray", "hxr"]  # hyphen/plural variants are handled by the matcher
    coverage = ("2002-02-12", "2018-04-11")

    variable_key = "band"
    variables = [
        Variable(b, f"{b.replace('-', '–')} keV",
                 [f"{b} kev", f"{b} kilo electron volt"])
        for b in BANDS
    ] + [
        Variable("hard", f"hard X-ray bands (≥{HARD_FROM_KEV} keV)",
                 ["hard x-ray", "non thermal", "nonthermal", "hxr"]),
        Variable("thermal", f"thermal bands (<{HARD_FROM_KEV} keV)",
                 ["thermal", "soft bands", "low energy"]),
    ]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)

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

        cols = select_bands(df.columns, extras.get("band"), HARD_FROM_KEV)
        series = df_to_series(df, {c: c for c in cols})
        band = extras.get("band")
        which = f" · {band} keV" if band and band not in ("hard", "thermal") else (
            f" · {band} bands" if band else "")
        return {
            "title": f"RHESSI observing summary{which}  "
                     f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Corrected count rate (s⁻¹ det⁻¹)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "sampling": df.attrs.get("sampling"),
            "provenance": "RHESSI observing summary via sunpy/Fido",
        }
