"""Solar Orbiter STIX quicklook X-ray lightcurves.

Direct from the STIX Data Center (i4ds) REST API — no file downloads. Five
quicklook energy bands at 4 s cadence, light-travel-time corrected to Earth
UT by the server (ltc=true). Coverage from 2020-04.

Query examples: "solar orbiter stix yesterday", "stix 2026-08-01".
"""
from __future__ import annotations

from datetime import datetime, timezone

from solarapp.core import httpclient
from solarapp.core.registry import DataSource, PlotSpec, Variable
from solarapp.core.tsutil import check_max_days, select_bands

API = "https://datacenter.stix.i4ds.net/api/request/ql/lightcurves"
MAX_DAYS = 7  # 4 s cadence; keep requests sane
TARGET_POINTS = 3000  # per band, before striding
# the five quicklook bands the Data Center returns, in keV
BANDS = ["4-10", "10-15", "15-25", "25-50", "50-84"]
HARD_FROM_KEV = 25


class SoloStix(DataSource):
    name = "solo_stix"
    display_name = "Solar Orbiter STIX (X-ray lightcurves)"
    keywords = ["stix", "solar orbiter stix", "solo stix",
                "spectrometer telescope"]
    coverage = ("2020-04-14", None)

    variable_key = "band"
    variables = [
        Variable(b, f"{b.replace('-', '–')} keV", [f"{b} kev"]) for b in BANDS
    ] + [
        Variable("hard", f"hard bands (≥{HARD_FROM_KEV} keV)",
                 ["hard x-ray", "non thermal", "nonthermal"]),
        Variable("thermal", f"thermal bands (<{HARD_FROM_KEV} keV)",
                 ["thermal", "low energy"]),
    ]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)
        check_max_days(start, end, MAX_DAYS, "STIX quicklook")

        r = httpclient.session().post(API, data={
            "begin": f"{start:%Y-%m-%dT%H:%M:%S}",
            "end": f"{end:%Y-%m-%dT%H:%M:%S}",
            "ltc": "true",
        }, timeout=120)
        r.raise_for_status()
        d = r.json()
        if "error" in d or not d.get("unix_time"):
            raise RuntimeError(
                "STIX Data Center returned no quicklook data for this interval "
                "(downlink gaps of days–weeks are normal)."
            )

        stride = max(1, len(d["unix_time"]) // TARGET_POINTS)
        times = [
            datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
            for t in d["unix_time"][::stride]
        ]
        names = list(d["energy_bins"]["names"])
        wanted = set(select_bands(names, extras.get("band"), HARD_FROM_KEV))
        series = []
        for key, label in zip(sorted(d["light_curves"], key=int), names):
            if label not in wanted:
                continue
            counts = d["light_curves"][key][::stride]
            series.append({"name": label, "times": times, "values": counts})

        ltc = "light-time corrected to Earth UT" if d.get("IS_LIGHT_TIME_CORRECTED") \
            else "spacecraft UT (not light-time corrected)"
        return {
            "title": f"SolO STIX quicklook  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            "yaxis": {"label": "Counts (4 s bin)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "provenance": f"STIX Data Center (i4ds) quicklook, {ltc}",
        }
