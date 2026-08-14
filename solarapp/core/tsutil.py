"""Small helpers shared by the timeseries plugins (mostly sunpy/Fido-backed)."""
from __future__ import annotations

import math
import os
import re
from datetime import datetime

# parfive defaults to no total timeout and a 90 s socket read, so a stalled
# archive connection hangs the whole request: an NCEI stall was measured at 82 s
# before it even reported the 503, and the retries multiplied that. Bound both,
# without overriding a value the operator set deliberately.
os.environ.setdefault("PARFIVE_TOTAL_TIMEOUT", "120")
os.environ.setdefault("PARFIVE_SOCK_READ_TIMEOUT", "30")


# NOTE — a half-open search window does not work here, and the failure is silent.
# Fido's a.Time is inclusive, so a one-day query ending at midnight also matches
# the *next* day's file: every single-day archival query downloads two dailies to
# use one instant of the second. Trimming the end by a microsecond looks like the
# fix but sunpy's NCEI client then matches *nothing* — measured on 2017-09-06,
# inclusive found 2 files and half-open found 0, so archival GOES would have
# broken entirely rather than getting faster.
# If this is worth revisiting, filter the search *results* (drop rows whose start
# is >= end) rather than narrowing the query, and re-verify per client — each
# archive's matching differs.


def check_mission_span(start: datetime, end: datetime, span_start: str,
                       span_end: str | None, mission: str) -> None:
    """Raise a friendly error when the request is entirely outside the mission."""
    s0 = datetime.fromisoformat(span_start).replace(tzinfo=start.tzinfo)
    if end < s0:
        raise RuntimeError(f"{mission} data starts {span_start}.")
    if span_end is not None:
        s1 = datetime.fromisoformat(span_end).replace(tzinfo=start.tzinfo)
        if start > s1:
            raise RuntimeError(f"{mission} stopped observing on {span_end}.")


# A satellite number written after "goes" is only a satellite when the number
# isn't the start of a date: "goes 10 july 2014" asks for 10 July on the primary
# spacecraft, not for GOES-10, and consuming "goes 10" there would strip the day
# before the date parser ever saw it — silently widening the request to a whole
# month. So a following month name (or ordinal suffix) disqualifies the match.
_MONTH_WORD = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_SAT_RE = re.compile(
    rf"\bgoes[-\s]?(\d{{1,2}})\b(?!\s*(?:st|nd|rd|th)\b)(?!\s*(?:{_MONTH_WORD}))",
    re.I,
)


def satellite_in_query(query: str, low: int, high: int) -> tuple[int | None, str | None]:
    """Read an explicit GOES spacecraft out of a query: "goes-15", "goes 15".

    Returns (number, matched_text) so the caller can pass the number to fetch()
    and hand the text back as a consumed token. Numbers outside [low, high] are
    ignored rather than raising: each GOES instrument flew on its own subset of
    the fleet, and a number this source cannot serve is more likely part of the
    date than a request for a spacecraft that never carried the instrument.
    """
    for m in _SAT_RE.finditer(query):
        number = int(m.group(1))
        if low <= number <= high:
            return number, m.group(0)
    return None, None


def pick_satellite(query):
    """Narrow a Fido GOES result to one satellite (the highest number).

    Several GOES spacecraft operate at once, so a pre-GOES-16 search returns
    a row per satellite (2003 gives GOES-10 and GOES-12). Concatenating them
    would interleave two instruments' curves into one series — duplicate
    timestamps and a visibly doubled light curve. The highest number is the
    newest instrument, which is also the usual primary.
    """
    table = next((t for t in query if len(t)), None)
    if table is None or "SatelliteNumber" not in table.colnames or len(table) < 2:
        return query
    best = max(int(row["SatelliteNumber"]) for row in table)
    return table[[i for i, row in enumerate(table)
                  if int(row["SatelliteNumber"]) == best]]


def fetch_files(query, mission: str, retries: int = 3) -> list[str]:
    """Fido.fetch with retry-on-error and a readable failure.

    Archives 503 intermittently (NCEI especially, more so under the
    concurrent fetches a multi-source request makes) — measured roughly one
    failure in three on the 2003 XRS files. parfive collects those in
    Results.errors and re-fetching a Results retries only the failures.
    Without this guard an all-failed download hands sunpy an empty file list,
    which surfaces as the useless "pop from empty list".

    max_splits=1 disables multi-part downloading: these files are tens of KB,
    so splitting them buys nothing and every extra part is another chance for
    a flaky archive to fail the whole download (MultiPartDownloadError).
    """
    from sunpy.net import Fido

    files = Fido.fetch(query, max_splits=1)
    for _ in range(retries):
        if not getattr(files, "errors", None):
            break
        files = Fido.fetch(files, max_splits=1)
    if not files:
        detail = ""
        errors = getattr(files, "errors", None)
        if errors:
            detail = f" ({len(errors)} download(s) failed: {_error_hint(errors[0])})"
        raise RuntimeError(
            f"{mission}: the archive listed files for this interval but none "
            f"could be downloaded{detail}. This is usually a transient archive "
            f"outage — try again shortly."
        )
    return files


