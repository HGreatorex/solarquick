"""Turn a PlotSpec into a downloadable CSV.

The browser posts back the spec it already holds rather than the server
re-running the query: the export is then guaranteed to be the same numbers as
the figure on screen, and slow archives are not hit twice.

Every CSV carries a commented header with the title, the query that produced
it, the data provenance, and — where the source reports it — whether the values
are archive cadence or binned. An export that looks like raw instrument data but
is silently mean-resampled would be worse than no export at all.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any

PlotSpec = dict[str, Any]


class NotExportable(Exception):
    """The spec has no tabular form (e.g. an image filmstrip)."""


def _header(spec: PlotSpec, query: str | None, extra: dict[str, str] | None = None) -> list[str]:
    fields = {
        "Title": spec.get("title", ""),
        "Query": query or "",
        "Source": spec.get("provenance", ""),
        "Sampling": spec.get("sampling") or "",
        "Note": spec.get("note", ""),
        "Exported": f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} by SolarQuick",
    }
    fields.update(extra or {})
    return [f"# {k}: {v}" for k, v in fields.items() if v]


def _slug(text: str, fallback: str = "export") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug[:60] or fallback


def filename(spec: PlotSpec, fmt: str) -> str:
    """A descriptive download name, e.g. solarquick_goes_xrs_2026-08-11.csv."""
    title = spec.get("title", "")
    date = ""
    m = re.search(r"\d{4}-\d{2}-\d{2}", title)
    if m:
        date = m.group(0)
    # the title's leading words name the instrument; drop the time range
    name = _slug(title.split("  ")[0] if "  " in title else title, "export")
    parts = [p for p in ("solarquick", name, date) if p]
    return "_".join(parts) + f".{fmt}"


# -- per-kind writers ----------------------------------------------------

def _timeseries_rows(spec: PlotSpec) -> tuple[str, list[str], list[list]]:
    """Align every series onto one x column (series can have differing grids)."""
    series = spec.get("series") or []
    if not series:
        raise NotExportable("this result has no data series to export")
    use_time = any(s.get("times") for s in series)
    x_label = "time_utc" if use_time else _slug(spec.get("xaxis", {}).get("label", "x"), "x")

    columns = [s.get("name", f"series_{i}") for i, s in enumerate(series)]
    table: dict[Any, list] = {}
    for i, s in enumerate(series):
        xs = s.get("times") if use_time else s.get("x")
        for x, y in zip(xs or [], s.get("values") or []):
            table.setdefault(x, [None] * len(series))[i] = y

    rows = [[x, *vals] for x, vals in sorted(table.items())]
    return x_label, columns, rows


def _spectrogram_rows(spec: PlotSpec) -> tuple[str, list[str], list[list]]:
    """One row per frequency, one column per time sample."""
    times, freqs, z = spec.get("times") or [], spec.get("freqs") or [], spec.get("z") or []
    if not times or not freqs:
        raise NotExportable("this spectrogram has no data to export")
    label = _slug(spec.get("yaxis", {}).get("label", "frequency"), "frequency")
    rows = [[f, *(z[i] if i < len(z) else [])] for i, f in enumerate(freqs)]
    return label, list(times), rows


def _events_rows(spec: PlotSpec) -> tuple[str, list[str], list[list]]:
    events = spec.get("events") or []
    if not events:
        raise NotExportable("no events in this result to export")
    cols = ["start_utc", "peak_utc", "end_utc", "hpc_x_arcsec", "hpc_y_arcsec", "noaa_ar"]
    rows = [[e.get("class"), e.get("start"), e.get("peak"), e.get("end"),
             e.get("x"), e.get("y"), e.get("ar")] for e in events]
    return "goes_class", cols, rows


def _positions_rows(spec: PlotSpec) -> tuple[str, list[str], list[list]]:
    bodies = spec.get("bodies") or []
    if not bodies:
        raise NotExportable("no positions in this result to export")
    cols = ["type", "x_au", "y_au", "r_au", "stonyhurst_lon_deg"]
    rows = [[b.get("name"), b.get("type"), b.get("x"), b.get("y"),
             b.get("r"), b.get("lon_deg")] for b in bodies]
    return "body", cols, rows


def _earthmap_rows(spec: PlotSpec) -> tuple[str, list[str], list[list]]:
    """The subsolar point, plus its track when the query spanned days."""
    rows = [[p.get("name"), p.get("lat"), p.get("lon")] for p in spec.get("points") or []]
    track = spec.get("track")
    if track:
        for lat, lon in zip(track.get("lat", []), track.get("lon", [])):
            rows.append(["subsolar track", lat, lon])
    if not rows:
        raise NotExportable("no point geometry in this result to export")
    return "point", ["latitude_deg", "longitude_deg"], rows


def _regions_rows(spec: PlotSpec) -> tuple[str, list[str], list[list]]:
    """Every region of every day fetched, not just the day on the disk map.

    The map and the table show one snapshot, but the query covered a span and
    the block carries all of it — exporting only the drawn day would quietly
    turn a month of active-region history into a single row.
    """
    regions = spec.get("regions") or []
    if not regions:
        raise NotExportable("no regions in this result to export")
    cols = ["valid_utc", "section", "location", "latitude_deg",
            "stonyhurst_lon_deg", "carrington_lon_deg", "area_uhem",
            "mcintosh_class", "longitudinal_extent_deg", "spots", "mag_type"]
    rows = [[r.get("nmbr"), r.get("valid"), r.get("section"), r.get("location"),
             r.get("lat"), r.get("lon"), r.get("carrington"), r.get("area"),
             r.get("mcintosh"), r.get("ll"), r.get("spots"), r.get("mag")]
            for r in regions]
    return "region", cols, rows


_WRITERS = {
    "spectrogram": _spectrogram_rows,
    "events": _events_rows,
    "positions": _positions_rows,
    "earthmap": _earthmap_rows,
    "regions": _regions_rows,
}


def spec_to_csv(spec: PlotSpec, query: str | None = None) -> str:
    """Render one PlotSpec block as CSV. Raises NotExportable for images."""
    kind = spec.get("kind")
    if kind == "images":
        raise NotExportable(
            "Image results have no tabular data — use the frame download button "
            "to save the PNGs, or query the underlying instrument's timeseries."
        )
    if kind == "multi":
        raise NotExportable("Export each result block separately.")

    if kind == "panels":
        panels = spec.get("panels") or []
        if not panels:
            raise NotExportable("no panels in this result to export")
        # one CSV per panel, concatenated with a blank line between blocks:
        # keeps differing axes honest instead of forcing them into one grid
        parts = []
        for panel in panels:
            merged = {**panel}
            merged.setdefault("provenance", spec.get("provenance"))
            merged.setdefault("sampling", spec.get("sampling"))
            parts.append(spec_to_csv(merged, query))
        return "\n".join(parts)

    writer = _WRITERS.get(kind, _timeseries_rows)
    x_label, columns, rows = writer(spec)

    buf = io.StringIO()
    for line in _header(spec, query, {"Rows": str(len(rows))}):
        buf.write(line + "\n")
    out = csv.writer(buf, lineterminator="\n")
    out.writerow([x_label, *columns])
    out.writerows(rows)
    return buf.getvalue()


def blocks(spec: PlotSpec) -> list[PlotSpec]:
    """Flatten a result into the individually exportable blocks."""
    return spec.get("items") or [spec] if spec.get("kind") == "multi" else [spec]
