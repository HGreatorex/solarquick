"""Data source base class and auto-discovery registry.

Adding a data source = dropping a module into solarapp/sources/ containing a
DataSource subclass. Nothing else to edit.
"""
from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

PlotSpec = dict[str, Any]  # see ARCHITECTURE.md for schema


class DataSource(ABC):
    """Base class for all data source plugins."""

    name: str = ""  # unique id, e.g. "goes_xrs"
    display_name: str = ""
    keywords: list[str] = []  # phrases the parser matches (lowercase)

    @abstractmethod
    def fetch(self, start: datetime, end: datetime) -> PlotSpec:
        """Fetch data for [start, end] (UTC) and return a PlotSpec dict."""


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
