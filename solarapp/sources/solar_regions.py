"""Active regions from the NOAA/USAF Solar Region Summary (SRS).

The SRS is the daily register of what is on the solar disk: every numbered
sunspot region with its heliographic position, area, McIntosh and Hale
classes, plus the spotless plages and the regions about to rotate back into
view. SWPC issues one at 0030 UT describing the previous day at 2400 UT.

Two endpoints, because they dovetail exactly:

- `services.swpc.noaa.gov/text/srs.txt` is the newest issuance only — the
  summary valid at *yesterday* 2400 UT.
- NOAA's NCEI/NGDC archive holds every issuance from 1996 to the day before
  that, one file per day. (`ftp://ftp.swpc.noaa.gov/pub/forecasts/SRS/` holds
  the same recent files, but FTP is why that link "opens something local" —
  browsers dropped the protocol. The HTTPS archive is the same data, all of
  it, and is what sunpy's own SRSClient uses.)

So a requested day D is read from the file *issued* D+1, whose header says
"Locations Valid at D/2400Z". Asking for today therefore has no file yet, and
rather than quietly showing yesterday's regions as today's, the block says so
and reports the validity time it actually has.

Three shapes come out of one plugin:

- one day  → the disk map and the region table for that snapshot;
- a range  → the same for the most recent day in it, over a trend of total
  sunspot area and region counts;
- a range with a region named ("ar 4503 last week") → that one region tracked
  across the days, its path drawn on the disk.

Positions are heliographic (Stonyhurst) and are projected onto the disk with
the solar tilt B0, computed here rather than via sunpy: it is a dozen lines of
arithmetic accurate to 0.005°, and importing sunpy would cost this otherwise
sub-second source about 1.5 s.
"""
from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from solarapp.core.httpclient import session
from solarapp.core.registry import DataSource, PlotSpec, Variable
from solarapp.core.tsutil import check_max_days

LIVE_URL = "https://services.swpc.noaa.gov/text/srs.txt"
ARCHIVE_URL = (
    "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/"
    "solar_region_summaries/{d:%Y}/{d:%m}/{d:%Y%m%d}SRS.txt"
)
# SWPC's warehouse also keeps a whole finished year in one 30 KB tarball
# (ftp://ftp.swpc.noaa.gov/pub/warehouse/YYYY/YYYY_SRS.tar.gz), which looks
# like the obvious way to serve a long range. It is not: Python's FTP client
# takes ~12 s to fetch one, where the same month of per-day files takes ~2 s
# on a good day. Measured, not assumed — worth re-measuring before anyone
# tries it again.
#
# One file per day, ~0.5 s each from NGDC, whose latency varies by a factor of
# three from minute to minute: a month lands in 2–8 s and two rotations in
# 8–25 s. Two rotations is also more than a region's whole passage across the
# visible disk, so nothing scientific is lost by stopping there.
MAX_DAYS = 62
WORKERS = 8
TIMEOUT = 20
# NGDC drops a request or two whenever several arrive together — the whole
# archive is known for it — so a day is only called unreadable after a retry.
RETRIES = 2
RETRY_WAIT = 0.4
# How far back the live file can still be the answer: it carries one issuance,
# and the archive lags it by about a day.
LIVE_REACH = timedelta(days=3)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

_ISSUED = re.compile(r":Issued:\s*(\d{4})\s+([A-Za-z]{3})[a-z]*\s+(\d{1,2})", re.I)
_VALID_AT = re.compile(r"valid\s+at\s+(\d{1,2})/(\d{2})(\d{2})Z", re.I)
_SECTION = re.compile(r"^\s*(IA|II|I)\.\s", re.I)
_LOCATION = re.compile(r"^([NS])(\d{1,2})([EW])(\d{1,3})$", re.I)
_LATITUDE = re.compile(r"^([NS])(\d{1,2})$", re.I)

SPOTS, PLAGE, RETURN = "spots", "plage", "return"
_SECTION_OF = {"I": SPOTS, "IA": PLAGE, "II": RETURN}


