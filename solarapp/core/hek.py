"""HEK flare-catalog access and GOES-class arithmetic.

Flare records come from the Heliophysics Event Knowledgebase (LMSAL) via
sunpy's HEK client, filtered to the SWPC/GOES flare list (one provider →
no cross-provider duplicates). GOES class strings ("M5.2") order via
class_rank(): letter decade × 10^index + magnitude.

"Biggest flare since X" searches descending by class (X → M → C → B) so the
result set stays small even over decade-long windows (130 X-class rows for
2014–2026 ≈ 20 s; unfiltered would be tens of thousands).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CLASS_LETTERS = "ABCMX"


def class_rank(goes_class: str) -> float:
    """Sortable magnitude for a GOES class string ("M5.2" → 4e-5ish scale).

    Returns 0 for missing/malformed classes so they sort last.
    """
    if not goes_class:
        return 0.0
    letter = goes_class[0].upper()
    if letter not in CLASS_LETTERS:
        return 0.0
    try:
        mag = float(goes_class[1:]) if len(goes_class) > 1 else 1.0
    except ValueError:
        mag = 1.0
    return 10.0 ** CLASS_LETTERS.index(letter) * mag


@dataclass
class Flare:
    goes_class: str
    start: datetime
    peak: datetime
    end: datetime
    hpc_x: float
    hpc_y: float
    ar: int

    @property
    def rank(self) -> float:
        return class_rank(self.goes_class)

    @property
    def has_position(self) -> bool:
        # SWPC frequently reports (0, y) or (0, 0) when the location is unknown
        return abs(self.hpc_x) > 1.0 and math.hypot(self.hpc_x, self.hpc_y) < 1200.0

    def label(self) -> str:
        return f"{self.goes_class or '?'} {self.peak:%Y-%m-%d %H:%M} UT"


def _utc(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", ""))
    return dt.replace(tzinfo=timezone.utc)


def search_flares(start: datetime, end: datetime, min_class: str | None = None) -> list[Flare]:
    """SWPC/GOES flare list for [start, end], optionally at/above min_class."""
    from sunpy.net import Fido
    from sunpy.net import attrs as a

    terms = [a.Time(start, end), a.hek.EventType("FL"), a.hek.OBS.Observatory == "GOES"]
    if min_class:
        cls = min_class.upper()
        if len(cls) == 1:
            cls += "1.0"
        terms.append(a.hek.FL.GOESCls >= cls)
    result = Fido.search(*terms)
    table = result["hek"]

    flares = []
    for row in table:
        try:
            flares.append(Flare(
                # HEK sometimes stores a bare decimal point ("X17." for X17.2)
                goes_class=str(row["fl_goescls"] or "").strip().rstrip("."),
                start=_utc(row["event_starttime"]),
                peak=_utc(row["event_peaktime"] or row["event_starttime"]),
                end=_utc(row["event_endtime"] or row["event_starttime"]),
                hpc_x=float(row["hpc_x"] or 0.0),
                hpc_y=float(row["hpc_y"] or 0.0),
                ar=int(row["ar_noaanum"] or 0),
            ))
        except (TypeError, ValueError):
            continue
    flares.sort(key=lambda f: f.peak)
    return flares


def biggest_flare(start: datetime, end: datetime) -> Flare | None:
    """Largest flare in the window, searching class bands X → M → C → B."""
    for band in ("X", "M", "C", "B"):
        flares = search_flares(start, end, min_class=band)
        if flares:
            return max(flares, key=lambda f: (f.rank, f.peak))
    return None


def flare_near(t: datetime, window: timedelta = timedelta(hours=12)) -> Flare | None:
    """The flare whose peak is closest to t (searched within ±window)."""
    flares = search_flares(t - window, t + window)
    if not flares:
        return None
    return min(flares, key=lambda f: abs((f.peak - t).total_seconds()))
