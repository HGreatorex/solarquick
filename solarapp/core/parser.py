"""Rule-based natural language query parser.

Two jobs:
1. Which data source(s)?  The query is split on conjunctions ("and", "with",
   commas, ...) and each segment is matched against plugin keywords, so
   "goes xrs and aia 304 yesterday" yields two requests. Date ranges are
   masked before splitting so "between A and B" survives intact.
2. What time range?    ISO ranges, single dates ("10th of July 2026"),
                       relative words (today / yesterday). Day-first, UTC.
                       One time range applies to every requested source.
No AI, no network, deterministic. An LLM layer can later wrap the same interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from dateutil import parser as dtparser

from .registry import Registry

_ISO = r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
_RANGE_RE = re.compile(
    rf"(?:from|between)?\s*({_ISO})\s*(?:to|and|until|-|through)\s*({_ISO})", re.I
)
# words that carry no date information and confuse fuzzy parsing
_NOISE = re.compile(
    r"\b(show|me|the|plot|display|give|get|data|for|on|please|quicklook|of|from)\b",
    re.I,
)
# conjunctions that separate one requested source from the next
_CONJ = re.compile(r"\band\b|\bwith\b|\bplus\b|\bvs\.?\b|[,+&]", re.I)


@dataclass
class SourceRequest:
    source: str
    extras: dict = field(default_factory=dict)


@dataclass
class ParsedQuery:
    # every requested source, in query order; single-source queries have one
    requests: list[SourceRequest] = field(default_factory=list)
    # first request, kept as plain fields for convenience/back-compat
    source: str | None = None
    extras: dict = field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _match_source(query: str, registry: Registry) -> str | None:
    """Score = summed length of all keywords found (word-boundary matched).

    Summing (rather than longest-match) lets multi-word context win:
    "solar orbiter stix" scores STIX (stix + solar orbiter stix) above
    EUI (solar orbiter), and "goes euvs" scores EUVS above GOES XRS.
    """
    q = query.lower()
    best, best_score = None, 0
    for src in registry.sources.values():
        score = sum(
            len(kw) for kw in src.keywords
            if re.search(rf"\b{re.escape(kw)}\b", q)
        )
        if score > best_score:
            best, best_score = src.name, score
    return best


_LAST_N = re.compile(r"\b(?:last|past)\s+(\d+)\s*(hour|day|week|month)s?\b", re.I)
_LAST_ONE = re.compile(r"\b(?:last|past)\s+(hour|day|week|month|year)\b", re.I)
_SINCE = re.compile(r"\bsince\s+(.+)$", re.I)
_TOD_RANGE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(?:to|until|–|-)\s*(\d{1,2}):(\d{2})\b")
_TOD_AT = re.compile(r"\bat\s+(\d{1,2}):(\d{2})\b")
_UNITS = {"hour": timedelta(hours=1), "day": timedelta(days=1),
          "week": timedelta(weeks=1), "month": timedelta(days=30),
          "year": timedelta(days=365)}


def _strip_keywords(q: str, matched_keywords: list[str]) -> str:
    for kw in matched_keywords:
        q = re.sub(rf"\b{re.escape(kw)}\b", " ", q, flags=re.I)
    return _NOISE.sub(" ", q)


def _span_by_precision(text: str) -> tuple[datetime, datetime] | None:
    """Fuzzy-parse text into a span whose width matches its precision.

    The text is parsed twice with different defaults; fields that follow the
    default were not in the text, revealing how specific it was:
    "10 july 2026" → that day, "july 2026" → that month, "2026" → that year.
    """
    base_year = datetime.now(timezone.utc).year
    d_lo = datetime(base_year, 1, 1)
    d_hi = datetime(base_year, 12, 28)
    try:
        p_lo, tok_lo = dtparser.parse(text, fuzzy_with_tokens=True, dayfirst=True, default=d_lo)
        p_hi, _ = dtparser.parse(text, fuzzy_with_tokens=True, dayfirst=True, default=d_hi)
    except (ValueError, OverflowError):
        return None
    if p_lo == d_lo and p_hi == d_hi:  # nothing date-like in the text
        return None
    s_lo = _utc(p_lo)
    if p_lo.month != p_hi.month:  # only a year was given
        s = s_lo.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return s, s.replace(year=s.year + 1)
    if p_lo.day != p_hi.day:  # month + year, no day
        s = s_lo.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
        return s, nxt
    if (p_lo.hour, p_lo.minute) != (0, 0):  # a time of day was given
        return s_lo, s_lo + timedelta(hours=1)
    day = s_lo.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day + timedelta(days=1)


def _refine_time_of_day(query: str, span: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
    """Crop a whole-day span to "14:00 to 15:30" / "at 14:30" if present."""
    start, end = span
    if end - start != timedelta(days=1):
        return span
    m = _TOD_RANGE.search(query)
    if m:
        h1, m1, h2, m2 = (int(g) for g in m.groups())
        if h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60:
            s = start.replace(hour=h1, minute=m1)
            e = start.replace(hour=h2, minute=m2)
            if e > s:
                return s, e
    m = _TOD_AT.search(query)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            s = start.replace(hour=h, minute=mi)
            return s, s + timedelta(hours=1)
    return span


def _extract_time(query: str, matched_keywords: list[str]) -> tuple[datetime, datetime] | None:
    q = query
    now = datetime.now(timezone.utc)

    m = _RANGE_RE.search(q)
    if m:
        s, e = dtparser.parse(m.group(1)), dtparser.parse(m.group(2))
        if len(m.group(2)) <= 10:  # date-only end -> include the whole day
            e += timedelta(days=1)
        return _utc(s), _utc(e)

    m = re.search(_ISO, q)
    if m:  # single ISO date/datetime — parse unambiguously (never day-first)
        s = _utc(dtparser.parse(m.group(0), yearfirst=True))
        if len(m.group(0)) <= 10:
            return _refine_time_of_day(q, (s, s + timedelta(days=1)))
        # "2026-07-10 14:00 to 15:30": bare end time following a full datetime
        tail = re.match(r"\s*(?:to|until|–|-)\s*(\d{1,2}):(\d{2})\b", q[m.end():])
        if tail:
            h, mi = int(tail.group(1)), int(tail.group(2))
            if h < 24 and mi < 60:
                e = s.replace(hour=h, minute=mi)
                if e > s:
                    return s, e
        return s, s + timedelta(hours=1)

    ql = q.lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "yesterday" in ql:
        return _refine_time_of_day(q, (today - timedelta(days=1), today))
    if "today" in ql:
        return _refine_time_of_day(q, (today, today + timedelta(days=1)))

    m = _LAST_N.search(ql)
    if m:
        return now - int(m.group(1)) * _UNITS[m.group(2).lower()], now
    m = _LAST_ONE.search(ql)
    if m:
        return now - _UNITS[m.group(1).lower()], now

    m = _SINCE.search(ql)
    if m:
        span = _span_by_precision(_strip_keywords(m.group(1), matched_keywords))
        if span:
            return span[0], now

    span = _span_by_precision(_strip_keywords(q, matched_keywords))
    if span:
        return _refine_time_of_day(q, span)
    return None


def _match_requests(query: str, registry: Registry) -> tuple[list[SourceRequest], list[str]]:
    """Match one source per conjunction-separated segment (deduplicated).

    Returns (requests, consumed_tokens). Falls back to whole-query matching
    when no individual segment matches (e.g. keywords split by a stray comma).
    """
    masked = _RANGE_RE.sub(" § ", query)  # protect "between A and B" from the split
    segments = [s for s in _CONJ.split(masked) if s.strip()]

    requests: list[SourceRequest] = []
    consumed: list[str] = []
    seen: set[tuple] = set()
    for seg in segments:
        name = _match_source(seg, registry)
        if name is None:
            continue
        extras, used = registry.get(name).parse_extras(seg)
        key = (name, tuple(sorted((k, repr(v)) for k, v in extras.items())))
        if key in seen:
            continue
        seen.add(key)
        consumed += used
        requests.append(SourceRequest(name, extras))

    if not requests:  # no segment matched — try the whole query
        name = _match_source(query, registry)
        if name is not None:
            extras, used = registry.get(name).parse_extras(query)
            consumed += used
            requests.append(SourceRequest(name, extras))
    return requests, consumed


def parse(query: str, registry: Registry) -> ParsedQuery:
    result = ParsedQuery()

    result.requests, consumed = _match_requests(query, registry)
    if not result.requests:
        names = ", ".join(s.display_name for s in registry.sources.values()) or "none installed"
        result.errors.append(f"No data source recognised. Available: {names}.")
        return result
    result.source = result.requests[0].source
    result.extras = result.requests[0].extras

    matched = [
        kw
        for req in result.requests
        for kw in registry.get(req.source).keywords
        if re.search(rf"\b{re.escape(kw)}\b", query.lower())
    ] + consumed
    tr = _extract_time(query, matched)
    if tr is None:
        result.errors.append(
            'No date found. Try e.g. "10 July 2026", "2026-07-10", '
            '"from 2026-07-10 to 2026-07-12", "yesterday".'
        )
        return result

    result.start, result.end = tr
    return result
