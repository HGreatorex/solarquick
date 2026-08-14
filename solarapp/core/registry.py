"""Data source base class and auto-discovery registry.

Adding a data source = dropping a module into solarapp/sources/ containing a
DataSource subclass. Nothing else to edit.
"""
from __future__ import annotations

import functools
import importlib
import pkgutil
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

PlotSpec = dict[str, Any]  # see ARCHITECTURE.md for schema

# Separators inside a keyword that users write inconsistently, and that carry no
# meaning: "x-ray", "x ray" and "xray" are the same word, as are "wind/waves"
# and "wind waves".
_SEP = r"[\s\-/]*"


@functools.lru_cache(maxsize=512)
def keyword_regex(keyword: str) -> re.Pattern:
    """Word-boundary matcher for one keyword, tolerant of how it's written.

    Internal hyphens, slashes and spaces are interchangeable and optional, and
    a trailing plural "s" is allowed, so the single keyword "x-ray" matches
    "x-ray", "x ray", "xray", "x-rays" and "xrays". This is why the keyword
    lists stay short: spelling variants are the matcher's job, not the
    plugin author's, and every unlisted variant used to fail outright.
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-/]+", keyword.strip()) if p]
    return re.compile(rf"\b{_SEP.join(parts)}s?\b", re.I)


class DataSource(ABC):
    """Base class for all data source plugins."""

    name: str = ""  # unique id, e.g. "goes_xrs"
    display_name: str = ""
    keywords: list[str] = []  # phrases the parser matches (lowercase)

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
        """
        return {}, []


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


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
        _registry.discover()
    return _registry