# -- the file ------------------------------------------------------------


@dataclass
class Region:
    """One row of an SRS section, in the units the file states."""

    nmbr: str
    section: str
    valid: date
    location: str | None = None
    lat: float | None = None
    lon: float | None = None          # Stonyhurst: west positive, east negative
    carrington: int | None = None
    area: int | None = None           # millionths of a solar hemisphere
    mcintosh: str | None = None       # Z: the three-letter McIntosh class
    ll: int | None = None             # longitudinal extent, degrees
    spots: int | None = None          # NN: number of visible spots
    mag: str | None = None            # Hale/Mount Wilson magnetic class

    @property
    def delta(self) -> bool:
        """A δ configuration — opposite polarities inside one penumbra.

        Worth singling out because it is the one classification in the file
        that predicts flares: essentially every X-class flare comes from one.
        """
        return bool(self.mag and "delta" in self.mag.lower())

    @property
    def complexity(self) -> str:
        """Coarse magnetic complexity, for colouring the disk map."""
        mag = (self.mag or "").lower()
        if "delta" in mag:
            return "delta"
        if "gamma" in mag:
            return "beta-gamma"
        if "beta" in mag:
            return "beta"
        return "alpha"


@dataclass
class Snapshot:
    """One SRS file: the regions on the disk at `valid` 2400 UT."""

    valid: date
    issued: date
    regions: list[Region] = field(default_factory=list)

    def section(self, name: str) -> list[Region]:
        return [r for r in self.regions if r.section == name]


def _int(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return None


def _parse_location(token: str) -> tuple[float, float] | None:
    """"S17W53" → (-17.0, +53.0): latitude, Stonyhurst longitude west-positive."""
    m = _LOCATION.match(token)
    if not m:
        return None
    ns, lat, ew, lon = m.groups()
    return (
        float(lat) * (-1 if ns.upper() == "S" else 1),
        float(lon) * (-1 if ew.upper() == "E" else 1),
    )


def _validity(issued: datetime, text: str) -> date:
    """The day the locations are valid for — stated in the file, not assumed.

    The header gives only a day of the month ("Locations Valid at 13/2400Z"),
    so it is resolved against the issue date; a file is issued the morning
    after the day it describes, which is the only case that ever occurs, but
    reading the number keeps a re-issued or late file honest.
    """
    m = _VALID_AT.search(text)
    default = issued.date() - timedelta(days=1)
    if not m:
        return default
    day = int(m.group(1))
    for back in (1, 0, 2, 3):
        candidate = issued.date() - timedelta(days=back)
        if candidate.day == day:
            return candidate
    return default


def parse_srs(text: str) -> Snapshot:
    """Parse one SRS file into a Snapshot.

    Handles both eras of the format: the pre-2000 files are entirely upper
    case and carry a COMMENT column in section IA, and any file may be a
    "Corrected Copy" or say NONE for a whole section. Classes are normalised
    to title case so a 1998 "BETA-GAMMA" and a 2024 "Beta-Gamma" are the same
    string to everything downstream.
    """
    m = _ISSUED.search(text)
    if not m:
        raise ValueError("not an SRS file (no :Issued: header)")
    year, mon, day = m.groups()
    issued = datetime(int(year), _MONTHS[mon.lower()], int(day), tzinfo=timezone.utc)
    valid = _validity(issued, text)

    snapshot = Snapshot(valid=valid, issued=issued.date())
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ":")):
            continue
        head = _SECTION.match(line)
        if head:
            section = _SECTION_OF[head.group(1).upper()]
            continue
        tokens = line.split()
        # column headers ("Nmbr Location ..."), NONE rows and the NNN
        # terminator all fail this: every data row starts with a region number
        if section is None or not tokens[0].isdigit():
            continue
        region = _parse_row(section, tokens, valid)
        if region is not None:
            snapshot.regions.append(region)
    return snapshot


