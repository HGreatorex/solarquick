"""Event-centric query expansion: turn flare queries into composed views.

Runs after parsing, before fetching. Three cases on the `flares` request:

- superlative ("biggest flare since 2014"): resolve via HEK descending class
  search, then build the event view.
- pinpoint ("flare at 2017-09-06 12:02"): resolve the flare nearest the
  parsed time, then build the event view.
- GOES class ("8 may 2024 01:41 X1.0", "X9.3"): the flare of that class
  nearest the time; over a long span or the whole catalog, one match is the
  event and several become a class-filtered list.
- plain list ("flares yesterday"): left untouched.

An event view narrows the time range to the flare's rise–decay window
(padded) and, when the user asked for nothing else, adds companions:
GOES XRS for the light curve, AIA 131+304 cropped to the flare site when
the catalog has a usable position (EIT 195 full-disk for pre-SDO events),
STIX when Solar Orbiter was flying, the FISM2 spectral movie (raw model
irradiance) for any flare since 2003, and since STEREO (2006-10) a VIEWPOINTS
block, with Solar Orbiter EUI and STEREO-A EUVI images when the site is in
their view and they have an image near the peak.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from solarapp.core import hek
from solarapp.core.parser import ParsedQuery, SourceRequest

SDO_ERA = datetime(2010, 5, 1, tzinfo=timezone.utc)
STIX_ERA = datetime(2020, 4, 14, tzinfo=timezone.utc)
STEREO_ERA = datetime(2006, 10, 27, tzinfo=timezone.utc)
FISM_ERA = datetime(2003, 1, 1, tzinfo=timezone.utc)
CROP_FOV = 500.0  # arcsec field of view around the flare site


def expand(parsed: ParsedQuery) -> None:
    """Resolve event queries in place. Raises RuntimeError when unresolvable."""
    req = next((r for r in parsed.requests if r.source == "flares"), None)
    if req is None:
        return

    flare = None
    goes_class = req.extras.pop("goes_class", None)
    catalog_wide = req.extras.pop("catalog_wide", False)
    at = req.extras.pop("at", False)
    superlative = req.extras.pop("superlative", False)
    if goes_class:
        flare = _class_flare(parsed, req, goes_class, catalog_wide)
        if flare is None:
            return  # several of that class: the filtered list is the answer
    elif superlative:
        flare = hek.biggest_flare(parsed.start, parsed.end)
        if flare is None:
            raise RuntimeError(
                f"No flares found in the HEK catalog between "
                f"{parsed.start:%Y-%m-%d} and {parsed.end:%Y-%m-%d}."
            )
    elif at:
        # "flare at 16:51" parses to the hour from 16:51: the stated instant is
        # its start, not its middle. A wider window (a bare date) means its middle.
        span = parsed.end - parsed.start
        mid = parsed.start if span <= timedelta(hours=1) else parsed.start + span / 2
        flare = hek.flare_near(mid)
        if flare is None:
            raise RuntimeError(
                f"No flare found in the catalog within ±12 h of {mid:%Y-%m-%d %H:%M} UT."
            )
    if flare is None:
        return  # plain flare-list query

    # narrow the request to the event window (generous decay-side padding)
    pad = max((flare.end - flare.start) / 2, timedelta(minutes=15))
    parsed.start = flare.start - pad
    parsed.end = flare.end + 2 * pad
    req.extras["pin_peak"] = flare.peak.isoformat()
    # the flare list itself should not re-filter by class in the narrow window
    req.extras.pop("min_class", None)

    if len(parsed.requests) > 1:
        return  # user chose their own companions ("biggest flare ... and euvs")

    parsed.requests.append(SourceRequest("goes_xrs", {}))
    if flare.peak >= SDO_ERA:
        for channel in ("aia_131", "aia_304"):
            extras: dict = {"channel": channel}
            if flare.has_position:
                extras |= {"center_x": flare.hpc_x, "center_y": flare.hpc_y,
                           "fov": CROP_FOV}
            parsed.requests.append(SourceRequest("sdo_images", extras))
    else:
        parsed.requests.append(SourceRequest("soho_images", {"channel": "eit_195"}))
    if flare.peak >= STIX_ERA:
        parsed.requests.append(SourceRequest("solo_stix", {}))
    if flare.peak >= FISM_ERA:
        parsed.requests.append(SourceRequest("fism", {}))
    if flare.peak >= STEREO_ERA:
        _add_viewpoints(parsed, flare)


def _class_flare(parsed: ParsedQuery, req: SourceRequest, goes_class: str,
                 catalog_wide: bool):
    """The flare a GOES class names ("8 may 2024 01:41 X1.0", "X9.3").

    A time to the minute: the flare of that class nearest it. A day, a longer
    span or the whole catalog: one match is the event, several become the
    class-filtered list (returns None). Catalog magnitudes are rounded and
    were rescaled for GOES-16, so with no exact match a class within 10% is
    used — and the note says so.
    """
    letter = goes_class[0]
    span = parsed.end - parsed.start
    if not catalog_wide and span <= timedelta(hours=1):
        return _nearest_of_class(parsed, parsed.start, goes_class)
    if catalog_wide and letter != "X":
        # X-class rows since 1996 are a few hundred; M-class and below are
        # thousands, too slow for HEK to page through for one answer
        raise RuntimeError(f"{goes_class} flares are too common to search the whole "
                           f"catalog for — add a date or year, e.g. “{goes_class} 2024”.")

    floor = hek.class_from_rank(hek.class_rank(goes_class) * (1 - _CLASS_TOL))
    found = hek.search_flares(parsed.start, parsed.end, min_class=floor)
    exact = [f for f in found if hek.same_class(f.goes_class, goes_class)]
    pool = exact or [f for f in found if hek.near_class(f.goes_class, goes_class, _CLASS_TOL)]
    if not pool:
        if span <= timedelta(days=1):  # a day: the flare may peak just outside it
            return _nearest_of_class(parsed, parsed.start + span / 2, goes_class)
        raise RuntimeError(f"No {goes_class} flare in the catalog between "
                           f"{parsed.start:%Y-%m-%d} and {parsed.end:%Y-%m-%d}.")
    if not exact:
        parsed.notes.append(f"No {goes_class} in the catalog; using flares within 10% "
                            f"({', '.join(sorted({f.goes_class for f in pool}))}).")
    if len(pool) == 1:
        return pool[0]
    req.extras.pop("min_class", None)
    req.extras["class_eq"] = goes_class
    if not exact:
        req.extras["class_tol"] = _CLASS_TOL
    if catalog_wide:  # frame the list on the matches, not 30 years
        parsed.start = pool[0].peak - timedelta(days=1)
        parsed.end = pool[-1].peak + timedelta(days=1)
    parsed.notes.append(f"{len(pool)} {goes_class} flares found — pick one from the list.")
    return None


_CLASS_TOL = 0.10


def _nearest_of_class(parsed: ParsedQuery, ref: datetime, goes_class: str):
    """Flare of that class nearest `ref` (±12 h); else the nearest of its letter."""
    letter = goes_class[0]
    window = timedelta(hours=12)
    found = [f for f in hek.search_flares(ref - window, ref + window, min_class=letter)
             if f.goes_class[:1].upper() == letter]
    exact = [f for f in found if hek.same_class(f.goes_class, goes_class)]
    pool = exact or found
    if not pool:
        raise RuntimeError(f"No {letter}-class flare in the catalog within "
                           f"±12 h of {ref:%Y-%m-%d %H:%M} UT.")
    flare = min(pool, key=lambda f: abs((f.peak - ref).total_seconds()))
    if not exact:
        parsed.notes.append(f"No {goes_class} in the catalog within ±12 h; "
                            f"showing the nearest {letter}-class flare, {flare.label()}.")
    return flare


def _add_viewpoints(parsed: ParsedQuery, flare) -> None:
    from solarapp.core import viewpoints
    from solarapp.sources.viewpoints import flare_site
    site = flare_site(flare) or {}    # unplaced: still positions and STIX, no extra images
    try:   # the other vantage points' images, where they can see the site
        extra = viewpoints.companions(flare.peak, hpc=site.get("hpc"), hgs=site.get("hgs"))
    except Exception:  # noqa: BLE001 — the VIEWPOINTS block reports the failure itself
        extra = []
    # images join the grid before the block, so they sit with AIA
    at = next((i for i, r in enumerate(parsed.requests) if r.source == "solo_stix"), len(parsed.requests))
    for source, extras in reversed(extra):
        parsed.requests.insert(at, SourceRequest(source, extras))
    parsed.requests.append(SourceRequest("viewpoints", {
        "peak": flare.peak.isoformat(), "label": flare.label(), **site}))
