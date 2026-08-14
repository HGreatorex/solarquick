"""How long each source usually takes, learned from how long it took.

A progress display that only counts upwards tells the user nothing about
whether to wait. These are the numbers behind "usually ~4 s": an exponentially
weighted mean per source, persisted to ~/.cache/solarapp/timings.json so the
estimate survives a restart and improves with use.

Deliberately *not* declared by the plugins. What a fetch costs depends on the
archive, the window, the network and the local file cache — a hardcoded
"typical" number would be wrong on every machine but the one it was measured
on. Nothing here affects what is fetched; a missing or nonsense file only
costs the display its estimate.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

STORE = Path.home() / ".cache" / "solarapp" / "timings.json"
# Weight of the newest observation. High enough to follow a machine getting a
# warm file cache, low enough that one archive stall doesn't become the norm.
ALPHA = 0.3
# Below this many observations an estimate is a guess, not a measurement, and
# is reported as such (the UI shows an indeterminate bar instead of a countdown).
CONFIDENT_AFTER = 3


def enabled() -> bool:
    return os.environ.get("SOLARQUICK_TIMINGS", "1") != "0"


class Timings:
    """Per-source rolling means, kept on disk. Thread-safe: fetches are parallel."""

    def __init__(self, path: Path = STORE):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}  # first run, or a file someone else's version wrote

    def record(self, source: str, ms: int) -> None:
        """Fold one observed duration into the mean for `source`."""
        if ms <= 0 or not enabled():
            return
        with self._lock:
            entry = self._data.get(source)
            if entry is None:
                self._data[source] = {"ms": float(ms), "n": 1}
            else:
                entry["ms"] = (1 - ALPHA) * entry["ms"] + ALPHA * ms
                entry["n"] = entry.get("n", 1) + 1
            self._save()

    def estimate(self, source: str) -> tuple[int, bool] | None:
        """(milliseconds, confident) for `source`, or None if never measured."""
        entry = self._data.get(source) if enabled() else None
        if not entry:
            return None
        return round(entry["ms"]), entry.get("n", 1) >= CONFIDENT_AFTER

    def _save(self) -> None:
        # called under the lock; write via a temp file so a crash mid-write
        # cannot leave a truncated file that then reads as "no history"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.part")
            tmp.write_text(json.dumps(self._data))
            tmp.replace(self.path)
        except OSError:
            pass  # an unwritable cache dir must never fail a query

    def stats(self) -> dict:
        return {k: {"ms": round(v["ms"]), "n": v.get("n", 1)} for k, v in self._data.items()}


_timings: Timings | None = None


def get_timings() -> Timings:
    global _timings
    if _timings is None:
        _timings = Timings()
    return _timings