def _parse_row(section: str, tokens: list[str], valid: date) -> Region | None:
    """One data row. Trailing columns are optional — early files vary."""
    region = Region(nmbr=tokens[0].zfill(4), section=section, valid=valid)

    if section == RETURN:
        # only a latitude and a Carrington longitude: the region is still
        # behind the east limb, so it has no position on the visible disk
        if len(tokens) < 2:
            return None
        m = _LATITUDE.match(tokens[1])
        if not m:
            return None
        region.lat = float(m.group(2)) * (-1 if m.group(1).upper() == "S" else 1)
        region.carrington = _int(tokens[2]) if len(tokens) > 2 else None
        return region

    if len(tokens) < 2:
        return None
    place = _parse_location(tokens[1])
    if place is None:
        return None
    region.location = tokens[1].upper()
    region.lat, region.lon = place
    region.carrington = _int(tokens[2]) if len(tokens) > 2 else None
    if section == PLAGE:  # any further column is a free-text comment
        return region

    region.area = _int(tokens[3]) if len(tokens) > 3 else None
    region.mcintosh = tokens[4].title() if len(tokens) > 4 else None
    region.ll = _int(tokens[5]) if len(tokens) > 5 else None
    region.spots = _int(tokens[6]) if len(tokens) > 6 else None
    if len(tokens) > 7:
        region.mag = "-".join(tokens[7:]).title()
    return region


# -- geometry ------------------------------------------------------------


def julian_day(when: datetime) -> float:
    y, m = when.year, when.month
    d = when.day + (when.hour + when.minute / 60 + when.second / 3600) / 24
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def b0_degrees(when: datetime) -> float:
    """Heliographic latitude of disk centre — the Sun's tilt toward Earth.

    ±7.25° over a year, which moves a polar region by a tenth of a radius on
    the projected disk, so it is worth having. Low-precision solar longitude
    (Astronomical Almanac); agrees with sunpy to 0.005°.
    """
    jd = julian_day(when)
    n = jd - 2451545.0
    mean_lon = math.radians((280.460 + 0.9856474 * n) % 360)
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360)
    true_lon = (mean_lon + math.radians(1.915) * math.sin(anomaly)
                + math.radians(0.020) * math.sin(2 * anomaly))
    node = math.radians((125.04 - 0.052954 * n) % 360)
    # apparent longitude: aberration and nutation in longitude
    apparent = true_lon - math.radians(0.00569) - math.radians(0.00478) * math.sin(node)
    inclination = math.radians(7.25)
    ascending = math.radians(73.6667 + 1.3958333 * (jd - 2396758.0) / 36525.0)
    return math.degrees(math.asin(math.sin(apparent - ascending) * math.sin(inclination)))


def project(lat_deg: float, lon_deg: float, b0_deg: float) -> tuple[float, float]:
    """Heliographic lat/lon → disk coordinates in solar radii, north up, west right."""
    lat, lon, b0 = map(math.radians, (lat_deg, lon_deg, b0_deg))
    x = math.cos(lat) * math.sin(lon)
    y = math.sin(lat) * math.cos(b0) - math.cos(lat) * math.cos(lon) * math.sin(b0)
    return round(x, 4), round(y, 4)


# -- fetching ------------------------------------------------------------


