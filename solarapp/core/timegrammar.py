"""Everything that turns the words in a query into one UTC time span.

Split out of parser.py because time is where the variety is: a solar physicist
writes "10 July 2026", "10/07/26", "yesterday afternoon", "last Wednesday",
"a couple of days ago", "from 10 to 12 July", "since 2014", "DOY 150 2024" and
"CR 2250", and every one of them has to land on the same (start, end) pair.

Three rules hold throughout:

- **Times are UTC**, and ambiguous numeric dates are day-first (10/07 is July).
- **Precision sets the width**: "2026" is a year, "July 2026" a month,
  "10 July 2026" a day, "14:30" an hour. Nothing is widened silently — the
  bare-number guard in `span_by_precision` exists because dateutil reads a
  stray "3" as a day-of-month, which would become a whole-year request.
- **A relative word resolves against now, backwards.** Data only exists in the
  past, so a bare weekday or month-day means the most recent one, not the
  upcoming one; "next" is the only forward-looking qualifier.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from dateutil import parser as dtparser

from .hek import GOES_CLASS_RE
from .registry import keyword_regex

# -- shared patterns -----------------------------------------------------

_ISO = r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
RANGE_RE = re.compile(
    rf"(?:from|between)?\s*({_ISO})\s*(?:to|and|until|through|thru|-|–|—)\s*({_ISO})", re.I
)
# words that carry no date information and confuse fuzzy parsing
NOISE = re.compile(
    r"\b(show|me|the|plot|display|give|get|data|for|on|please|quicklook|of|from)\b",
    re.I,
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MONTH_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I
)
# d/m/y and d.m.y, two- or four-digit year — day-first, as everywhere else
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2}|\d{4})\b")
# …except when the year leads: 2024/05/08 is always year-month-day, and
# day-first reading made it 5 August
_YMD_SLASH = re.compile(r"\b(\d{4})[/.](\d{1,2})[/.](\d{1,2})\b")


def _ymd_to_iso(m: re.Match) -> str:
    y, mo, d = (int(g) for g in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else m.group(0)

_UNITS = {"hour": timedelta(hours=1), "day": timedelta(days=1),
          "week": timedelta(weeks=1), "month": timedelta(days=30),
          "year": timedelta(days=365)}

_LAST_N = re.compile(r"\b(?:last|past|previous)\s+(\d+)\s*(hour|day|week|month)s?\b", re.I)
_LAST_ONE = re.compile(r"\b(?:last|past|previous)\s+(hour|day|week|month|year)\b", re.I)
_SINCE = re.compile(r"\b(?:since|after)\s+(.+)$", re.I)
_AGO = re.compile(r"\b(\d+)\s*(hour|day|week|month|year)s?\s+ago\b", re.I)
_THIS_PERIOD = re.compile(r"\bthis\s+(week|month|year)\b", re.I)
_NOW = re.compile(r"\b(?:now|right now|latest|most recent|current|today so far)\b", re.I)
_DOY = re.compile(r"\b(?:doy|day\s+of\s+year)\s*[:#]?\s*(\d{1,3})\b", re.I)
_CARRINGTON = re.compile(r"\b(?:carrington\s+rotation|carrington|cr)\s*[:#]?\s*(\d{3,4})\b", re.I)
# "the 10th", "on the 3rd" — a day of the month with nothing else said
_DAY_OF_MONTH = re.compile(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", re.I)

# -- word numbers --------------------------------------------------------
# "a couple of days ago" is the same request as "2 days ago"; the numeric
# rules below only ever see digits, so the words are rewritten first.
_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "couple": 2, "few": 3, "several": 3,
}
_UNIT_WORDS = "hour|day|week|month|year|fortnight"
_WORD_NUM_RE = re.compile(
    rf"\b({'|'.join(_WORD_NUMBERS)})\b(?=\s+(?:{_UNIT_WORDS})s?\b)", re.I
)
_VAGUE_OF = re.compile(r"\b(couple|few|several)\s+of\b", re.I)


def normalise_numbers(text: str) -> str:
    """Rewrite spelled-out counts and "fortnight" into digits and units."""
    text = _VAGUE_OF.sub(r"\1", text)
    text = re.sub(r"\bfortnights?\b", "2 weeks", text, flags=re.I)
    return _WORD_NUM_RE.sub(lambda m: str(_WORD_NUMBERS[m.group(1).lower()]), text)


# -- weekdays ------------------------------------------------------------
# "sat" and "sun" are deliberately absent: in this app they are far more often
# a satellite and the Sun than Saturday and Sunday.
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1,
    "wednesday": 2, "weds": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sunday": 6,
}
_WEEKDAY_RE = re.compile(
    rf"\b(last|past|previous|this|next|on)?\s*({'|'.join(_WEEKDAYS)})\b", re.I
)

# -- parts of the day ----------------------------------------------------
# Conventional UT windows. They are wide on purpose: someone asking for
# "yesterday morning" wants the morning, not an hour they have to guess.
_PARTS = {
    "morning": (6, 12), "afternoon": (12, 18), "evening": (18, 24),
    "midday": (11, 13), "noon": (11, 13), "lunchtime": (11, 13),
    "midnight": (23, 25), "night": (18, 30),
}
_PART_RE = re.compile(rf"\b({'|'.join(_PARTS)})\b", re.I)
_LAST_NIGHT = re.compile(r"\b(?:last|yesterday)\s+night\b|\btonight\b", re.I)
_WEEKEND = re.compile(r"\b(?:last|past|this|the|over the)?\s*weekend\b", re.I)

# -- time of day ---------------------------------------------------------
# A clock time is "14:30", "14", "2pm" or "2:30 pm". A bare hour is only
# trusted where a keyword marks it as a time ("at 14", "between 10 and 12"),
# because on its own a small number is far more likely to be a wavelength,
# a detector or a flare class than an hour.
_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
_TOD_RANGE = re.compile(
    rf"\b(?:from\s+|between\s+)?{_CLOCK}\s*(?:to|until|through|and|–|—|-)\s*{_CLOCK}"
    r"(?:\s*(?:ut|utc|z))?\b",
    re.I,
)
_TOD_AT = re.compile(rf"\b(?:at|around)\s+{_CLOCK}(?:\s*(?:ut|utc|z))?\b", re.I)
_TOD_BARE = re.compile(rf"(?<![\d:]){_CLOCK}(?:\s*(?:ut|utc|z))?\b", re.I)
# how long "at 14:30" means, when no end time is given
_POINT_WINDOW = timedelta(hours=1)

# Clock times are removed before the *date* is parsed. dateutil would otherwise
# fold "10 July 2026 between 10:00 and 12:00" into a single instant and report
# hour precision, which collapses the span to one hour before the time-of-day
# logic ever runs. Times are re-applied afterwards by refine_time_of_day.
_STRIP_CLOCK = re.compile(
    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?|\b\d{1,2}\s*[ap]\.?m\.?\b", re.I)


def _now() -> datetime:
    """Current UTC instant — one seam, so tests can pin "today"."""
    return datetime.now(timezone.utc)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _day(d: datetime) -> tuple[datetime, datetime]:
    return _midnight(d), _midnight(d) + timedelta(days=1)


# -- clock helpers -------------------------------------------------------


def _clock(hour: str, minute: str | None, meridiem: str | None) -> tuple[int, int] | None:
    """Convert one matched clock group to (hour, minute), or None if impossible."""
    h, m = int(hour), int(minute or 0)
    if m > 59:
        return None
    if meridiem:
        ap = meridiem[0].lower()
        if not 1 <= h <= 12:
            return None
        h = 0 if (ap == "a" and h == 12) else (12 if (ap == "p" and h == 12) else
                                               h + 12 if ap == "p" else h)
    elif h > 23:
        return None
    return h, m


def _is_explicit(minute: str | None, meridiem: str | None) -> bool:
    """True when the digits were unambiguously written as a time."""
    return bool(minute or meridiem)


def refine_time_of_day(query: str, span: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
    """Crop a whole-day span to the time of day the query asked for.

    Understands "14:00 to 15:30", "between 10:00 and 12:00", "10am-2pm",
    "at 14:30", "around 2pm", a bare trailing "yesterday 14:00", and the
    named parts of a day ("yesterday afternoon"). A range whose end precedes
    its start ("22:00 to 02:00") is read as crossing midnight, which is what
    an observer means by it.
    """
    start, end = span
    if end - start != timedelta(days=1):
        return span

    # every candidate is scanned, not just the first: a date elsewhere in the
    # query produces earlier near-misses ("2026-08-01" offers "08-01" to the
    # range pattern), and stopping at those would hide the real time of day
    for m in _TOD_RANGE.finditer(query):
        h1, m1, ap1, h2, m2, ap2 = m.groups()
        # at least one endpoint must be written unmistakably as a time, so
        # "from 3 to 4" (which is far more often a count than a clock) is left
        # alone while "3 to 4pm" and "10:00-12:00" are honoured
        if not (_is_explicit(m1, ap1) or _is_explicit(m2, ap2)):
            continue
        t1, t2 = _clock(h1, m1, ap1), _clock(h2, m2, ap2)
        if not (t1 and t2):
            continue
        # "3 to 4pm" means the afternoon at both ends: an am/pm written once
        # governs the pair. Only taken when it keeps the range in order, so
        # "10 to 2pm" stays 10:00–14:00 rather than becoming 22:00–14:00.
        if ap2 and not ap1:
            borrowed = _clock(h1, m1, ap2)
            if borrowed and borrowed < t2:
                t1 = borrowed
        elif ap1 and not ap2:
            borrowed = _clock(h2, m2, ap1)
            if borrowed and borrowed > t1:
                t2 = borrowed
        s = start.replace(hour=t1[0], minute=t1[1])
        e = start.replace(hour=t2[0], minute=t2[1])
        if e < s:  # crosses midnight
            e += timedelta(days=1)
        if e > s:
            return s, e

    for m in _TOD_AT.finditer(query):
        t = _clock(*m.groups())
        if t:
            s = start.replace(hour=t[0], minute=t[1])
            return s, s + _POINT_WINDOW

    for m in _TOD_BARE.finditer(query):
        if not _is_explicit(m.group(2), m.group(3)):
            continue
        t = _clock(*m.groups())
        if t:
            s = start.replace(hour=t[0], minute=t[1])
            return s, s + _POINT_WINDOW

    m = _PART_RE.search(query)
    if m:  # "yesterday afternoon", "wednesday morning"
        lo, hi = _PARTS[m.group(1).lower()]
        return start + timedelta(hours=lo), start + timedelta(hours=hi)
    return span


# -- calendar spans ------------------------------------------------------


def span_by_precision(text: str, default_year: int | None = None
                      ) -> tuple[datetime, datetime] | None:
    """Fuzzy-parse text into a span whose width matches its precision.

    The text is parsed twice with different defaults; fields that follow the
    default were not in the text, revealing how specific it was:
    "10 july 2026" → that day, "july 2026" → that month, "2026" → that year.

    A year or month name must actually appear: dateutil will read a bare "3"
    (from e.g. a mistyped "lst 3 days") as a day-of-month against today's
    year, which this function would then widen into a whole-year span — a
    silently enormous request. Relative phrasings are handled by the callers
    before this point, so requiring one is safe.
    """
    if not (_YEAR_RE.search(text) or _MONTH_RE.search(text)):
        return None
    base_year = default_year or _now().year
    d_lo = datetime(base_year, 1, 1)
    d_hi = datetime(base_year, 12, 28)
    try:
        p_lo, tok_lo = dtparser.parse(text, fuzzy_with_tokens=True, dayfirst=True, default=d_lo)
        p_hi, _ = dtparser.parse(text, fuzzy_with_tokens=True, dayfirst=True, default=d_hi)
    except (ValueError, OverflowError):
        return None
    # Both parses matching their defaults normally means nothing date-like was
    # found — except when the text names the current year, which is exactly
    # what the defaults are built from. "goes 2026" asked for 2026 in 2026 and
    # used to be rejected as dateless.
    if p_lo == d_lo and p_hi == d_hi and not _YEAR_RE.search(text):
        return None
    s_lo = _utc(p_lo)
    if p_lo.month != p_hi.month:  # only a year was given
        s = s_lo.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return s, s.replace(year=s.year + 1)
    if p_lo.day != p_hi.day:  # month + year, no day
        s = s_lo.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
        return _step_back_a_year(text, (s, nxt))
    if (p_lo.hour, p_lo.minute) != (0, 0):  # a time of day was given
        return s_lo, s_lo + timedelta(hours=1)
    return _step_back_a_year(text, _day(s_lo))


def _step_back_a_year(text: str, span: tuple[datetime, datetime]
                      ) -> tuple[datetime, datetime]:
    """A month-and-day with no year means the most recent one, not next year's.

    dateutil defaults the year to the current one, so on 14 August "5 December"
    resolves four months into the future — a date with no data. When the text
    never stated a year, walk back one.
    """
    if _YEAR_RE.search(text) or span[0] <= _now():
        return span
    return tuple(s.replace(year=s.year - 1) for s in span)


# -- ranges written in words --------------------------------------------
# A date-ish chunk: "10 July 2026", "July 2026", "10/07/26", "2014", "the 10th".
_DATEISH = (
    # the day, month and year of one end may be joined by spaces or hyphens
    # ("8 may 2024", "8-may-2024"), so that a hyphenated date is one date
    r"(?:\d{1,2}(?:st|nd|rd|th)?[\s\-/]+)?"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    # the (?!\d) matters: without it "12 july 2026" reads "20" as the day of
    # the month and leaves "26" behind, so the range ends in the year 20
    r"(?:[\s\-/]+\d{1,2}(?:st|nd|rd|th)?(?!\d))?(?:,?[\s\-/]+\d{4})?"
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
    r"|(?:19|20)\d{2}(?!\d)"
    r"|\d{1,2}(?:st|nd|rd|th)"
)
# What may sit between the two ends of a range. A bare hyphen must be spaced:
# unspaced, it is part of a date far more often than it is a range, and
# "8-may-2024" used to split into "8" and "may-2024" — read as May 2024 through
# the end of the year, an eight-month request from a single-day query.
_SEPARATOR = r"(?:\s*(?:to|until|through|thru|and)\s*|\s*[–—]\s*|\s+-\s+)"
_NATURAL_RANGE = re.compile(rf"(?:from\s+|between\s+)?({_DATEISH}){_SEPARATOR}({_DATEISH})", re.I)
# "10 to 12 July 2026": a bare number is only a date when the other end
# carries a month, which is what keeps "from 3 to 4pm" out of this rule. Here
# an unspaced hyphen is allowed ("10-12 july"), so the right-hand side must
# lead with a day number — that is the whole difference between the range
# "10-12 july" and the date "8-may-2024".
_DAY_AND_MONTH = (
    r"\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"(?:,?\s+\d{4})?"
)
_NUMERIC_LEFT_RANGE = re.compile(
    rf"\b(\d{{1,2}}(?:st|nd|rd|th)?)\s*(?:to|until|through|thru|–|—|-)\s*({_DAY_AND_MONTH})",
    re.I,
)


def natural_range(query: str) -> tuple[tuple[datetime, datetime], str] | None:
    """A range written in words: "from 10 to 12 July 2026", "2014 to 2016".

    Returns the span and the matched text, so the caller can mask it before
    splitting the query on conjunctions — otherwise "between 10 July and 12
    July" would be read as two requested instruments.

    The right-hand side is parsed first and lends its year (and month) to the
    left, which is what makes "10 to 12 July 2026" mean three days in July
    rather than the 10th of some other month.
    """
    for pattern in (_NATURAL_RANGE, _NUMERIC_LEFT_RANGE):
        for m in pattern.finditer(query):
            left, right = m.group(1), m.group(2)
            hi = span_by_precision(right)
            if hi is None:
                continue
            # the right-hand year is lent to the left ("July to August 2014"),
            # then, failing that, its whole date is ("10 to 12 July 2026")
            lo = span_by_precision(left, hi[0].year)
            if lo is None or lo[0] >= hi[1]:
                lo = _span_with_default(left, hi[0])
            if lo is None or lo[0] >= hi[1]:
                continue
            return (lo[0], hi[1]), m.group(0)
    return None


def _span_with_default(text: str, like: datetime) -> tuple[datetime, datetime] | None:
    """Parse a fragment ("10", "the 10th") against another date's year/month."""
    text = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", text, flags=re.I)
    try:
        p = dtparser.parse(text, fuzzy=True, dayfirst=True,
                           default=like.replace(tzinfo=None))
    except (ValueError, OverflowError):
        return None
    return _day(_utc(p))


