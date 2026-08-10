"""Rule-based natural language query parser.

Two jobs:
1. Which data source?  Match plugin keywords (longest match wins).
2. What time range?    ISO ranges, single dates ("10th of July 2026"),
                       relative words (today / yesterday). Day-first, UTC.
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


@dataclass
class ParsedQuery:
    source: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    extras: dict = field(default_factory=dict)  # source-specific options (e.g. wavelength)
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


def parse(query: str, registry: Registry) -> ParsedQuery:
    result = ParsedQuery()

    result.source = _match_source(query, registry)
    if result.source is None:
        names = ", ".join(s.display_name for s in registry.sources.values()) or "none installed"
        result.errors.append(f"No data source recognised. Available: {names}.")
        return result

    src = registry.get(result.source)
    result.extras, consumed = src.parse_extras(query)
    matched = [
        kw for kw in src.keywords if re.search(rf"\b{re.escape(kw)}\b", query.lower())
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