def _get(url: str) -> str | None:
    """The file's text, or None for a 404 — the one answer that means "no file".

    Anything else that is not an SRS file raises, and so gets retried and
    reported as unread. NGDC hands back a 200 HTML courtesy page when it is
    under load, and reading that as "no summary was published that day" is the
    kind of confidently wrong answer that sends someone looking for a solar
    minimum that never happened.
    """
    response = session().get(url, timeout=TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    text = response.text
    if not text.lstrip().startswith(":Product:"):
        raise ValueError("the archive returned something that is not an SRS file")
    return text


def _load_archive(day: date) -> tuple[date, Snapshot | None, str | None]:
    """The summary valid at `day` 2400 UT — the file issued the next morning.

    Returns (day, snapshot, error). NGDC drops requests under concurrency
    often enough that one retry turns most of them into successes; what is
    still unread is reported as unread, never as absent.
    """
    url = ARCHIVE_URL.format(d=day + timedelta(days=1))
    failure = None
    for attempt in range(RETRIES):
        try:
            text = _get(url)
            return day, (parse_srs(text) if text else None), None
        except Exception as e:
            failure = str(e)
            time.sleep(RETRY_WAIT * (attempt + 1))
    return day, None, failure


def _load_live() -> Snapshot | None:
    text = _get(LIVE_URL)
    return parse_srs(text) if text else None




def _days(start: datetime, end: datetime) -> list[date]:
    """Every validity day the request covers; `end` is exclusive by convention."""
    last = (end - timedelta(microseconds=1)).date()
    out, day = [], start.date()
    while day <= last:
        out.append(day)
        day += timedelta(days=1)
    return out


def load_days(days: list[date], today: date
              ) -> tuple[dict[date, Snapshot], Snapshot | None, dict[date, str]]:
    """Fetch every day concurrently, falling back to the live file for the newest.

    Returns (snapshots by validity day, the live snapshot if it was read,
    the days that could not be read and why). A day that is in neither is one
    the archive answered 404 for — genuinely never published. The live
    snapshot is handed back so a request for *today*, which no file can answer
    yet, can be told what the newest summary actually covers.
    """
    snapshots: dict[date, Snapshot] = {}
    unread: dict[date, str] = {}
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(days))) as pool:
        for day, snap, error in pool.map(_load_archive, days):
            if snap is not None:
                snapshots[snap.valid] = snap
            if error is not None:
                unread[day] = error

    live = None
    unresolved = [d for d in days if d not in snapshots]
    if unresolved and max(unresolved) >= today - LIVE_REACH:
        try:
            live = _load_live()
        except Exception as e:
            unread[max(unresolved)] = str(e)
        if live is not None and live.valid in unresolved:
            snapshots[live.valid] = live
            unread.pop(live.valid, None)
    return snapshots, live, unread


# -- the plugin ----------------------------------------------------------

# Region numbers are the file's own four digits (SWPC prints AR 14503 as
# 4503), so both spellings are accepted and normalised to the printed form.
# Only "ar"/"noaa" may introduce a bare number: "regions 2014-06-10" would
# otherwise read its own date as a region number.
_AR = re.compile(r"\b(?:ar|noaa)\s*#?\s*(\d{3,5})\b", re.I)
_AR_REGION = re.compile(r"\bregion\s+#?\s*(\d{3,5})\b", re.I)
_PLAUSIBLE_YEAR = range(1996, 2036)
# Enough to route "ar 4503 yesterday", where nothing else scores at all, and
# deliberately too little to outrank an instrument named alongside it.
_AR_BONUS = 6
_FLARE_SUBJECT = re.compile(r"\bflares?\b", re.I)


def _region_number(query: str) -> tuple[str, str] | None:
    """(four-digit number, matched text) for a region named in `query`."""
    m = _AR.search(query)
    if m is None:
        m = _AR_REGION.search(query)
        if m is not None and int(m.group(1)) in _PLAUSIBLE_YEAR:
            return None  # "region 2014" is a year far more often than a region
    if m is None:
        return None
    return m.group(1)[-4:].zfill(4), m.group(0)


