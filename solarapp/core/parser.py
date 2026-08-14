"""Rule-based natural language query parser.

Three jobs:

1. **Which data source(s)?** The query is split on conjunctions ("and", "with",
   commas, ...) and each segment is scored against plugin keywords, so
   "goes xrs and aia 304 yesterday" yields two requests. Date ranges are masked
   before splitting so "between A and B" survives intact. A segment that names
   only an *option* ("…, 193 and 211") attaches to the instrument named before
   it, and a query that names only an option ("sunspots") is routed by the
   declared variables of every source.
2. **Which options?** Each source resolves its own declared variables and
   `parse_extras`, and the tokens they consume are removed before dates are
   read, so "aia 193 on 10 July" cannot lose its day to a wavelength.
3. **What time range?** Delegated to `core/timegrammar`, which covers ISO
   ranges, natural ranges, single dates, relative words, weekdays, parts of a
   day, day-of-year and Carrington rotations. Day-first, UTC.

Matching is tolerant of *spelling*, never of *meaning*. registry.keyword_regex
folds hyphens, spaces and plurals into one keyword, so "xrays" reaches GOES XRS
without the plugin listing six variants — but when a broadened term ties across
several sources ("euv") the parser reports the tie and names the candidates
instead of letting plugin load order decide.

Typos are handled by retry, not by loosening the matchers: a query that
fails to parse is spell-corrected against the keyword + variable + date-word
vocabulary and parsed again, so "goess xsr yesterdy" works while exact matching
stays strict. Corrections are reported in ParsedQuery.corrections — a science
tool should never silently reinterpret what was asked for.

No AI, no network, deterministic. The optional Claude layer only *rewrites* a
query that all of this failed on, and its rewrite comes straight back through
here.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import summary as summary_view
from . import timegrammar as tg
from .registry import Registry

# conjunctions that separate one requested source from the next
_CONJ = re.compile(
    r"\band\b|\bwith\b|\bplus\b|\bvs\.?\b|\bversus\b|\balongside\b|"
    r"\bas well as\b|\btogether with\b|[,;+&]", re.I
)


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
    # set when no instrument was named and the overview was composed instead
    summary: str | None = None

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
        if self.summary:
            notes.append(self.summary)
        return " ".join(notes)


# -- typo tolerance ------------------------------------------------------

# Words the parser understands beyond plugin keywords and variables: dates,
# time grammar, and the flare vocabulary. Included so date typos ("yesterdy",
# "januray") are correctable too, and so real words are never "corrected" away.
_GRAMMAR_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "weds", "thu", "thur", "thurs", "fri",
    "today", "yesterday", "tomorrow", "tonight", "last", "past", "previous",
    "next", "since", "between", "from", "until", "through", "thru",
    "hour", "hours", "day", "days", "week", "weeks", "weekend", "fortnight",
    "month", "months", "year", "years",
    "now", "latest", "recent", "current", "ago", "this", "at", "around",
    "morning", "afternoon", "evening", "night", "midday", "noon", "midnight",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "couple", "few", "several",
    "doy", "carrington", "rotation",
    "flare", "flares", "class", "biggest", "largest", "strongest", "powerful",
    "above", "over", "show", "plot", "display", "give", "data", "please",
    "quicklook", "position", "positions", "where", "spectrum", "spectra",
    # cadence words: read by GOES XRS's parse_extras, so they must be known
    # here too or _autocorrect would "fix" them into something else
    "high", "full", "resolution", "res", "cadence", "second", "seconds",
    "channel", "band", "bands", "detector", "line", "lines", "index",
}
_GRAMMAR_WORDS |= {w for kw in summary_view.KEYWORDS for w in re.findall(r"[a-z]+", kw)}

# Function words. They are in the vocabulary so the spell-checker leaves them
# alone — with "band" among the option words, an unprotected "and" was one
# edit away and every multi-source query lost its second instrument. They are
# also what makes a query "only a date": a request built entirely from these,
# the grammar words and digits has named no instrument at all.
_STOPWORDS = {
    "and", "with", "plus", "vs", "versus", "alongside", "the", "a", "an", "of",
    "for", "on", "in", "to", "into", "me", "my", "i", "we", "us", "it", "its",
    "is", "was", "were", "be", "been", "there", "here", "that", "these",
    "those", "some", "any", "all", "both", "just", "only", "also", "again",
    "about", "during", "up", "down", "out", "off", "then", "than", "so",
    "what", "which", "who", "how", "why", "when", "did", "do", "does", "done",
    "can", "could", "would", "should", "will", "want", "need", "like", "see",
    "look", "looking", "find", "please", "thanks", "ut", "utc", "z",
}
_GRAMMAR_WORDS |= _STOPWORDS

# Below this length, edit distance is not evidence: the instrument acronyms
# are dense (sot/sdo, eve/eui/eit are one or two edits apart), so a typo'd
# 3-letter token would confidently resolve to the wrong instrument.
_FUZZY_MIN_LEN = 4
# 0.80 admits single transpositions ("lasoc" -> "lasco"), which difflib scores
# harshly; verified against the full example-query set to change none of them.
_FUZZY_CUTOFF = 0.80
# How much shorter or longer the correction may be than what was typed.
_FUZZY_MAX_LEN_DIFF = 1


def _vocabulary(registry: Registry) -> set[str]:
    """Every word the parser knows: grammar plus whatever the plugins declare."""
    return set(_GRAMMAR_WORDS) | registry.vocabulary()


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
            # A typo is a slip, not a different word: "lasoc" for "lasco" keeps
            # its length, while "slit" → "sunlit" invents two characters and
            # would silently answer a question about spectrograph slits with a
            # daylight map.
            if near and abs(len(near[0]) - len(low)) > _FUZZY_MAX_LEN_DIFF:
                near = []
            if near and near[0] != low:
                corrections.append((piece, near[0]))
                piece = near[0]
        out.append(piece)
    return "".join(out), corrections


# -- source matching -----------------------------------------------------


def _variable_score(src, text: str) -> int:
    """How strongly `text` names one of this source's options.

    Used only when no source keyword matched anywhere: "sunspots yesterday"
    and "magnetogram" name a *thing*, not an instrument, and the instrument
    that offers it is the answer.
    """
    return max((v.score(text) for v in src.variables), default=0)


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
    if max((s for s, _ in scored), default=0) <= 0:
        scored = [(_variable_score(src, q), src.name) for src in registry.sources.values()]
    best_score = max((s for s, _ in scored), default=0)
    if best_score <= 0:
        return None, []
    tied = [name for s, name in scored if s == best_score]
    return tied[0], tied


def _mask_ranges(query: str) -> str:
    """Hide date ranges before the conjunction split.

    "between 10 July and 12 July" is one time span, not two instruments, and
    the "and" inside it would otherwise cut the query in half.
    """
    masked = tg.RANGE_RE.sub(" § ", query)
    nat = tg.natural_range(masked)
    if nat:
        masked = masked.replace(nat[1], " § ")
    return masked


def _match_requests(
    query: str, registry: Registry
) -> tuple[list[SourceRequest], list[str], list[tuple[str, list[str]]]]:
    """Match one source per conjunction-separated segment (deduplicated).

    Returns (requests, consumed_tokens, ambiguities), where an ambiguity is a
    (segment, tied source names) pair the caller must ask the user about.

    A segment that names no source but does name an option of the source
    before it belongs to that source: "aia 171, 193 and 211" is three AIA
    requests, and used to be one. Falls back to whole-query matching when no
    individual segment matches (e.g. keywords split by a stray comma).
    """
    segments = [s for s in _CONJ.split(_mask_ranges(query)) if s.strip()]

    requests: list[SourceRequest] = []
    consumed: list[str] = []
    ambiguities: list[tuple[str, list[str]]] = []
    seen: set[tuple] = set()

    def add(name: str, extras: dict, used: list[str]) -> None:
        key = (name, tuple(sorted((k, repr(v)) for k, v in extras.items())))
        if key in seen:
            return
        seen.add(key)
        consumed.extend(used)
        requests.append(SourceRequest(name, extras))

    for seg in segments:
        name, tied = _match_source(seg, registry)
        if name is None or len(tied) > 1:
            # "…, 193 and 211": an option with no instrument of its own
            # continues the instrument named before it. This also answers the
            # tie — "304" belongs to five instruments in the abstract, but
            # after "aia 171," it plainly belongs to AIA.
            if requests:
                previous = registry.get(requests[-1].source)
                extras, used = previous.variable_extras(seg)
                if used:
                    add(previous.name, extras, used)
                    continue
            if name is not None:
                ambiguities.append((seg.strip(), tied))
            continue
        extras, used = registry.get(name).parse_extras(seg)
        add(name, extras, used)

    if not requests and not ambiguities:  # no segment matched — try the whole query
        name, tied = _match_source(query, registry)
        if name is not None and len(tied) > 1:
            ambiguities.append((query.strip(), tied))
        elif name is not None:
            extras, used = registry.get(name).parse_extras(query)
            add(name, extras, used)
    return requests, consumed, ambiguities


# -- suggestions ---------------------------------------------------------


def _phrases(registry: Registry) -> list[str]:
    """Every phrase a user could have meant: source keywords and option names."""
    out: list[str] = []
    for src in registry.sources.values():
        out += list(src.keywords)
        for var in src.variables:
            out += list(var.keywords)
    return out


def _suggest(query: str, registry: Registry) -> list[str]:
    """The closest few known phrases to the words actually typed."""
    phrases = _phrases(registry)
    hits: list[str] = []
    for token in re.findall(r"[a-z]{3,}", query.lower()):
        for near in difflib.get_close_matches(token, phrases, n=2, cutoff=0.7):
            if near not in hits:
                hits.append(near)
    return hits[:4]


def names_only_a_date(query: str) -> bool:
    """True when every word in the query is grammar, not subject matter.

    This is the difference between "2017-09-06" (a request for an overview of
    that day) and "solar wind speed yesterday" (a request for something
    SolarQuick does not serve). Both name no instrument, but only the first
    one asked a question the overview answers; quietly showing X-ray flux for
    the second would be a confidently wrong answer to a question about the
    plasma stream.
    """
    words = re.findall(r"[a-z]+", query.lower())
    return all(w in _GRAMMAR_WORDS for w in words)


def _no_source_error(query: str, registry: Registry) -> str:
    names = ", ".join(s.display_name for s in registry.sources.values()) or "none installed"
    near = _suggest(query, registry)
    hint = f" Did you mean {', '.join(repr(n) for n in near)}?" if near else ""
    return (f"No instrument or quantity recognised in “{query.strip()}”.{hint} "
            f"Available: {names}. A date on its own gives an overview.")


# -- entry points --------------------------------------------------------


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

    wants_summary = summary_view.asked_for(query)
    if wants_summary:
        result.requests, consumed, ambiguities = [], [], []
    else:
        result.requests, consumed, ambiguities = _match_requests(query, registry)
        if ambiguities:
            # a tie is a real question, not a failure — name the candidates so
            # the user can answer it in one edit instead of guessing at the
            # vocabulary
            for segment, tied in ambiguities:
                names = ", ".join(registry.get(n).display_name for n in tied)
                result.errors.append(
                    f"“{segment.strip()}” could mean several instruments: {names}. "
                    "Please name the one you want."
                )
            return result

    matched = [
        kw
        for req in result.requests
        for kw in registry.get(req.source).matched_keywords(query.lower())
    ] + consumed
    tr = tg.extract(query, matched)

    if not result.requests:
        # Nothing named an instrument. A query that is *only* a date is a
        # request for an overview of that date; a query that named something
        # else and simply wasn't understood must still fail, or the overview
        # would become a wrong answer to every unsupported question.
        if tr is None or not (wants_summary or names_only_a_date(query)):
            result.errors.append(_no_source_error(query, registry))
            return result
        pairs = summary_view.compose(registry, *tr)
        if not pairs:
            result.errors.append(
                f"No instrument in SolarQuick was observing between "
                f"{tr[0]:%Y-%m-%d} and {tr[1]:%Y-%m-%d}."
            )
            return result
        result.requests = [SourceRequest(name, extras) for name, extras in pairs]
        if not wants_summary:
            result.summary = summary_view.note(registry, result.requests)

    result.source = result.requests[0].source
    result.extras = result.requests[0].extras

    if tr is None:
        result.errors.append(
            'No date found. Try e.g. "10 July 2026", "2026-07-10", "yesterday", '
            '"last Wednesday", "the last 3 days", "from 2026-07-10 to 2026-07-12".'
        )
        return result

    result.start, result.end = tr
    return result
