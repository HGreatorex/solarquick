"""The overview a bare date deserves.

"2017-09-06" names no instrument, and the honest reading of it is not an error
but a question: *what happened that day?* This module answers it by composing
the sources that opted in with `summary_rank` and were actually observing
then — X-ray flux, the flare list, an EUV filmstrip, a coronagraph.

Membership is declared, never listed here: a new plugin joins the overview by
setting `summary_rank` (and optionally `summary_extras`) in its own file, and
drops out automatically for dates outside its `coverage`.
"""
from __future__ import annotations

from datetime import datetime

from .registry import Registry, keyword_regex

# Phrases that ask for the overview outright. Kept here rather than in a
# plugin because the summary is a composition of sources, not a source.
KEYWORDS = [
    "summary", "overview", "what happened", "whats happening", "what's happening",
    "space weather", "solar activity", "activity", "conditions", "everything",
    "all data", "all sources", "anything interesting", "recap", "digest",
]
MAX_SOURCES = 4


def asked_for(query: str) -> bool:
    return any(keyword_regex(kw).search(query) for kw in KEYWORDS)


def compose(registry: Registry, start: datetime, end: datetime
            ) -> list[tuple[str, dict]]:
    """(source, extras) pairs for an overview of [start, end], best first.

    Sources outside their observing span are dropped rather than fetched and
    failed: an overview of 2003 should quietly be GOES XRS + flares + LASCO,
    not those plus two errors about SDO not existing yet.
    """
    out = []
    for src in registry.summary_sources():
        if not src.covers(start, end):
            continue
        out.append((src.name, dict(src.summary_extras)))
        if len(out) >= MAX_SOURCES:
            break
    return out


def note(registry: Registry, requests) -> str:
    """One line telling the user what the overview chose, and how to narrow it."""
    names = ", ".join(registry.get(r.source).display_name.split(" (")[0]
                      for r in requests)
    return (f"No instrument named, so this is an overview: {names}. "
            f"Name one (or several) to go deeper.")