class SolarRegions(DataSource):
    name = "solar_regions"
    display_name = "Active regions (NOAA/USAF SRS)"
    # Bare "region" is deliberately absent: it is a qualifier in far too many
    # sentences ("the polar region") to be a claim on the whole query.
    keywords = [
        "active region", "region summary", "solar region summary", "srs",
        "sunspot region", "sunspot group", "spot group", "noaa region",
        "sunspot area", "region map", "hale class", "magnetic class",
        "mcintosh", "mount wilson",
    ]
    coverage = ("1996-01-01", None)
    summary_rank = 25  # which regions were on the disk is half of "what happened"

    variable_key = "section"
    # Section I (regions with sunspots) is the default and declares no
    # variable of its own on purpose: bare "sunspots" already belongs to
    # SDO/HMI's white-light continuum, and a photograph of the spots is a
    # perfectly good answer to it. "sunspot region", "sunspot area" and the
    # rest are source keywords, so naming this table still reaches it.
    variables = [
        Variable(PLAGE, "plages without spots",
                 ["plage", "h alpha plage", "spotless region"]),
        Variable(RETURN, "regions due to return",
                 ["due to return", "returning region", "far side region"]),
        Variable("delta", "delta regions only",
                 ["delta", "delta class", "magnetically complex"],
                 extras={"only_delta": True}, group="filter"),
    ]

    def score(self, text: str) -> int:
        score = super().score(text)
        if _region_number(text):
            score += _AR_BONUS
        if score and _FLARE_SUBJECT.search(text):
            # "flares from active region 4503" is a question about flares —
            # the region is the qualifier. Withdraw rather than outscore the
            # flare list, which would answer a different question entirely.
            # A query that wants both writes "and", and the parser splits it.
            return 0
        return score

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        extras, consumed = self.variable_extras(query)
        named = _region_number(query)
        if named:
            # consumed so the date parser never sees the number as a year
            extras["ar"], matched = named[0], named[1]
            consumed.append(matched)
        return extras, consumed

    # -- fetch ------------------------------------------------------------

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)
        # one file per day, so the span is the cost; two solar rotations is
        # already more than a region's whole life on the visible disk
        check_max_days(start, end, MAX_DAYS, "Solar Region Summary")
        days = _days(start, end)

        today = datetime.now(timezone.utc).date()
        snapshots, live, unread = load_days(days, today)
        notes: list[str] = []

        if not snapshots:
            # The commonest case by far: "active regions today", asked before
            # tonight's summary exists. Show the newest one there is and say
            # what it is valid for, rather than passing it off as today's.
            if live is not None and days[-1] >= live.valid:
                snapshots[live.valid] = live
                notes.append(
                    f"No summary is valid at {days[-1]:%Y-%m-%d} 2400 UT yet — SWPC "
                    f"issues each one at 0030 UT the following morning. Showing the "
                    f"latest, valid {live.valid:%Y-%m-%d} 2400 UT."
                )
            elif unread:
                raise RuntimeError(
                    f"Could not read the Solar Region Summary for "
                    f"{days[0]:%Y-%m-%d} – {days[-1]:%Y-%m-%d} — "
                    f"{next(iter(unread.values()))} (NGDC does this under load; "
                    f"the same query usually works on a second try)."
                )
            else:
                raise RuntimeError(
                    f"No Solar Region Summary published for "
                    f"{days[0]:%Y-%m-%d} – {days[-1]:%Y-%m-%d}."
                )
        else:
            # Two different silences, kept apart: a 404 means SWPC published
            # nothing that day, and an unread day means the archive refused
            # us. Reporting the second as the first would invent a quiet Sun.
            gaps = sorted(d for d in days if d not in snapshots and d not in unread)
            if gaps:
                notes.append(f"No summary was published for {_list_days(gaps)}.")
            still_unread = sorted(d for d in days if d not in snapshots and d in unread)
            if still_unread:
                notes.append(
                    f"Could not read {len(still_unread)} of {len(days)} days from the "
                    f"NGDC archive ({_list_days(still_unread)}) — they are missing "
                    f"from the trend, not from the Sun."
                )

        ar = extras.get("ar")
        if ar:
            return self._track(snapshots, ar, extras, notes)
        return self._snapshot(snapshots, days, extras, notes)

    # -- the three shapes -------------------------------------------------

    def _snapshot(self, snapshots, days, extras, notes) -> PlotSpec:
        """The disk on the most recent day fetched, over a trend if there is one."""
        section = extras.get("section", SPOTS)
        only_delta = extras.get("only_delta", False)
        ref = max(snapshots)

        rows = [_row(r, ref) for day in sorted(snapshots)
                for r in snapshots[day].regions]
        if only_delta:
            # plages and far-side regions have no magnetic classification at
            # all, so they are not "delta regions the export happens to
            # include" — they are a different question
            rows = [r for r in rows if r["delta"]]

        shown = [r for r in rows if r["valid"] == ref.isoformat()
                 and r["section"] == section]
        if only_delta and not shown and section == SPOTS:
            notes.append(f"No delta regions on the disk on {ref:%Y-%m-%d}.")

        spec: PlotSpec = {
            "kind": "regions",
            "title": _title(snapshots, ref, section, only_delta),
            "regions": rows,
            "ref_valid": ref.isoformat(),
            "table_section": section,
            "provenance": _PROVENANCE,
        }
        if len(snapshots) > 1:
            spec["trend"] = _trend(snapshots)
        if notes:
            spec["note"] = " ".join(notes)
        return spec

    def _track(self, snapshots, ar, extras, notes) -> PlotSpec:
        """One region, day by day: its path across the disk and how it grew."""
        rows, points = [], []
        for day in sorted(snapshots):
            for region in snapshots[day].regions:
                if region.nmbr != ar:
                    continue
                row = _row(region, day)
                rows.append(row)
                if row["x"] is not None:
                    points.append(row)

        if not rows:
            span = f"{min(snapshots):%Y-%m-%d} – {max(snapshots):%Y-%m-%d}"
            raise RuntimeError(
                f"AR {ar} does not appear in any summary for {span}. Region numbers "
                f"are the four digits SWPC prints (14503 and 4503 are the same one)."
            )

        first, last = rows[0], rows[-1]
        spotted = [r for r in rows if r["section"] == SPOTS]
        spec: PlotSpec = {
            "kind": "regions",
            "title": (f"AR {ar}  {first['valid']} – {last['valid']}  ·  "
                      f"{len(rows)} daily snapshots"),
            "regions": rows,
            "ref_valid": last["valid"],
            # every row is the same region, so the table shows all of them
            # whatever section each day filed it under — a region that has
            # rotated off the west limb reappears under "due to return"
            "table_section": SPOTS,
            "track": {"nmbr": ar,
                      "x": [p["x"] for p in points],
                      "y": [p["y"] for p in points],
                      "labels": [p["valid"] for p in points]},
            "provenance": _PROVENANCE,
        }
        if len(spotted) > 1:
            spec["trend"] = _region_trend(ar, spotted)
        if not spotted:
            notes.append(f"AR {ar} is listed without spots on every day in range.")
        if notes:
            spec["note"] = " ".join(notes)
        return spec


