"""GOES EUVS solar EUV/FUV irradiances, both instrument generations.

Daily L2 netCDF from NOAA NCEI, read with h5netcdf. Which archive is used
follows the date (or an explicitly named spacecraft):

- **GOES-R EXIS/EUVS**, 2017-02-07 onwards (GOES-16/17/18/19): 1-min averages
  of seven line irradiances plus the MgII index.
- **GOES 13–15 EUVS**, 2009-09-01 onwards (GOES-14/15): the "science quality"
  10.24 s product, which carries **Lyman-α only** — see below.

Satellite and channel are query options:
- "goes-18 euvs 304 yesterday"  → one satellite, one line
- "euvs yesterday"              → the primary satellite for that date, all
  channels grouped into three panels (EUVS-A lines, EUVS-B lines, MgII index)
- "euvs 2014-06-10"             → GOES-15, Lyman-α (the legacy archive)

GOES-R channels: 25.6 nm (He II), 28.4 nm (Fe XV), 30.4 nm (He II) [EUVS-A];
117.5 nm, 121.6 nm (Lyman-α), 133.5 nm (C II), 140.5 nm (Si IV) [EUVS-B];
plus the MgII core-to-wing index. GOES-19 is primary from 2025-04-07.

The legacy EUVS flew five broadband channels (A 5–15 nm, B 25–34 nm, C, D,
E 118–127 nm), but only channels B and E were reduced to L2, and **every B /
30.4 nm variable in the archive is fill** — verified across GOES-14 and
GOES-15 files from 2009, 2010, 2012, 2014, 2016 and 2019. So the pre-2017 era
serves the 121–122 nm band (Lyman-α) and nothing else; 30.4 nm before 2017 is
SDO EVE's job.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np

from solarapp.core.fetchcache import cached_download, listing
from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import (
    check_mission_span, clip_df, df_to_series, downsample_df, satellite_in_query,
)

NCEI = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes"
NCEI_LEGACY = ("https://www.ncei.noaa.gov/data/goes-space-environment-monitor"
               "/access/science/euvs")
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
GOES_R_START = "2017-02-07"

# -- legacy (GOES 13-15) -------------------------------------------------
# GOES-15 ran continuously; GOES-14 was a standby spacecraft, so its coverage
# is a handful of scattered campaigns (2009, 2010, 2012, 2015-2020) rather
# than a span. It is offered by name, never chosen by default.
LEGACY_SATS = {14: ("2009-09-01", "2020-03-31"), 15: ("2010-03-25", "2020-03-04")}
LEGACY_PRIMARY = [("2009-09-01", 14), ("2010-03-25", 15)]
# the one channel the legacy L2 archive actually populates (121-122 nm band)
LEGACY_VAR = "irr_1216_1nm"
LEGACY_FLAG = "irr_1216_flag"
LEGACY_TIME = "time_chanE"
LEGACY_LABEL = "121–122 nm (Lyman-α)"
LEGACY_CADENCE_S = 10.24


def _iso(date: str, like: datetime) -> datetime:
    """Parse an archive boundary date into `like`'s timezone awareness."""
    return datetime.fromisoformat(date).replace(tzinfo=like.tzinfo)


def primary_satellite(when: datetime) -> int | None:
    sat = None
    for since, number in PRIMARY:
        if when >= _iso(since, when):
            sat = number
    return sat


def legacy_satellite(when: datetime) -> int | None:
    """The pre-GOES-R spacecraft to use for `when`, or None if before coverage."""
    sat = None
    for since, number in LEGACY_PRIMARY:
        if when >= _iso(since, when):
            sat = number
    return sat


