"""Rule-based natural language query parser.

Two jobs:
1. Which data source(s)?  The query is split on conjunctions ("and", "with",
   commas, ...) and each segment is matched against plugin keywords, so
   "goes xrs and aia 304 yesterday" yields two requests. Date ranges are
   masked before splitting so "between A and B" survives intact.
2. What time range?    ISO ranges, single dates ("10th of July 2026"),
                       relative words (today / yesterday). Day-first, UTC.
                       One time range applies to every requested source.

Typos are handled by retry, not by loosening the matchers: a query that
fails to parse is spell-corrected against the keyword + date-word vocabulary
and parsed again, so "goess xsr yesterdy" works while exact matching stays
strict. Corrections are reported in ParsedQuery.corrections — a science tool
should never silently reinterpret what was asked for.

No AI, no network, deterministic. An LLM layer can later wrap the same interface.
"""
from __future__ import annotations

import difflib
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
    # spelling fixes applied to make the query parse: [(typed, used), ...]
    corrections: list[tuple[str, str]] = field(default_factory=list)
    # set when Claude restated the query: (canonical_query, one-line reading)
    rewritten: tuple[str, str] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def correction_note(self) -> str:
        notes = []
        if self.corrections:
            fixes = ", ".join(f"“{a}” → “{b}”" for a, b in self.corrections)
            notes.append(f"Interpreted {fixes}.")
        if self.rewritten:
            canonical, reading = self.rewritten
            notes.append(f"{reading} Read as: “{canonical}”.")
        return " ".join(notes)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# -- typo tolerance ------------------------------------------------------

# Words the parser understands beyond plugin keywords: dates, time grammar,
# and the flare vocabulary. Included so date typos ("yesterdy", "januray")
# are correctable too, and so real words are never "corrected" away.
_GRAMMAR_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "today", "yesterday", "tomorrow", "last", "past", "since", "between",
    "from", "until", "through", "hour", "hours", "day", "days", "week",
    "weeks", "month", "months", "year", "years",
    "flare", "flares", "class", "biggest", "largest", "strongest", "powerful",
    "above", "over", "show", "plot", "display", "give", "data", "please",
    "quicklook", "position", "positions", "where", "spectrum", "spectra",
}
# Below this length, edit distance is not evidence: the instrument acronyms
# are dense (sot/sdo, eve/eui/eit are one or two edits apart), so a typo'd
# 3-letter token would confidently resolve to the wrong instrument.
_FUZZY_MIN_LEN = 4
# 0.80 admits single transpositions ("lasoc" -> "lasco"), which difflib scores
# harshly; verified against the full example-query set to change none of them.
_FUZZY_CUTOFF = 0.80


def _vocabulary(registry: Registry) -> set[str]:
    words = set(_GRAMMAR_WORDS)
    for src in registry.sources.values():
        for kw in src.keywords:
            words.update(re.findall(r"[a-z]+", kw.lower()))
    return words


def _autocorrect(query: str, registry: Registry) -> tuple[str, list[tuple[str, str]]]:
    """Spell-correct unknown alphabetic tokens against the parser vocabulary.

    Only tokens that are not already understood are touched, so a valid query
    is returned unchanged and cannot be corrupted. Three-letter tokens are
    matched against the grammar words only ("lst" → "last"): those are not
    acronym-dense, whereas correcting a short token against instrument names
    could confidently turn it into the wrong instrument.
    """
    vocab = _vocabulary(registry)
    corrections: list[tuple[str, str]] = []
    out: list[str] = []
    for piece in re.split(r"([^A-Za-z]+)", query):  # keep separators/numbers
        low = piece.lower()
        if piece.isalpha() and low not in vocab:
            if len(piece) >= _FUZZY_MIN_LEN:
                candidates = vocab
            elif len(piece) == _FUZZY_MIN_LEN - 1:
                candidates = _GRAMMAR_WORDS
            else:
                candidates = ()
            near = difflib.get_close_matches(low, candidates, n=1, cutoff=_FUZZY_CUTOFF)
            if near and near[0] != low:
                corrections.append((piece, near[0]))
                piece = near[0]
        out.append(piece)
    return "".join(out), corrections


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


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MONTH_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I
)


def _span_by_precision(text: str) -> tuple[datetime, datetime] | None:
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
    """Parse a query, correcting unrecognised words first.

    Correction runs before parsing rather than only after a failure, because
    a typo usually *degrades* a parse instead of breaking it: "flares in
    Januray 2025" would quietly widen to the whole year, "bigest flare since
    2014" would return a 12-year list instead of the one biggest flare, and
    "goes and terminater today" would silently drop the second source. Those
    confident-but-wrong answers are worse than an error.

    Corrections only ever replace tokens the parser could not understand at
    all, so a valid query is parsed untouched; if the corrected query fails
    to parse we fall back to the original's clearer error message.
    """
    corrected, corrections = _autocorrect(query, registry)
    if corrections:
        retry = _parse_once(corrected, registry)
        if retry.ok:
            retry.corrections = corrections
            return retry
    result = _parse_once(query, registry)
    if result.ok:
        return result
    return _llm_fallback(query, registry, result)


def _llm_fallback(query: str, registry: Registry, failed: ParsedQuery) -> ParsedQuery:
    """Last resort: have Claude restate the query, then parse that.

    The rewrite goes through the same keyword parser as any other query, so a
    hallucinated source simply fails to match — the model cannot widen what
    SolarQuick will fetch. Returns the original failure if unavailable.
    """
    from . import llm_parser

    if not llm_parser.enabled():
        return failed
    try:
        canonical, reading, problem = llm_parser.rewrite(query, registry)
    except Exception as e:  # never let the fallback turn into a worse error
        failed.errors.append(f"(Interpretation via Claude was unavailable: {e}.)")
        return failed

    if not canonical:
        return ParsedQuery(errors=[problem or failed.errors[0]])

    result = _parse_once(canonical, registry)
    if not result.ok:
        return failed  # the rewrite didn't parse either; keep the clearer error
    result.rewritten = (canonical, reading)
    return result


def _parse_once(query: str, registry: Registry) -> ParsedQuery:
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