_PROVENANCE = ("Joint USAF/NOAA Solar Region Summary — SWPC (latest) / "
               "NOAA NCEI archive; positions valid 2400 UT")


def _list_days(days: list[date]) -> str:
    shown = ", ".join(f"{d:%Y-%m-%d}" for d in days[:3])
    return shown + (" …" if len(days) > 3 else "")


def _title(snapshots: dict, ref: date, section: str, only_delta: bool) -> str:
    what = {SPOTS: "Active regions", PLAGE: "Plages without spots",
            RETURN: "Regions due to return"}[section]
    if only_delta and section == SPOTS:
        what = "Delta regions"
    if len(snapshots) > 1:
        return (f"{what}  {min(snapshots):%Y-%m-%d} – {max(snapshots):%Y-%m-%d}"
                f"  ·  disk valid {ref:%Y-%m-%d} 2400 UT")
    return f"{what}  valid {ref:%Y-%m-%d} 2400 UT"


def _row(region: Region, ref: date) -> dict:
    """One region as the frontend and the CSV both want it."""
    x = y = None
    behind = False
    if region.lat is not None and region.lon is not None:
        # projected for the day the position is valid, not the day in view:
        # B0 moves by a degree over a fortnight
        x, y = project(region.lat, region.lon,
                       b0_degrees(datetime(region.valid.year, region.valid.month,
                                           region.valid.day, tzinfo=timezone.utc)))
        # A region reported past 90° of longitude has rotated over the limb;
        # its orthographic projection lands back *inside* the disk, which
        # would draw a far-side region as if it were facing us. Pin it to the
        # limb instead and say so, so the map cannot imply a visible spot.
        behind = abs(region.lon) > 90
        if behind:
            radius = math.hypot(x, y) or 1.0
            x, y = round(x / radius, 4), round(y / radius, 4)
    return {
        "nmbr": region.nmbr,
        "section": region.section,
        "valid": region.valid.isoformat(),
        "location": region.location,
        "lat": region.lat,
        "lon": region.lon,
        "x": x,
        "y": y,
        "behind": behind,
        "carrington": region.carrington,
        "area": region.area,
        "mcintosh": region.mcintosh,
        "ll": region.ll,
        "spots": region.spots,
        "mag": region.mag,
        "delta": region.delta,
        "complexity": region.complexity,
        # click-through: a region in the table becomes its own history
        "query": (f"ar {region.nmbr} from {region.valid - timedelta(days=6):%Y-%m-%d} "
                  f"to {region.valid:%Y-%m-%d}"),
    }


