"""Small helpers shared by sunpy/Fido-backed timeseries plugins."""
from __future__ import annotations

import math
from datetime import datetime


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
    """
    if len(df) <= target_points:
        return df
    bin_s = max(1, int((end - start).total_seconds() / target_points))
    return df.resample(f"{bin_s}s").mean().dropna(how="all")
