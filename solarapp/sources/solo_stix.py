"""Solar Orbiter STIX quicklook X-ray lightcurves.

Direct from the STIX Data Center (i4ds) REST API — no file downloads. Five
quicklook energy bands at 4 s cadence, light-travel-time corrected to Earth
UT by the server (ltc=true). Coverage from 2020-04.

Query examples: "solar orbiter stix yesterday", "stix 2026-08-01".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from solarapp.core import httpclient
from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import check_mission_span

API = "https://datacenter.stix.i4ds.net/api/request/ql/lightcurves"
MAX_DAYS = 7  # 4 s cadence; keep requests sane
TARGET_POINTS = 3000  # per band, before striding


class SoloStix(DataSource):
    name = "solo_stix"
    display_name = "Solar Orbiter STIX (X-ray lightcurves)"
    keywords = ["stix", "solar orbiter stix", "solo stix"]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        check_mission_span(start, end, "2020-04-14", None, "Solar Orbiter STIX")
        if end - start > timedelta(days=MAX_DAYS):
            raise RuntimeError(f"STIX quicklook requests are limited to {MAX_DAYS} days.")

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
        series = []
        for key, label in zip(sorted(d["light_curves"], key=int),
                              d["energy_bins"]["names"]):
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