def _error_hint(error) -> str:
    """Short human-readable cause from a parfive error tuple/object."""
    response = getattr(error, "response", None)
    status = getattr(response, "status", None)
    if status:
        return f"HTTP {status}"
    exc = getattr(error, "exception", None)
    return type(exc).__name__ if exc is not None else "unknown cause"


def check_max_days(start: datetime, end: datetime, max_days: int, what: str) -> None:
    """Refuse an over-long request, and say what window was understood.

    The span is quoted because a too-long request is often a *misread* date
    rather than a greedy user: "wind 8-may-2024" once parsed as May through
    December, and the bare message "limited to 2 days" gave no hint that the
    single day asked for had become eight months. An error that states the
    interpretation lets the reader see the real fault at a glance.
    """
    days = (end - start).total_seconds() / 86400
    if days <= max_days:
        return
    raise RuntimeError(
        f"{what} requests are limited to {max_days} days, and this query was "
        f"read as {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT "
        f"({days:.1f} days). If that is not the window you meant, the date was "
        f"misread — an ISO date like 2024-05-08 is never ambiguous."
    )


def band_numbers(name: str) -> list[int]:
    """The energy limits written into a band label ("6 - 12 keV" → [6, 12])."""
    return [int(n) for n in re.findall(r"\d+", name)]


def select_bands(names, band: str | None, hard_from: int = 25) -> list[str]:
    """The energy-band labels a `band` extra asks for.

    Labels are matched on their numbers rather than their text because the
    spacing differs between archives and sunpy versions ("6 - 12 keV" vs
    "6-12 keV"). `hard`/`thermal` split the set at `hard_from` keV, which is
    what a solar physicist means by those words. Falls back to every band if
    the selection matches nothing, so a naming change degrades to showing
    more rather than to an empty plot.
    """
    labels = list(names)
    if band is None:
        return labels
    if band == "hard":
        return [c for c in labels if band_numbers(c)[:1] >= [hard_from]] or labels
    if band == "thermal":
        return [c for c in labels if 0 < len(band_numbers(c)) and
                band_numbers(c)[0] < hard_from] or labels
    want = band_numbers(band)
    return [c for c in labels if band_numbers(c)[:2] == want] or labels


def df_to_series(df, columns: dict[str, str], positive_only: bool = True) -> list[dict]:
    """Convert dataframe columns to PlotSpec series.

    columns maps dataframe column name -> display name. Missing columns are
    skipped; NaN (and non-positive values, for log axes) are dropped.
    """
    out = []
    for col, label in columns.items():
        if col not in df:
            continue
        times, values = [], []
        for t, v in df[col].items():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            if positive_only and v <= 0:
                continue
            times.append(t.isoformat() + "+00:00")
            values.append(float(v))
        out.append({"name": label, "times": times, "values": values})
    return out


def clip_df(df, start: datetime, end: datetime):
    """Clip a naive-UTC-indexed dataframe to [start, end]."""
    return df[(df.index >= start.replace(tzinfo=None)) & (df.index <= end.replace(tzinfo=None))]


def downsample_df(df, start: datetime, end: datetime, target_points: int = 3000):
    """Mean-resample so each series carries at most ~target_points points.

    High-cadence sources (GBM 0.256 s, RHESSI 4 s) would otherwise ship
    millions of points to the browser. No-op when already coarse enough.

    Records what it did in `df.attrs["sampling"]` so exports can state whether
    the values are archive cadence or binned — a CSV that silently contained
    4 s means where the archive has 0.256 s samples would be misleading.
    """
    if len(df) <= target_points:
        df.attrs["sampling"] = "archive cadence, not resampled"
        return df
    bin_s = max(1, int((end - start).total_seconds() / target_points))
    out = df.resample(f"{bin_s}s").mean().dropna(how="all")
    # set after the resample: pandas does not reliably propagate .attrs
    out.attrs["sampling"] = (
        f"mean-resampled into {bin_s} s bins from {len(df)} archive samples"
    )
    return out
