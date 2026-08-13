"""Result cache: keys, recency-aware TTL, eviction, and isolation from callers."""
from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core import resultcache
from solarapp.core.resultcache import ResultCache


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def now():
    return datetime.now(timezone.utc)


# -- keys ----------------------------------------------------------------

def test_key_distinguishes_everything_that_changes_the_result():
    base = ("goes_xrs", {}, utc(2026, 7, 10), utc(2026, 7, 11))
    k = resultcache.key_for(*base)
    assert k != resultcache.key_for("rhessi", {}, base[2], base[3])
    assert k != resultcache.key_for("goes_xrs", {"channel": "aia_171"}, base[2], base[3])
    assert k != resultcache.key_for("goes_xrs", {}, utc(2026, 7, 9), base[3])
    assert k != resultcache.key_for("goes_xrs", {}, base[2], utc(2026, 7, 12))


def test_key_is_stable_regardless_of_extras_order():
    a = resultcache.key_for("sdo_images", {"channel": "aia_131", "fov": 500.0},
                            utc(2026, 7, 10), utc(2026, 7, 11))
    b = resultcache.key_for("sdo_images", {"fov": 500.0, "channel": "aia_131"},
                            utc(2026, 7, 10), utc(2026, 7, 11))
    assert a == b


def test_key_separates_similar_float_options():
    # a 500" crop and a 501" crop are different images
    t = (utc(2026, 7, 10), utc(2026, 7, 11))
    assert resultcache.key_for("sdo_images", {"fov": 500.0}, *t) != \
        resultcache.key_for("sdo_images", {"fov": 501.0}, *t)


# -- recency-aware TTL ---------------------------------------------------

def test_settled_window_caches_hard():
    assert resultcache.ttl_for(utc(2020, 1, 2)) == resultcache.TTL_SETTLED


def test_window_reaching_the_present_expires_quickly():
    # data is still arriving for recent windows, so serving them for hours
    # would show a stale, half-filled day
    assert resultcache.ttl_for(now()) == resultcache.TTL_RECENT
    assert resultcache.ttl_for(now() - timedelta(hours=6)) == resultcache.TTL_RECENT


def test_ttl_boundary_follows_the_archive_lag():
    lag = resultcache.ARCHIVE_LAG_DAYS
    assert resultcache.ttl_for(now() - timedelta(days=lag + 1)) == resultcache.TTL_SETTLED
    assert resultcache.ttl_for(now() - timedelta(days=lag - 1)) == resultcache.TTL_RECENT


# -- store and retrieve --------------------------------------------------

def test_hit_and_miss():
    c = ResultCache()
    assert c.get("k") is None
    c.put("k", {"title": "x"}, utc(2020, 1, 1))
    assert c.get("k")["title"] == "x"
    assert (c.hits, c.misses) == (1, 1)


def test_expiry(monkeypatch):
    c = ResultCache()
    clock = [1000.0]
    monkeypatch.setattr(resultcache.time, "monotonic", lambda: clock[0])
    c.put("k", {"title": "x"}, now())          # recent window: TTL_RECENT
    assert c.get("k") is not None
    clock[0] += resultcache.TTL_RECENT + 1
    assert c.get("k") is None


def test_expired_entry_frees_its_bytes(monkeypatch):
    c = ResultCache()
    clock = [1000.0]
    monkeypatch.setattr(resultcache.time, "monotonic", lambda: clock[0])
    c.put("k", {"title": "x" * 1000}, now())
    assert c.bytes > 0
    clock[0] += resultcache.TTL_RECENT + 1
    c.get("k")
    assert c.bytes == 0 and c.stats()["entries"] == 0


def test_mutating_a_returned_spec_does_not_poison_the_cache():
    # the server adds timings/parsed/note to whatever it returns; without a copy
    # those would be written into the cached object and served to the next caller
    c = ResultCache()
    c.put("k", {"title": "x"}, utc(2020, 1, 1))
    first = c.get("k")
    first["timings"] = {"total": 5}
    first["note"] = "corrections applied"
    second = c.get("k")
    assert "timings" not in second and "note" not in second


def test_mutating_the_stored_spec_afterwards_does_not_change_the_cache():
    c = ResultCache()
    spec = {"title": "x"}
    c.put("k", spec, utc(2020, 1, 1))
    spec["title"] = "mutated"
    assert c.get("k")["title"] == "x"


# -- bounds --------------------------------------------------------------

def test_entry_count_bound_evicts_oldest():
    c = ResultCache(max_entries=3)
    for i in range(5):
        c.put(f"k{i}", {"i": i}, utc(2020, 1, 1))
    assert c.stats()["entries"] == 3
    assert c.get("k0") is None and c.get("k4") is not None


def test_lru_order_keeps_recently_read_entries():
    c = ResultCache(max_entries=2)
    c.put("a", {"v": 1}, utc(2020, 1, 1))
    c.put("b", {"v": 2}, utc(2020, 1, 1))
    c.get("a")                                  # 'a' is now the most recent
    c.put("c", {"v": 3}, utc(2020, 1, 1))       # evicts 'b', not 'a'
    assert c.get("a") is not None
    assert c.get("b") is None


def test_byte_budget_is_enforced():
    c = ResultCache(max_bytes=4000, max_entries=100)
    for i in range(20):
        c.put(f"k{i}", {"blob": "x" * 1000}, utc(2020, 1, 1))
    assert c.bytes <= 4000
    assert c.stats()["entries"] < 20


def test_oversized_entry_is_skipped_not_cached():
    # one huge spectrogram must not evict everything else
    c = ResultCache(max_bytes=1000)
    c.put("small", {"v": "x" * 100}, utc(2020, 1, 1))
    c.put("huge", {"v": "x" * 5000}, utc(2020, 1, 1))
    assert c.get("huge") is None
    assert c.get("small") is not None


def test_reinsert_does_not_double_count_bytes():
    c = ResultCache()
    for _ in range(5):
        c.put("k", {"v": "x" * 500}, utc(2020, 1, 1))
    assert c.stats()["entries"] == 1
    assert c.bytes < 5 * 600


def test_unserialisable_spec_is_skipped():
    c = ResultCache()
    c.put("k", {"bad": {1, 2, 3}}, utc(2020, 1, 1))   # a set is not JSON
    assert c.get("k") is None


def test_clear():
    c = ResultCache()
    c.put("k", {"v": 1}, utc(2020, 1, 1))
    c.clear()
    assert c.get("k") is None and c.bytes == 0


# -- switch --------------------------------------------------------------

def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SOLARQUICK_CACHE", "0")
    assert resultcache.enabled() is False
    monkeypatch.setenv("SOLARQUICK_CACHE", "1")
    assert resultcache.enabled() is True


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("SOLARQUICK_CACHE", raising=False)
    assert resultcache.enabled() is True


# -- concurrency ---------------------------------------------------------

def test_thread_safe_under_concurrent_use():
    from concurrent.futures import ThreadPoolExecutor
    c = ResultCache(max_entries=16)

    def hammer(i):
        for j in range(40):
            c.put(f"k{(i + j) % 30}", {"v": "x" * 200}, utc(2020, 1, 1))
            c.get(f"k{j % 30}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(hammer, range(8)))
    # the invariants must survive the race, not just avoid crashing
    assert c.stats()["entries"] <= 16
    assert c.bytes >= 0
    assert c.bytes <= c.max_bytes