# -- special calendars ---------------------------------------------------


def _doy_span(query: str) -> tuple[datetime, datetime] | None:
    """"DOY 150 2024" — day-of-year, as flight archives and instrument teams write it."""
    m = _DOY.search(query)
    if not m:
        return None
    doy = int(m.group(1))
    if not 1 <= doy <= 366:
        return None
    y = _YEAR_RE.search(query)
    year = int(y.group(0)) if y else _now().year
    try:
        day = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
    except (ValueError, OverflowError):
        return None
    return _day(day)


def _carrington_span(query: str) -> tuple[datetime, datetime] | None:
    """"CR 2250" — one Carrington rotation, start to start.

    The rotation start times come from sunpy rather than a formula copied in
    here, and the import is deferred: it costs over a second, and only a query
    that says "carrington" ever pays it.
    """
    m = _CARRINGTON.search(query)
    if not m:
        return None
    n = int(m.group(1))
    try:
        from sunpy.coordinates.sun import carrington_rotation_time

        s = carrington_rotation_time(n).to_datetime().replace(tzinfo=timezone.utc)
        e = carrington_rotation_time(n + 1).to_datetime().replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return s, e


# -- relative words ------------------------------------------------------


def _weekday_span(query: str) -> tuple[datetime, datetime] | None:
    """"Wednesday", "last Wednesday", "next Friday" → that whole day.

    A bare weekday means the most recent one *including today*, because that
    is what "xrays Wednesday" means when said on a Wednesday. "last" forces
    the previous week's; "next" is the only qualifier that looks forward.
    """
    m = _WEEKDAY_RE.search(query)
    if not m:
        return None
    qualifier = (m.group(1) or "").lower()
    target = _WEEKDAYS[m.group(2).lower()]
    today = _midnight(_now())
    back = (today.weekday() - target) % 7
    if qualifier == "next":
        ahead = (target - today.weekday()) % 7 or 7
        return _day(today + timedelta(days=ahead))
    if qualifier in ("last", "past", "previous") and back == 0:
        back = 7  # "last Wednesday", said on a Wednesday, is not today
    return _day(today - timedelta(days=back))


