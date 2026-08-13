"""Small helpers shared by sunpy/Fido-backed timeseries plugins."""
from __future__ import annotations

import math
import os
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
