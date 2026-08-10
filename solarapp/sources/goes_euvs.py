"""GOES-R EUVS solar EUV/FUV line irradiances (EXIS instrument).

Daily 1-min-average L2 netCDF from NOAA NCEI, read with h5netcdf.

Satellite and channel are query options:
- "goes-18 euvs 304 yesterday"  → one satellite, one line
- "euvs yesterday"              → the primary satellite for that date, all
  channels grouped into three panels (EUVS-A lines, EUVS-B lines, MgII index)

Channels: 25.6 nm (He II), 28.4 nm (Fe XV), 30.4 nm (He II) [EUVS-A];
117.5 nm, 121.6 nm (Lyman-α), 133.5 nm (C II), 140.5 nm (Si IV) [EUVS-B];
plus the MgII core-to-wing index. GOES-R EUVS coverage starts 2017-02-07
(GOES-16); GOES-19 is primary from 2025-04-07.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np

from solarapp.core.fetchcache import cached_download, listing
from solarapp.core.registry import DataSource, PlotSpec

NCEI = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes"
J2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
MAX_DAYS = 3

# var name -> (wavelength label, group)
CHANNELS = {
    "irr_256": ("25.6 nm He II", "EUVS-A"),
    "irr_284": ("28.4 nm Fe XV", "EUVS-A"),
    "irr_304": ("30.4 nm He II", "EUVS-A"),
    "irr_1175": ("117.5 nm", "EUVS-B"),
    "irr_1216": ("121.6 nm Lyman-α", "EUVS-B"),
    "irr_1335": ("133.5 nm C II", "EUVS-B"),
    "irr_1405": ("140.5 nm Si IV", "EUVS-B"),
    "MgII_standard": ("MgII index", "MgII"),
}
CHANNEL_ALIASES = {
    "256": "irr_256", "25.6": "irr_256",
    "284": "irr_284", "28.4": "irr_284",
    "304": "irr_304", "30.4": "irr_304",
    "1175": "irr_1175", "117.5": "irr_1175",
    "1216": "irr_1216", "121.6": "irr_1216",
    "1335": "irr_1335", "133.5": "irr_1335",
    "1405": "irr_1405", "140.5": "irr_1405",
}
# (primary-from date, satellite number)
PRIMARY = [("2017-02-07", 16), ("2025-04-07", 19)]


def primary_satellite(when: datetime) -> int | None:
    sat = None
    for since, number in PRIMARY:
        if when >= datetime.fromisoformat(since).replace(tzinfo=when.tzinfo):
            sat = number
    return sat


class GoesEuvs(DataSource):
    name = "goes_euvs"
    display_name = "GOES EUVS (EUV/FUV line irradiance)"
    # channel words (mg ii, lyman alpha) deliberately aren't keywords: they
    # select a channel via parse_extras once "euvs" has routed here, and they
    # would otherwise hijack queries meant for EVE or EUI Lyman-α.
    keywords = ["euvs", "goes euvs", "euv sensor"]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        extras: dict = {}
        consumed: list[str] = []

        m = re.search(r"\bgoes[- ]?(1[5-9])\b", ql)
        if m:
            extras["satellite"] = int(m.group(1))
            consumed.append(m.group(0))

        if re.search(r"\bmg ?ii\b", ql):
            extras["channel"] = "MgII_standard"
        elif "lyman" in ql:
            extras["channel"] = "irr_1216"
        else:
            m = re.search(r"\b(\d{3,4}(?:\.\d)?|\d{2}\.\d)\b", ql)
            if m and m.group(1) in CHANNEL_ALIASES:
                extras["channel"] = CHANNEL_ALIASES[m.group(1)]
                consumed.append(m.group(0))
        return extras, consumed

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        if end - start > timedelta(days=MAX_DAYS):
            raise RuntimeError(f"GOES EUVS requests are limited to {MAX_DAYS} days.")
        sat = extras.get("satellite")
        note = None
        if sat is None:
            sat = primary_satellite(start)
            if sat is None:
                raise RuntimeError(
                    "GOES-R EUVS data starts 2017-02-07 (GOES-16). "
                    "For earlier EUV irradiance try SDO EVE (from 2010)."
                )
            note = (f"Using GOES-{sat} (primary for this date); "
                    f"say e.g. 'goes-18 euvs' to choose another satellite.")
        channel = extras.get("channel")

        data = self._read_days(sat, start, end)

        def series_for(var: str) -> dict:
            label, _ = CHANNELS[var]
            t, v = data.get(var, ([], []))
            return {"name": label, "times": t, "values": v}

        span = f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT"
        prov = f"GOES-{sat} EXIS/EUVS L2 1-min averages (NOAA NCEI)"
        if channel:
            label, group = CHANNELS[channel]
            is_mgii = group == "MgII"
            return {
                "title": f"GOES-{sat} EUVS {label}  {span}",
                "series": [series_for(channel)],
                "yaxis": {"label": "MgII index" if is_mgii else "Irradiance (W m⁻²)",
                          "type": "linear" if is_mgii else "log"},
                "xaxis": {"label": "Time (UT)"},
                "provenance": prov,
                **({"note": note} if note else {}),
            }

        panels = []
        for group, ylabel, ytype in [("EUVS-A", "Irradiance (W m⁻²)", "log"),
                                     ("EUVS-B", "Irradiance (W m⁻²)", "log"),
                                     ("MgII", "MgII index", "linear")]:
            series = [series_for(v) for v, (_, g) in CHANNELS.items() if g == group]
            series = [s for s in series if s["values"]]
            if series:
                panels.append({
                    "title": f"GOES-{sat} EUVS {group}",
                    "series": series,
                    "yaxis": {"label": ylabel, "type": ytype},
                    "xaxis": {"label": "Time (UT)"},
                })
        if not panels:
            raise RuntimeError(f"No GOES-{sat} EUVS data found for this interval.")
        return {
            "kind": "panels",
            "panels": panels,
            "title": f"GOES-{sat} EUVS  {span}",
            "provenance": prov,
            **({"note": note} if note else {}),
        }

    # -- NCEI access ------------------------------------------------------

    def _read_days(self, sat: int, start: datetime, end: datetime) -> dict:
        import h5netcdf

        out: dict[str, tuple[list, list]] = {}
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        found_any = False
        while day < end:
            try:
                path = self._day_file(sat, day)
                found_any = True
            except Exception:
                day += timedelta(days=1)
                continue
            with h5netcdf.File(str(path), "r") as f:
                secs = np.asarray(f.variables["time"][:], dtype=float)
                times = [J2000 + timedelta(seconds=s) for s in secs]
                for var in CHANNELS:
                    if var not in f.variables:
                        continue
                    vals = np.asarray(f.variables[var][:], dtype=float)
                    t_acc, v_acc = out.setdefault(var, ([], []))
                    for t, v in zip(times, vals):
                        if np.isfinite(v) and v > 0 and start <= t <= end:
                            t_acc.append(t.isoformat())
                            v_acc.append(float(v))
            day += timedelta(days=1)
        if not found_any:
            raise RuntimeError(
                f"No GOES-{sat} EUVS files on NCEI for this interval "
                "(the science archive lags real time by ~1–2 weeks)."
            )
        return out

    def _day_file(self, sat: int, day: datetime):
        base = f"{NCEI}/goes{sat}/l2/data/euvs-l2-avg1m_science/{day:%Y}/{day:%m}"
        pattern = rf"sci_euvs-l2-avg1m_g{sat}_d{day:%Y%m%d}_v[\d-]+\.nc"
        names = re.findall(pattern, listing(f"{base}/"))
        if not names:
            raise FileNotFoundError(f"no EUVS file for g{sat} {day:%Y-%m-%d}")
        name = sorted(set(names))[-1]
        return cached_download(f"{base}/{name}", name)