def _weekend_span(query: str) -> tuple[datetime, datetime] | None:
    """The most recent Saturday 00:00 → Monday 00:00."""
    if not _WEEKEND.search(query):
        return None
    today = _midnight(_now())
    back = (today.weekday() - 5) % 7  # 5 = Saturday
    sat = today - timedelta(days=back)
    return sat, sat + timedelta(days=2)


def _strip_keywords(q: str, matched_keywords: list[str]) -> str:
    for kw in matched_keywords:
        q = keyword_regex(kw).sub(" ", q)
    return NOISE.sub(" ", q)


def date_text(q: str, matched_keywords: list[str]) -> str:
    """The part of the query that can carry a calendar date."""
    return _STRIP_CLOCK.sub(" ", _strip_keywords(q, matched_keywords))


def extract(query: str, matched_keywords: list[str]) -> tuple[datetime, datetime] | None:
    """The time span `query` asks for, or None if it names no time at all.

    Ordered from the most explicit phrasing to the least, so a query that
    states a date outright is never reinterpreted by a looser rule.
    """
    q = normalise_numbers(query)
    q = GOES_CLASS_RE.sub(" ", q)       # "X1.0" is a flare, never a date
    q = _YMD_SLASH.sub(_ymd_to_iso, q)  # 2024/05/08 → 2024-05-08
    now = _now()
    today = _midnight(now)

    m = RANGE_RE.search(q)
    if m:
        s, e = dtparser.parse(m.group(1)), dtparser.parse(m.group(2))
        if len(m.group(2)) <= 10:  # date-only end -> include the whole day
            e += timedelta(days=1)
        return _utc(s), _utc(e)

    m = re.search(_ISO, q)
    if m:  # single ISO date/datetime — parse unambiguously (never day-first)
        s = _utc(dtparser.parse(m.group(0), yearfirst=True))
        if len(m.group(0)) <= 10:
            return refine_time_of_day(q, (s, s + timedelta(days=1)))
        # "2026-07-10 14:00 to 15:30": bare end time following a full datetime
        tail = re.match(rf"\s*(?:to|until|through|–|—|-)\s*{_CLOCK}\b", q[m.end():], re.I)
        if tail:
            t = _clock(*tail.groups())
            if t:
                e = s.replace(hour=t[0], minute=t[1])
                if e < s:  # crosses midnight
                    e += timedelta(days=1)
                if e > s:
                    return s, e
        return s, s + _POINT_WINDOW

    nat = natural_range(q)
    if nat:
        return nat[0]

    m = _SLASH_DATE.search(q)
    if m:  # 10/07/2026 and 10.07.26 — day first, as stated in the docs
        d, mo, y = (int(g) for g in m.groups())
        y += 2000 if y < 70 else (1900 if y < 100 else 0)
        try:
            return refine_time_of_day(q, _day(datetime(y, mo, d, tzinfo=timezone.utc)))
        except ValueError:
            pass  # 31/02: fall through to the other readings

    ql = q.lower()
    if _LAST_NIGHT.search(ql):  # evening through the following dawn
        base = today - timedelta(days=1) if "tonight" not in ql else today
        return base + timedelta(hours=18), base + timedelta(days=1, hours=6)
    if "yesterday" in ql:
        return refine_time_of_day(q, (today - timedelta(days=1), today))
    if "today" in ql:
        return refine_time_of_day(q, (today, today + timedelta(days=1)))

    m = _AGO.search(ql)  # "3 days ago" is a day, not a 3-day window
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "day":
            return refine_time_of_day(q, _day(today - timedelta(days=n)))
        t = now - n * _UNITS[unit]
        return t, t + _POINT_WINDOW

    span = _weekend_span(ql)
    if span:
        return span

    m = _THIS_PERIOD.search(ql)  # calendar period so far, not a rolling window
    if m:
        unit = m.group(1).lower()
        if unit == "week":
            return today - timedelta(days=today.weekday()), now
        if unit == "month":
            return today.replace(day=1), now
        return today.replace(month=1, day=1), now

    m = _LAST_N.search(ql)
    if m:
        return now - int(m.group(1)) * _UNITS[m.group(2).lower()], now
    m = _LAST_ONE.search(ql)
    if m:
        return now - _UNITS[m.group(1).lower()], now

    # checked after the explicit windows so "the most recent 3 days" keeps its
    # width instead of collapsing to the last hour
    if _NOW.search(ql):
        return now - _POINT_WINDOW, now

    m = _SINCE.search(ql)
    if m:
        tail = m.group(1)
        span = (span_by_precision(date_text(tail, matched_keywords))
                or _weekday_span(tail))
        if span:
            return span[0], now

    span = _carrington_span(q) or _doy_span(q)
    if span:
        return span

    span = _weekday_span(ql)  # "xrays wednesday"
    if span:
        return refine_time_of_day(q, span)

    span = span_by_precision(date_text(q, matched_keywords))
    if span:
        return refine_time_of_day(q, span)

    m = _DAY_OF_MONTH.search(ql)  # "the 10th" — this month, or last month
    if m:
        day = int(m.group(1))
        for month_start in (today.replace(day=1),
                            (today.replace(day=1) - timedelta(days=1)).replace(day=1)):
            try:
                d = month_start.replace(day=day)
            except ValueError:
                continue
            if d <= today:
                return refine_time_of_day(q, _day(d))

    # a bare part of the day, with no date: today's
    if _PART_RE.search(ql):
        return refine_time_of_day(q, (today, today + timedelta(days=1)))
    return None