def _instant(day: date) -> str:
    """The ISO instant the snapshot is valid for: `day` 2400 UT is the next 0000.

    The exact instant, not the day it describes, because the trend is a time
    axis and 2400 UT of the 6th *is* midnight on the 7th. It does mean each
    point sits on the tick after the day it summarises, so every trend title
    says which convention it is drawn in.
    """
    return (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            + timedelta(days=1)).isoformat()


def _trend(snapshots: dict[date, Snapshot]) -> list[dict]:
    """Total area and region counts per day — two panels, two sets of units."""
    days = sorted(snapshots)
    times = [_instant(d) for d in days]
    spots = {d: snapshots[d].section(SPOTS) for d in days}
    return [
        {
            "title": "Sunspot area  ·  daily 2400 UT snapshots",
            "series": [{"name": "Total area",
                        "times": times,
                        "values": [sum(r.area or 0 for r in spots[d]) for d in days]}],
            "yaxis": {"label": "millionths of a hemisphere"},
        },
        {
            "title": "Sunspots  ·  daily 2400 UT snapshots",
            "series": [{"name": "Sunspots", "times": times,
                        "values": [sum(r.spots or 0 for r in spots[d]) for d in days]}],
            "yaxis": {"label": "count"},
        },
        # A panel of its own, not stacked with the spot count: there are ten
        # regions to a hundred spots, and on one axis the region and delta
        # counts flatten into the baseline — which is the series a reader
        # cares most about, since the delta count is the flare risk.
        {
            "title": "Regions  ·  daily 2400 UT snapshots",
            "series": [
                {"name": "Numbered regions", "times": times,
                 "values": [len(spots[d]) for d in days]},
                {"name": "Delta regions", "times": times,
                 "values": [sum(1 for r in spots[d] if r.delta) for d in days]},
            ],
            "yaxis": {"label": "count"},
        },
    ]


def _region_trend(ar: str, rows: list[dict]) -> list[dict]:
    times = [_instant(date.fromisoformat(r["valid"])) for r in rows]
    return [
        {
            "title": f"AR {ar} area  ·  daily 2400 UT snapshots",
            "series": [{"name": "Area", "times": times,
                        "values": [r["area"] for r in rows]}],
            "yaxis": {"label": "millionths of a hemisphere"},
        },
        {
            "title": f"AR {ar} spots  ·  daily 2400 UT snapshots",
            "series": [{"name": "Sunspots", "times": times,
                        "values": [r["spots"] for r in rows]}],
            "yaxis": {"label": "count"},
        },
    ]
