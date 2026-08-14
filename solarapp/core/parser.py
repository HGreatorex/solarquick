"""Rule-based natural language query parser.

Two jobs:
1. Which data source(s)?  The query is split on conjunctions ("and", "with",
   commas, ...) and each segment is scored against plugin keywords, so
   "goes xrs and aia 304 yesterday" yields two requests. Date ranges are
   masked before splitting so "between A and B" survives intact.
2. What time range?    ISO ranges, single dates ("10th of July 2026"),
                       relative words (today / yesterday / now / 3 days ago /
                       this month), and a time of day within a single day
                       ("at 14:30", "between 10:00 and 12:00", "10am-2pm").
                       Day-first, UTC. One range applies to every source.

Matching is tolerant of *spelling*, never of *meaning*. registry.keyword_regex
folds hyphens, spaces and plurals into one keyword, so "xrays" reaches GOES XRS
without the plugin listing six variants — but when a broadened term ties across
several sources ("euv") the parser reports the tie and names the candidates
instead of letting plugin load order decide.

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

from .registry import Registry, keyword_regex

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
    "now", "latest", "recent", "current", "ago", "this", "at", "around",
    "morning", "afternoon", "evening", "night",
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
            # separators inside a keyword are optional when matching, so the
            # run-together spelling is a real word here too — without it
            # "xray" looks unknown and gets "corrected" to "ray"
            joined = re.sub(r"[^a-z]", "", kw.lower())
            if len(joined) > 1:
                words.add(joined)
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


def _match_source(query: str, registry: Registry) -> tuple[str | None, list[str]]:
    """Best-scoring source for `query`, plus every source tied with it.

    Scoring lives on the plugin (DataSource.score) so a source can express
    context a keyword list cannot. A tie is returned rather than broken: a
    term like "euv" genuinely belongs to five instruments, and silently
    picking one would be exactly the confidently-wrong answer this app
    refuses to give. Callers turn a tie into a question for the user.
    """
    q = query.lower()
    scored = [(src.score(q), src.name) for src in registry.sources.values()]
    best_score = max((s for s, _ in scored), default=0)
    if best_score <= 0:
        return None, []
    tied = [name for s, name in scored if s == best_score]
    return tied[0], tied


_LAST_N = re.compile(r"\b(?:last|past)\s+(\d+)\s*(hour|day|week|month)s?\b", re.I)
_LAST_ONE = re.compile(r"\b(?:last|past)\s+(hour|day|week|month|year)\b", re.I)
_SINCE = re.compile(r"\bsince\s+(.+)$", re.I)
_AGO = re.compile(r"\b(\d+)\s*(hour|day|week|month|year)s?\s+ago\b", re.I)
_THIS_PERIOD = re.compile(r"\bthis\s+(week|month|year)\b", re.I)
_NOW = re.compile(r"\b(?:now|right now|latest|most recent|current)\b", re.I)
_UNITS = {"hour": timedelta(hours=1), "day": timedelta(days=1),
          "week": timedelta(weeks=1), "month": timedelta(days=30),
          "year": timedelta(days=365)}

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


def _strip_keywords(q: str, matched_keywords: list[str]) -> str:
    for kw in matched_keywords:
        q = keyword_regex(kw).sub(" ", q)
    return _NOISE.sub(" ", q)


# Clock times are removed before the *date* is parsed. dateutil would otherwise
# fold "10 July 2026 between 10:00 and 12:00" into a single instant and report
# hour precision, which collapses the span to one hour before the time-of-day
# logic ever runs. Times are re-applied afterwards by _refine_time_of_day.
_STRIP_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?|\b\d{1,2}\s*[ap]\.?m\.?\b", re.I)


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
        return s, nxt
    if (p_lo.hour, p_lo.minute) != (0, 0):  # a time of day was given
        return s_lo, s_lo + timedelta(hours=1)
    day = s_lo.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day + timedelta(days=1)


def _refine_time_of_day(query: str, span: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
    """Crop a whole-day span to the time of day the query asked for.

    Understands "14:00 to 15:30", "between 10:00 and 12:00", "10am-2pm",
    "at 14:30", "around 2pm" and a bare trailing "yesterday 14:00". A range
    whose end precedes its start ("22:00 to 02:00") is read as crossing
    midnight, which is what an observer means by it.
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

    ql = q.lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "yesterday" in ql:
        return _refine_time_of_day(q, (today - timedelta(days=1), today))
    if "today" in ql:
        return _refine_time_of_day(q, (today, today + timedelta(days=1)))

    m = _AGO.search(ql)  # "3 days ago" is a day, not a 3-day window
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "day":
            day = today - timedelta(days=n)
            return _refine_time_of_day(q, (day, day + timedelta(days=1)))
        t = now - n * _UNITS[unit]
        return t, t + _POINT_WINDOW

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
        span = _span_by_precision(_date_text(m.group(1), matched_keywords))
        if span:
            return span[0], now

    span = _span_by_precision(_date_text(q, matched_keywords))
    if span:
        return _refine_time_of_day(q, span)
    return None


def _date_text(q: str, matched_keywords: list[str]) -> str:
    """The part of the query that can carry a calendar date."""
    return _STRIP_CLOCK.sub(" ", _strip_keywords(q, matched_keywords))


def _match_requests(
    query: str, registry: Registry
) -> tuple[list[SourceRequest], list[str], list[tuple[str, list[str]]]]:
    """Match one source per conjunction-separated segment (deduplicated).

    Returns (requests, consumed_tokens, ambiguities), where an ambiguity is a
    (segment, tied source names) pair the caller must ask the user about.
    Falls back to whole-query matching when no individual segment matches
    (e.g. keywords split by a stray comma).
    """
    masked = _RANGE_RE.sub(" § ", query)  # protect "between A and B" from the split
    segments = [s for s in _CONJ.split(masked) if s.strip()]

    requests: list[SourceRequest] = []
    consumed: list[str] = []
    ambiguities: list[tuple[str, list[str]]] = []
    seen: set[tuple] = set()
    for seg in segments:
        name, tied = _match_source(seg, registry)
        if name is None:
            continue
        if len(tied) > 1:
            ambiguities.append((seg.strip(), tied))
            continue
        extras, used = registry.get(name).parse_extras(seg)
        key = (name, tuple(sorted((k, repr(v)) for k, v in extras.items())))
        if key in seen:
            continue
        seen.add(key)
        consumed += used
        requests.append(SourceRequest(name, extras))

    if not requests and not ambiguities:  # no segment matched — try the whole query
        name, tied = _match_source(query, registry)
        if name is not None and len(tied) > 1:
            ambiguities.append((query.strip(), tied))
        elif name is not None:
            extras, used = registry.get(name).parse_extras(query)
            consumed += used
            requests.append(SourceRequest(name, extras))
    return requests, consumed, ambiguities


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

    result.requests, consumed, ambiguities = _match_requests(query, registry)
    if ambiguities:
        # a tie is a real question, not a failure — name the candidates so the
        # user can answer it in one edit instead of guessing at the vocabulary
        for segment, tied in ambiguities:
            names = ", ".join(registry.get(n).display_name for n in tied)
            result.errors.append(
                f"“{segment.strip()}” could mean several instruments: {names}. "
                "Please name the one you want."
            )
        return result
    if not result.requests:
        names = ", ".join(s.display_name for s in registry.sources.values()) or "none installed"
        result.errors.append(f"No data source recognised. Available: {names}.")
        return result
    result.source = result.requests[0].source
    result.extras = result.requests[0].extras

    matched = [
        kw
        for req in result.requests
        for kw in registry.get(req.source).matched_keywords(query.lower())
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
