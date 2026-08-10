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


def _extract_time(query: str, matched_keywords: list[str]) -> tuple[datetime, datetime] | None:
    q = query

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
            return s, s + timedelta(days=1)
        return s, s + timedelta(hours=1)

    ql = q.lower()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if "yesterday" in ql:
        return today - timedelta(days=1), today
    if "today" in ql:
        return today, today + timedelta(days=1)

    # strip source keywords + noise words, then fuzzy-parse what's left
    for kw in matched_keywords:
        q = re.sub(rf"\b{re.escape(kw)}\b", " ", q, flags=re.I)
    q = _NOISE.sub(" ", q)
    try:
        dt, _ = dtparser.parse(q, fuzzy_with_tokens=True, dayfirst=True, default=today.replace(tzinfo=None))
        day = _utc(dt.replace(hour=0, minute=0, second=0, microsecond=0))
        return day, day + timedelta(days=1)
    except (ValueError, OverflowError):
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
