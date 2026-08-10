from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core.parser import parse
from solarapp.core.registry import get_registry
from solarapp.sources.positions import MAX_SAMPLES, sample_epochs, to_xy

REG = get_registry()


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


def test_discovered():
    assert REG.get("positions") is not None


def test_keywords_route_to_positions():
    for q in [
        "positions today",
        "spacecraft positions 2026-07-10",
        "where is psp today",
        "where is solar orbiter today",  # tie with EUI broken by registry order
        "planet positions yesterday",
    ]:
        p = parse(q, REG)
        assert p.ok and p.source == "positions", q


def test_solar_orbiter_images_still_route_to_eui():
    p = parse("solar orbiter fsi 174 yesterday", REG)
    assert p.ok and p.source == "solo_eui"


def test_single_day_is_one_epoch():
    ts = sample_epochs(d(2026, 7, 10), d(2026, 7, 11))
    assert ts == [d(2026, 7, 10) + timedelta(hours=12)]


def test_range_samples_bounded():
    ts = sample_epochs(d(2026, 6, 1), d(2026, 8, 1))
    assert len(ts) == MAX_SAMPLES
    assert ts[0] == d(2026, 6, 1) and ts[-1] == d(2026, 8, 1)


def test_to_xy_earth_convention():
    x, y = to_xy(0.0, 1.0)  # Earth: lon 0 → +x axis
    assert x == pytest.approx(1.0) and y == pytest.approx(0.0)
    x, y = to_xy(90.0, 0.5)
    assert x == pytest.approx(0.0, abs=1e-12) and y == pytest.approx(0.5)


def test_positions_in_multi_query():
    p = parse("goes xrs and positions today", REG)
    assert p.ok
    assert [r.source for r in p.requests] == ["goes_xrs", "positions"]
