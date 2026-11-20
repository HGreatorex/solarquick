"""Event-centric query expansion: turn flare queries into composed views.

Runs after parsing, before fetching. Three cases on the `flares` request:

- superlative ("biggest flare since 2014"): resolve via HEK descending class
  search, then build the event view.
- pinpoint ("flare at 2017-09-06 12:02"): resolve the flare nearest the
  parsed time, then build the event view.
- plain list ("flares yesterday"): left untouched.

An event view narrows the time range to the flare's rise–decay window
(padded) and, when the user asked for nothing else, adds companions:
GOES XRS for the light curve, AIA 131+304 cropped to the flare site when
the catalog has a usable position (EIT 195 full-disk for pre-SDO events),
STIX when Solar Orbiter was flying, and the FISM2 spectral movie (raw model
irradiance) for any flare since 2003.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from solarapp.core import hek
from solarapp.core.parser import ParsedQuery, SourceRequest

SDO_ERA = datetime(2010, 5, 1, tzinfo=timezone.utc)
STIX_ERA = datetime(2020, 4, 14, tzinfo=timezone.utc)
FISM_ERA = datetime(2003, 1, 1, tzinfo=timezone.utc)
CROP_FOV = 500.0  # arcsec field of view around the flare site


def expand(parsed: ParsedQuery) -> None:
    """Resolve event queries in place. Raises RuntimeError when unresolvable."""
    req = next((r for r in parsed.requests if r.source == "flares"), None)
    if req is None:
        return

    flare = None
    if req.extras.pop("superlative", False):
        flare = hek.biggest_flare(parsed.start, parsed.end)
        if flare is None:
            raise RuntimeError(
                f"No flares found in the HEK catalog between "
                f"{parsed.start:%Y-%m-%d} and {parsed.end:%Y-%m-%d}."
            )
    elif req.extras.pop("at", False):
        mid = parsed.start + (parsed.end - parsed.start) / 2
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
