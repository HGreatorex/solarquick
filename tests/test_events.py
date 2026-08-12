from datetime import datetime, timedelta, timezone

from solarapp.core.hek import class_rank
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

REG = get_registry()
NOW = datetime.now(timezone.utc)


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


# -- GOES class ordering -------------------------------------------------

def test_class_rank_ordering():
    order = ["B2.0", "C1.0", "C9.9", "M1.0", "M5.2", "X1.0", "X9.3", "X17"]
    ranks = [class_rank(c) for c in order]
    assert ranks == sorted(ranks)
    assert class_rank("") == 0.0 and class_rank("?") == 0.0


# -- flare query parsing -------------------------------------------------

def test_flare_list_query():
    p = parse("flares yesterday", REG)
    assert p.ok and p.source == "flares" and p.extras == {}


def test_class_filter():
    p = parse("x-class flares in july 2026", REG)
    assert p.ok
    assert p.extras == {"min_class": "X"}
    assert p.start == d(2026, 7, 1) and p.end == d(2026, 8, 1)  # month span


def test_above_filter():
    p = parse("flares above m5 yesterday", REG)
    assert p.ok and p.extras == {"min_class": "M5"}


def test_superlative():
    p = parse("biggest flare since 2014", REG)
    assert p.ok
    assert p.extras.get("superlative") is True
    assert p.start == d(2014, 1, 1)
    assert (NOW - p.end) < timedelta(minutes=5)  # end ≈ now


def test_flare_at():
    p = parse("flare at 2017-09-06 12:02", REG)
    assert p.ok
    assert p.extras.get("at") is True
    assert p.start == datetime(2017, 9, 6, 12, 2, tzinfo=timezone.utc)


# -- new time grammar ----------------------------------------------------

def test_last_week():
    p = parse("flares last week", REG)
    assert p.ok and timedelta(days=6, hours=23) < p.end - p.start <= timedelta(days=7)


def test_past_n_days():
    p = parse("goes xrs past 3 days", REG)
    assert p.ok and (p.end - p.start) == timedelta(days=3)


def test_year_span():
    p = parse("flares in 2024", REG)
    assert p.ok and p.start == d(2024, 1, 1) and p.end == d(2025, 1, 1)


def test_month_span():
    p = parse("flares in march 2025", REG)
    assert p.ok and p.start == d(2025, 3, 1) and p.end == d(2025, 4, 1)


def test_exact_day_still_day_span():
    p = parse("goes xrs 10 july 2026", REG)
    assert p.ok and p.start == d(2026, 7, 10) and p.end == d(2026, 7, 11)


# -- time-of-day cropping ------------------------------------------------

def test_tod_range_crop():
    p = parse("goes xrs 2026-07-10 14:00 to 15:30", REG)
    assert p.ok
    assert p.start == datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    assert p.end == datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)


def test_tod_at_crop():
    p = parse("goes xrs yesterday at 14:30", REG)
    assert p.ok
    assert p.start.hour == 14 and p.start.minute == 30
    assert p.end - p.start == timedelta(hours=1)
