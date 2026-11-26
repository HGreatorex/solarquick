"""VIEWPOINTS: one flare as Earth, Solar Orbiter and STEREO-A see it.

The event view adds this block for any flare since STEREO's launch (2006-10)
that can be placed. Asked for directly ("viewpoints 2024-05-14 16:00 to
18:00") it takes the largest flare in the window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from solarapp.core import hek, viewpoints
from solarapp.core.registry import DataSource, PlotSpec


def region_site(flare) -> tuple[float, float, str] | None:
    """Stonyhurst (lat, lon) of the flare's region at the peak, from the most recent
    Solar Region Summary that lists it, rotated forward — past the limb if need be:
    limb flares are the ones a second viewpoint matters for."""
    from solarapp.sources import solar_regions as sr
    if not flare.ar:
        return None
    want = flare.ar % 10000
    days = [flare.peak.date() - timedelta(days=b) for b in (1, 2, 3, 0)]
    snaps, _, _ = sr.load_days(sorted(days), datetime.now(timezone.utc).date())
    for valid in days:
        snap = snaps.get(valid)
        reg = snap and next((r for r in snap.section(sr.SPOTS) + snap.section(sr.PLAGE)
                             if sr._ar_number(r.nmbr) == want and r.lat is not None
                             and r.lon is not None), None)
        if reg:
            ref = datetime(valid.year, valid.month, valid.day, tzinfo=timezone.utc) + timedelta(days=1)
            lon = reg.lon + sr.synodic_rate(reg.lat) * (flare.peak - ref).total_seconds() / 86400
            return reg.lat, lon, f"AR {reg.nmbr} in the {valid:%Y-%m-%d} region summary, rotated to the peak"
    return None


def flare_site(flare) -> dict | None:
    """{hpc: (x″, y″)} from the catalogue, else {hgs: (lat, lon), estimated: why}."""
    if flare.has_position:
        return {"hpc": (flare.hpc_x, flare.hpc_y)}
    try:
        est = region_site(flare)
    except Exception:  # noqa: BLE001
        est = None
    return {"hgs": est[:2], "estimated": est[2]} if est else None


class Viewpoints(DataSource):
    name = "viewpoints"
    display_name = "Viewpoints (Earth / Solar Orbiter / STEREO-A)"
    keywords = ["viewpoints", "viewpoint", "multi viewpoint", "observer geometry"]
    coverage = ("2006-10-27", None)

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)
        if extras.get("peak"):
            t, label = datetime.fromisoformat(extras["peak"]), extras.get("label", "flare")
            site = {k: extras[k] for k in ("hpc", "hgs", "estimated") if extras.get(k)}
        else:
            flare = hek.biggest_flare(start, end)
            if flare is None:
                raise RuntimeError(f"No flare in the catalogue between {start:%Y-%m-%d %H:%M} "
                                   f"and {end:%Y-%m-%d %H:%M} UT.")
            site = flare_site(flare) or {}
            t, label = flare.peak, flare.label()
        return viewpoints.build(t, label, window=(start, end), **site)
