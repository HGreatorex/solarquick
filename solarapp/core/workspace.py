"""The two-pane UI's structured request: what the form ticked, made runnable.

The text route reads a sentence; the form already knows its sources, options
and window, so it skips parsing and arrives here as data. Two things happen
that the text route never needed:

- **Nothing ticked is a request too.** It means "show me the day": GOES XRS,
  the flare list (whose sites the UI also draws on a disk), spacecraft
  positions, and two images — AIA 171 and 304, or EIT 171 and 304 before SDO.
  Sources not observing then are dropped rather than left to fail.
- **Image options apply to every image block at once.** `frames` sets the
  shared timeline length; `crop_fov` frames each image around the flare. A
  crop needs a centre, so when a block has none (anything the event view did
  not already place) the largest flare in the window is resolved against HEK
  and its site is used; with no usable position the images stay full disk and
  the note says why — a crop around an arbitrary point would look like a
  flare site and is not one.
"""
from __future__ import annotations

from datetime import datetime, timezone

from solarapp.core import hek
from solarapp.core.helioviewer import HelioviewerImageSource
from solarapp.core.parser import SourceRequest
from solarapp.core.registry import Registry

SDO_ERA = datetime(2010, 5, 1, tzinfo=timezone.utc)
CROP_KEYS = ("center_x", "center_y", "fov")


def default_requests(registry: Registry, start: datetime, end: datetime
                     ) -> list[SourceRequest]:
    if start >= SDO_ERA:
        images = [("sdo_images", {"channel": "aia_171"}),
                  ("sdo_images", {"channel": "aia_304"})]
    else:
        images = [("soho_images", {"channel": "eit_171"}),
                  ("soho_images", {"channel": "eit_304"})]
    wanted = [("goes_xrs", {}), ("flares", {}), *images, ("positions", {})]
    out = []
    for name, extras in wanted:
        src = registry.get(name)
        if src is not None and src.covers(start, end):
            out.append(SourceRequest(name, dict(extras)))
    return out


def is_image(registry: Registry, name: str) -> bool:
    src = registry.get(name)
    return src is not None and getattr(src, "display", "plot") == "images"


def apply_image_options(requests: list[SourceRequest], registry: Registry,
                        start: datetime, end: datetime, frames: int | None = None,
                        crop_fov: float | None = None) -> str | None:
    """Set the shared timeline and crop on every image request; returns a note.

    `crop_fov` None leaves each request's crop as it came (an event view's
    crop survives), 0 means full disk, a positive value crops to that many
    arcsec around the flare.
    """
    images = [r for r in requests if is_image(registry, r.source)]
    hv = [r for r in images if isinstance(registry.get(r.source), HelioviewerImageSource)]
    if frames:
        for r in hv:
            r.extras["frames"] = int(frames)
    if crop_fov is None or not hv:
        return None
    # a coronagraph cropped to a flare site is a view of its occulter
    croppable = [r for r in hv if not str(r.extras.get("channel", "")).startswith("lasco")]
    if not crop_fov:
        for r in hv:
            for k in CROP_KEYS:
                r.extras.pop(k, None)
        return None
    note = None
    if any("center_x" not in r.extras for r in croppable):
        flare = hek.biggest_flare(start, end)
        site = None
        if flare is not None and flare.has_position:
            site = (flare.hpc_x, flare.hpc_y)
        elif flare is not None:  # recent flares: place it on its region
            from solarapp.sources.solar_regions import flare_sites_from_regions
            try:
                est = flare_sites_from_regions([flare]).get(0)
            except Exception:
                est = None
            site = est[:2] if est else None
        if site is None:
            for r in hv:
                for k in CROP_KEYS:
                    r.extras.pop(k, None)
            return ("No flare with a catalogued position in this window — "
                    "images are shown full disk.")
        for r in croppable:
            if "center_x" not in r.extras:
                r.extras |= {"center_x": site[0], "center_y": site[1]}
        note = f"Images cropped to {crop_fov:g}″ around the {flare.label()} site."
    for r in croppable:
        r.extras["fov"] = float(crop_fov)
    return note