class GoesEuvs(DataSource):
    name = "goes_euvs"
    display_name = "GOES EUVS (EUV/FUV line irradiance)"
    # channel words (mg ii, lyman alpha) deliberately aren't keywords: they
    # select a channel via parse_extras once "euvs" has routed here, and they
    # would otherwise hijack queries meant for EVE or EUI Lyman-α.
    keywords = ["euvs", "goes euvs", "euv sensor", "euv"]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        extras: dict = {}
        consumed: list[str] = []

        # 14 upwards: EUVS flew on GOES-13 too, but nothing from it was
        # reduced to L2, so 13 is left to be read as part of a date
        sat, text = satellite_in_query(ql, 14, 19)
        if sat is not None:
            extras["satellite"] = sat
            consumed.append(text)

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
        channel = extras.get("channel")

        # A named GOES 13-15 spacecraft picks the legacy archive whatever the
        # date, because the two fleets overlap: GOES-15 EUVS ran until
        # 2020-03-04, three years into the GOES-R era.
        if sat in LEGACY_SATS or (sat is None and start < _iso(GOES_R_START, start)):
            return self._fetch_legacy(sat, start, end, channel)

        note = None
        if sat is None:
            sat = primary_satellite(start)
            note = (f"Using GOES-{sat} (primary for this date); "
                    f"say e.g. 'goes-18 euvs' to choose another satellite.")

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

    # -- legacy backend (GOES 13-15, NCEI science archive) ----------------

    def _fetch_legacy(self, sat: int | None, start: datetime, end: datetime,
                      channel: str | None) -> PlotSpec:
        notes = []
        if sat is None:
            sat = legacy_satellite(start)
            if sat is None:
                raise RuntimeError(
                    "GOES EUVS science data starts 2009-09-01 (GOES-14) and "
                    "2010-03-25 (GOES-15). For earlier EUV irradiance try "
                    "SDO EVE (from 2010) or SOHO."
                )
            notes.append(f"Using GOES-{sat}; say e.g. 'goes-14 euvs' to choose "
                         f"the other legacy spacecraft.")
        span_start, span_end = LEGACY_SATS[sat]
        check_mission_span(start, end, span_start, span_end, f"GOES-{sat} EUVS")

        if channel and channel != "irr_1216":
            label = CHANNELS[channel][0]
            raise RuntimeError(
                f"GOES-{sat} EUVS has no {label} data: the pre-2017 L2 archive "
                f"populates Lyman-α only (every 30.4 nm value in it is fill). "
                f"Try 'eve {label.split()[0]} …' for SDO EVE, or ask for a date "
                f"from 2017-02-07 for GOES-R EUVS."
            )
        if channel is None:
            notes.append("Pre-2017 EUVS is Lyman-α only — the archive's 30.4 nm "
                         "channel is empty. For 30.4 nm try SDO EVE.")

        df = self._read_legacy_days(sat, start, end)
        df = downsample_df(clip_df(df, start, end), start, end)
        series = df_to_series(df, {LEGACY_VAR: LEGACY_LABEL})
        if not series or not series[0]["values"]:
            raise RuntimeError(
                f"No good GOES-{sat} EUVS samples in this interval (the archive "
                f"flags eclipses, lunar transits and calibrations, and those are "
                f"dropped)."
            )
        return {
            "title": f"GOES-{sat} EUVS {LEGACY_LABEL}  "
                     f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": series,
            # Lyman-α varies by a few percent over a day, so a log axis would
            # render a flat line with unreadable ticks; the GOES-R panels stay
            # logarithmic because they span decades across seven channels.
            "yaxis": {"label": "Irradiance (W m⁻²)", "type": "linear"},
            "xaxis": {"label": "Time (UT)"},
            "sampling": df.attrs.get("sampling"),
            "provenance": f"GOES-{sat} EUVS L2 {LEGACY_CADENCE_S} s irradiance, "
                          f"science quality, as observed (not 1-AU normalised) "
                          f"— NOAA NCEI",
            **({"note": " ".join(notes)} if notes else {}),
        }

    def _read_legacy_days(self, sat: int, start: datetime, end: datetime):
        """Concatenate the daily files covering [start, end] into a dataframe."""
        import h5netcdf
        import pandas as pd

        times: list[datetime] = []
        values: list[float] = []
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        found_any = False
        while day < end:
            try:
                path = self._legacy_day_file(sat, day)
            except Exception:
                day += timedelta(days=1)
                continue
            found_any = True
            with h5netcdf.File(str(path), "r") as f:
                secs = np.asarray(f.variables[LEGACY_TIME][:], dtype=float)
                vals = np.asarray(f.variables[LEGACY_VAR][:], dtype=float)
                # flag 0 is good_data; the rest mark eclipse, lunar transit,
                # calibration, off-pointing and bad data, all of which put
                # spurious dropouts into the light curve
                flags = np.asarray(f.variables[LEGACY_FLAG][:], dtype=float)
                keep = (flags == 0) & np.isfinite(vals) & (vals > 0) & np.isfinite(secs)
                times += [J2000 + timedelta(seconds=s) for s in secs[keep]]
                values += [float(v) for v in vals[keep]]
            day += timedelta(days=1)
        if not found_any:
            raise RuntimeError(
                f"No GOES-{sat} EUVS files on NCEI for this interval. "
                f"GOES-14's coverage in particular is a few scattered campaigns, "
                f"not a continuous span."
            )
        index = pd.DatetimeIndex([t.replace(tzinfo=None) for t in times])
        return pd.DataFrame({LEGACY_VAR: values}, index=index).sort_index()

    def _legacy_day_file(self, sat: int, day: datetime):
        base = f"{NCEI_LEGACY}/goes{sat}/geuv-l2-ir10s/{day:%Y}/{day:%m}"
        pattern = rf"sci_geuv-l2-ir10s_g{sat}_d{day:%Y%m%d}_v[\d-]+\.nc"
        names = re.findall(pattern, listing(f"{base}/"))
        if not names:
            raise FileNotFoundError(f"no legacy EUVS file for g{sat} {day:%Y-%m-%d}")
        name = sorted(set(names))[-1]
        return cached_download(f"{base}/{name}", name)

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
