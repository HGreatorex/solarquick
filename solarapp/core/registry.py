"""Data source base class and auto-discovery registry.

Adding a data source = dropping a module into solarapp/sources/ containing a
DataSource subclass. Nothing else to edit.

A plugin declares four things, and everything else in the app is derived from
them, so a new file teaches the whole system at once:

- `keywords`   — the phrases that route a query here (parser vocabulary).
- `variables`  — the *options within* the source: channels, lines, detectors,
  energy bands. Declaring them (rather than hiding the matching inside
  `parse_extras`) is what lets the spell-checker learn their words, the
  "did you mean" message name them, `/api/sources` list them, and a segment
  like "…and 193" attach itself to the instrument named before it.
- `coverage`   — the instrument's observing span, so a request outside it is
  refused before a network call and the summary view never picks a source
  that was not flying.
- `summary_rank` — opt in to the bare-date overview ("what happened on …").
"""
from __future__ import annotations

import functools
import importlib
import pkgutil
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PlotSpec = dict[str, Any]  # see ARCHITECTURE.md for schema

# Separators inside a keyword that users write inconsistently, and that carry no
# meaning: "x-ray", "x ray" and "xray" are the same word, as are "wind/waves"
# and "wind waves".
_SEP = r"[\s\-/]*"


@functools.lru_cache(maxsize=1024)
def keyword_regex(keyword: str) -> re.Pattern:
    """Word-boundary matcher for one keyword, tolerant of how it's written.

    Internal hyphens, slashes and spaces are interchangeable and optional, and
    a trailing plural "s" is allowed, so the single keyword "x-ray" matches
    "x-ray", "x ray", "xray", "x-rays" and "xrays". This is why the keyword
    lists stay short: spelling variants are the matcher's job, not the
    plugin author's, and every unlisted variant used to fail outright.

    A keyword that ends in a digit (a wavelength, "goes 16") does not take the
    plural `s`: "304s" is not a word anyone types, and allowing it would let
    a stray "1600s" (a decade) select an AIA channel.
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-/]+", keyword.strip()) if p]
    plural = "" if keyword.rstrip().endswith(tuple("0123456789")) else "s?"
    return re.compile(rf"\b{_SEP.join(parts)}{plural}\b", re.I)


def keyword_words(keyword: str) -> set[str]:
    """Every spelling of `keyword` the spell-checker should treat as known.

    It must cover exactly what `keyword_regex` accepts, or the parser
    "corrects" words it would have matched verbatim: "active regions
    yesterday" reported an interpretation of “regions” → “region” on its way
    to the right answer, which reads like a warning about a typo nobody made.

    So: the separate words, the run-together spelling (separators inside a
    keyword are optional — but only when nothing else was removed, or "lasco
    c2" would contribute the non-word "lascoc"), and the plural the matcher
    allows on the final word.
    """
    words = set(re.findall(r"[a-z]+", keyword.lower()))
    joined = re.sub(r"[\s\-/]", "", keyword.lower())
    if len(joined) > 1 and joined.isalpha():
        words.add(joined)
    # The matcher's plural is meant for words that take one: it also accepts
    # "goess", but that is a slip worth reporting, not a spelling of "goes",
    # so a word already ending in s does not gain another.
    last = re.findall(r"[a-z]+", keyword.lower())
    if last and not last[-1].endswith("s") and not keyword.rstrip().endswith(tuple("0123456789")):
        words.add(last[-1] + "s")
        if joined.isalpha() and len(joined) > 1:
            words.add(joined + "s")
    return words


@dataclass
class Variable:
    """One selectable option inside a source: a channel, line, band, detector.

    `extras` is what reaches `fetch()`; when omitted it is
    `{source.variable_key: key}`, which covers the common case of a plugin
    whose options are all one kind of thing.

    `group` names the *dimension* the option belongs to. Options in different
    groups are independent and combine ("goes xrs long channel high
    resolution" is a channel *and* a cadence), while options in the same group
    are alternatives and only the best-scoring one is taken.
    """

    key: str
    label: str
    keywords: list[str] = field(default_factory=list)
    extras: dict[str, Any] | None = None
    group: str = ""  # "" means the source's default group (variable_key)
    # a variable that exists but should not be offered in help text (spelling
    # variants that would clutter the listing)
    hidden: bool = False

    def extras_for(self, source: "DataSource") -> dict:
        return dict(self.extras) if self.extras is not None else {source.variable_key: self.key}

    def score(self, text: str) -> int:
        """Summed length of the keywords that fired, as for sources."""
        return sum(len(kw) for kw in self.keywords if keyword_regex(kw).search(text))

    def matched_text(self, text: str) -> list[str]:
        out = []
        for kw in self.keywords:
            m = keyword_regex(kw).search(text)
            if m:
                out.append(m.group(0))
        return out


def _iso(date: str | None) -> datetime | None:
    if date is None:
        return None
    return datetime.fromisoformat(date).replace(tzinfo=timezone.utc)


class DataSource(ABC):
    """Base class for all data source plugins."""

    name: str = ""  # unique id, e.g. "goes_xrs"
    display_name: str = ""
    keywords: list[str] = []  # phrases the parser matches (lowercase)

    # options within this source; see Variable
    variables: list[Variable] = []
    # extras key a bare Variable fills ("channel" suits most imagers)
    variable_key: str = "channel"
    # what the block renders as in the two-pane UI: "images" go into the image
    # grid (and its synced movie timeline), everything else stacks below it
    display: str = "plot"

    # (first light, last light) as ISO dates; None = open-ended. Declared so the
    # app can refuse an out-of-span request without a network round trip and
    # pick era-appropriate sources for the summary view.
    coverage: tuple[str | None, str | None] = (None, None)

    # Lower ranks come first in the bare-date summary; None keeps the source
    # out of it. Set this and a new plugin joins the overview automatically.
    summary_rank: int | None = None
    summary_extras: dict[str, Any] = {}

    def score(self, text: str) -> int:
        """How strongly `text` asks for this source: summed keyword lengths.

        Summing (rather than longest-match) lets multi-word context win:
        "solar orbiter stix" scores STIX (stix + solar orbiter stix) above
        EUI (solar orbiter). Override to add context a flat keyword list
        cannot express — see Wind/WAVES, where a bare "wind" means the
        spacecraft only when "solar wind" isn't what was asked for.
        """
        return sum(len(kw) for kw in self.keywords if keyword_regex(kw).search(text))

    def matched_keywords(self, text: str) -> list[str]:
        """The keywords that fired for `text` — used to strip them before date parsing."""
        return [kw for kw in self.keywords if keyword_regex(kw).search(text)]

    # -- variables --------------------------------------------------------

    def match_variables(self, text: str) -> list[Variable]:
        """The declared variables `text` asks for: the best one in each group.

        Scores like source matching, so context accumulates: "he ii 304"
        picks 30.4 nm (two keywords) over 25.6 nm (which is also He II).
        A tie goes to the earlier declaration — within one instrument the
        options are alternatives of the same kind, and refusing to answer
        would be a worse trade than it is between instruments.
        """
        best: dict[str, tuple[int, Variable]] = {}
        for var in self.variables:
            s = var.score(text)
            if s <= 0:
                continue
            group = var.group or self.variable_key
            if s > best.get(group, (0, None))[0]:
                best[group] = (s, var)
        return [v for _, v in best.values()]

    def match_variable(self, text: str) -> Variable | None:
        """The single best-scoring declared variable, ignoring groups."""
        matches = self.match_variables(text)
        return max(matches, key=lambda v: v.score(text), default=None)

    def variable_extras(self, text: str) -> tuple[dict, list[str]]:
        """(extras, consumed tokens) for every variable `text` names."""
        extras: dict = {}
        consumed: list[str] = []
        for var in self.match_variables(text):
            extras |= var.extras_for(self)
            consumed += var.matched_text(text)
        return extras, consumed

    def variable_words(self) -> set[str]:
        """Every word a declared variable can be selected by (for the spell-checker)."""
        words: set[str] = set()
        for var in self.variables:
            for kw in var.keywords:
                words |= keyword_words(kw)
        return words

    def variable_labels(self) -> list[str]:
        """Human-readable option names, for help text and error messages."""
        return [v.label for v in self.variables if not v.hidden]

    # -- coverage ---------------------------------------------------------

    def covers(self, start: datetime, end: datetime) -> bool:
        """Does this instrument's observing span overlap [start, end]?"""
        s0, s1 = _iso(self.coverage[0]), _iso(self.coverage[1])
        if s0 is not None and end < s0:
            return False
        if s1 is not None and start > s1:
            return False
        return True

    def check_coverage(self, start: datetime, end: datetime) -> None:
        """Raise a friendly error when the request lies outside `coverage`.

        Says which end of the span was missed, because the two have different
        remedies: "starts" means pick a later date, "stopped observing" means
        the instrument is gone and another one is needed.
        """
        s0, s1 = _iso(self.coverage[0]), _iso(self.coverage[1])
        who = self.display_name.split(" (")[0]
        if s0 is not None and end < s0:
            raise RuntimeError(f"{who} data starts {self.coverage[0]}.")
        if s1 is not None and start > s1:
            raise RuntimeError(f"{who} stopped observing on {self.coverage[1]}.")

    def coverage_error(self) -> str:
        """The observing span as one sentence, for pre-flight refusals."""
        s0, s1 = self.coverage
        if s0 and s1:
            span = f"{s0} – {s1}"
        elif s0:
            span = f"from {s0} onwards"
        else:
            span = f"until {s1}"
        return f"{self.display_name} covers {span}."

    # -- fetching ---------------------------------------------------------

    @abstractmethod
    def fetch(self, start: datetime, end: datetime, **extras: Any) -> PlotSpec:
        """Fetch data for [start, end] (UTC) and return a PlotSpec dict.

        `extras` are source-specific options produced by parse_extras()
        (e.g. an AIA wavelength). Sources that take none ignore them.
        """

    def parse_extras(self, query: str) -> tuple[dict[str, Any], list[str]]:
        """Extract source-specific options from the raw query.

        Returns (extras, consumed_tokens): extras are forwarded to fetch() as
        keyword arguments; consumed_tokens are stripped from the query before
        date parsing so they can't be mistaken for a date (e.g. "171").

        The default resolves the declared `variables`, which is all most
        plugins need; override only for options a keyword table cannot
        express (a satellite number, a flare class threshold), and call
        `variable_extras()` from the override to keep both.
        """
        return self.variable_extras(query)


class Registry:
    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}

    def discover(self) -> None:
        """Import every module in solarapp.sources and register DataSource subclasses."""
        import solarapp.sources as pkg

        for info in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
            for obj in vars(mod).values():
                try:
                    is_plugin = (
                        issubclass(obj, DataSource) and obj is not DataSource and obj.name
                    )
                except TypeError:  # not a class (e.g. dict[str, Any] alias)
                    continue
                if is_plugin:
                    self._sources[obj.name] = obj()

    @property
    def sources(self) -> dict[str, DataSource]:
        return self._sources

    def get(self, name: str) -> DataSource | None:
        return self._sources.get(name)

    def summary_sources(self) -> list[DataSource]:
        """Sources that opted into the bare-date overview, most central first."""
        return sorted(
            (s for s in self._sources.values() if s.summary_rank is not None),
            key=lambda s: (s.summary_rank, s.name),
        )

    def vocabulary(self) -> set[str]:
        """Every word the app understands from plugins: keywords + variables."""
        words: set[str] = set()
        for src in self._sources.values():
            for kw in src.keywords:
                words |= keyword_words(kw)
            words |= src.variable_words()
        return words


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
        _registry.discover()
    return _registry
