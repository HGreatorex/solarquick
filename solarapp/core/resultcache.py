"""In-memory cache of fetched PlotSpecs, keyed on the exact request.

Two things make this safe to do at all:

1. The parser resolves relative words before fetching, so a key holds concrete
   datetimes — "yesterday" cannot be served from a cache built yesterday.
2. TTL depends on how recent the requested window is. A window that ended over
   ARCHIVE_LAG_DAYS ago is effectively immutable and caches for hours; a window
   touching the present is still being filled by the archives, so it expires in
   minutes. Getting this backwards would silently serve stale "today" data,
   which is worse than being slow.

Failures are never cached: an NCEI 503 must not turn into an hour of phantom
outage, and a retry should really retry.

Set SOLARQUICK_CACHE=0 to disable — worth doing when hunting a data bug, so you
can be certain you are looking at a fresh fetch.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

# Beyond this age an archive window is treated as settled. Tied to a real
# property of the sources: SWPC's realtime feed covers 7 days and the NCEI
# science files land within a few days, so an older window rarely changes.
ARCHIVE_LAG_DAYS = 7
TTL_SETTLED = 6 * 3600.0   # historical window: cache hard
TTL_RECENT = 120.0         # still filling: just enough to dedupe a burst
MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 64


def enabled() -> bool:
    return os.environ.get("SOLARQUICK_CACHE", "1") != "0"


def key_for(source: str, extras: dict[str, Any], start: datetime, end: datetime) -> str:
    opts = ",".join(f"{k}={extras[k]!r}" for k in sorted(extras))
    return f"{source}|{opts}|{start.isoformat()}|{end.isoformat()}"


def ttl_for(end: datetime) -> float:
    """Long for settled windows, short for anything reaching into the present."""
    age = datetime.now(timezone.utc) - end
    return TTL_SETTLED if age > timedelta(days=ARCHIVE_LAG_DAYS) else TTL_RECENT


class ResultCache:
    """LRU with a byte budget. Thread-safe: fetches run in a thread pool."""

    def __init__(self, max_bytes: int = MAX_BYTES, max_entries: int = MAX_ENTRIES):
        self._entries: OrderedDict[str, tuple[float, int, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict | None:
        """Return a shallow copy of the cached spec, or None.

        The copy matters: the server adds `timings`, `parsed` and `note` to the
        spec it returns, and without it those would be written back into the
        cached object and served to the next caller. Only top-level keys are
        copied — deep-copying a multi-MB spectrogram would cost more than the
        fetch it saves — so callers must not mutate nested values in place.
        """
        with self._lock:
            found = self._entries.get(key)
            if found is None:
                self.misses += 1
                return None
            expires, _, spec = found
            if time.monotonic() > expires:
                self._drop(key)
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return dict(spec)

    def peek(self, key: str) -> bool:
        """Is `key` cached and live? Counts as neither a hit nor a miss.

        Used by the progress display to announce "cached" before the fetch
        starts. It must not disturb the LRU order or the statistics, or
        looking at the plan would change what the plan reports.
        """
        with self._lock:
            found = self._entries.get(key)
            return found is not None and time.monotonic() <= found[0]

    def put(self, key: str, spec: dict, end: datetime) -> None:
        try:
            # no `default=`: a spec that cannot round-trip as JSON is a plugin
            # bug, and refusing to cache it keeps that bug reproducible
            size = len(json.dumps(spec))
        except (TypeError, ValueError):
            return
        if size > self.max_bytes:
            return  # one entry must not evict the whole cache
        with self._lock:
            if key in self._entries:
                self._drop(key)
            self._entries[key] = (time.monotonic() + ttl_for(end), size, dict(spec))
            self.bytes += size
            while self._entries and (
                self.bytes > self.max_bytes or len(self._entries) > self.max_entries
            ):
                self._drop(next(iter(self._entries)))

    def _drop(self, key: str) -> None:
        """Remove one entry. Caller holds the lock."""
        _, size, _ = self._entries.pop(key)
        self.bytes -= size

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.bytes = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "mib": round(self.bytes / 1048576, 1),
                "hits": self.hits,
                "misses": self.misses,
            }


_cache = ResultCache()


def get_cache() -> ResultCache:
    return _cache
