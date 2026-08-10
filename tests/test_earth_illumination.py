from datetime import datetime, timezone

import pytest

from solarapp.core.parser import parse
from solarapp.core.registry import get_registry
from solarapp.sources.earth_illumination import (
    night_band,
    subsolar_point,
    terminator_curve,
)

REG = get_registry()


def test_discovered_and_routed():
    for q in ["terminator today", "earth illumination 2026-06-21",
              "day night map yesterday", "subsolar point today"]:
        p = parse(q, REG)
        assert p.ok and p.source == "earth_illumination", q


def test_subsolar_equinox_lat_zero():
    lat, _ = subsolar_point(datetime(2026, 3, 20, 14, 46, tzinfo=timezone.utc))
    assert abs(lat) < 0.5


def test_subsolar_june_solstice():
    lat, _ = subsolar_point(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
    assert lat == pytest.approx(23.43, abs=0.1)


def test_subsolar_lon_near_zero_at_noon_ut():
    # equation of time keeps the subsolar point within ~4° of Greenwich at noon
    _, lon = subsolar_point(datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))
    assert abs(lon) < 5


def test_night_band_equinox_geometry():
    # dec=0, subsolar lon=0: darkness is the |lon| > 90 hemisphere at every lat
    band = night_band(0.0, 0.0, 90.0)
    pts = [(lo, la) for lo, la in zip(band["lon"], band["lat"]) if lo is not None]
    assert pts, "band should not be empty"
    assert all(abs(lo) >= 89.9 for lo, _ in pts)


def test_polar_night_full_parallel():
    # June: 80°S is in polar night — its parallel is shaded end to end
    band = night_band(23.4, 0.0, 90.0)
    lons_at_80s = [lo for lo, la in zip(band["lon"], band["lat"])
                   if lo is not None and la == pytest.approx(-80.0)]
    assert min(lons_at_80s) == -180.0 and max(lons_at_80s) == 180.0


def test_midnight_sun_unshaded():
    # June: 80°N has midnight sun — no shading at all on that parallel
    band = night_band(23.4, 0.0, 90.0)
    assert not [lo for lo, la in zip(band["lon"], band["lat"])
                if lo is not None and la == pytest.approx(80.0)]


def test_terminator_touches_polar_circles():
    # dec=23.4: terminator reaches ±(90−23.4)° latitude
    curve = terminator_curve(23.4, 0.0)
    lats = [la for la in curve["lat"] if la is not None]
    assert max(lats) == pytest.approx(90 - 23.4, abs=0.2)
    assert min(lats) == pytest.approx(-(90 - 23.4), abs=0.2)


def test_terminator_equinox_meridians():
    curve = terminator_curve(0.0, 0.0)
    lons = {round(lo) for lo in curve["lon"] if lo is not None}
    assert lons == {90, -90}


def test_multi_query_with_terminator():
    p = parse("goes xrs and terminator yesterday", REG)
    assert p.ok
    assert [r.source for r in p.requests] == ["goes_xrs", "earth_illumination"]
