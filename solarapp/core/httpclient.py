"""One pooled HTTP session, plus a small TTL cache for hot feed URLs.

Every source talks to a handful of hosts repeatedly, so a fresh TLS handshake
per call was pure overhead. `session()` is shared process-wide and is safe to
use from the fetch thread pool (requests.Session is thread-safe for plain
request calls).

`get_json_cached` exists for one specific case: NOAA SWPC publishes a single
rolling 7-day JSON feed of ~4.6 MB, and every recent-GOES query downloaded all
of it to use one day. The result cache only helps for an *identical* repeat
query, so asking for yesterday and then today re-pulled the whole feed. A short
TTL is safe here because the feed is a rolling window that only gains samples.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

FEED_TTL = 60.0

_session: requests.Session | None = None
_session_lock = threading.Lock()
_feeds: dict[str, tuple[float, Any]] = {}
_feed_lock = threading.Lock()


def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            # a few sources fan out concurrently; keep enough pooled sockets
            # that they are not serialised behind the default pool size
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=8, pool_maxsize=16, max_retries=0,
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session


def get_json_cached(url: str, timeout: int = 30, ttl: float = FEED_TTL) -> Any:
    """GET a JSON feed, reusing a recent response.

    Only for feeds that are append-mostly rolling windows — never for anything
    where a stale read could misrepresent the data.
    """
    now = time.monotonic()
    with _feed_lock:
        hit = _feeds.get(url)
        if hit is not None and now < hit[0]:
            return hit[1]

    payload = session().get(url, timeout=timeout).json()

    with _feed_lock:
        _feeds[url] = (now + ttl, payload)
    return payload


def clear_feeds() -> None:
    with _feed_lock:
        _feeds.clear()
