from datetime import datetime, timezone

from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

REG = get_registry()


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


def test_flagship_query():
    p = parse("show me the GOES SXR data for the 10th of July 2026", REG)
    assert p.ok
    assert p.source == "goes_xrs"
    assert p.start == d(2026, 7, 10)
    assert p.end == d(2026, 7, 11)


def test_iso_date():
    p = parse("goes xrs 2026-07-10", REG)
    assert p.ok and p.start == d(2026, 7, 10)


def test_range():
    p = parse("plot goes from 2026-07-10 to 2026-07-12", REG)
    assert p.ok
    assert p.start == d(2026, 7, 10)
    assert p.end == d(2026, 7, 13)  # inclusive end day


def test_dayfirst():
    p = parse("goes sxr 10/07/2026", REG)
    assert p.ok and p.start == d(2026, 7, 10)


def test_yesterday():
    p = parse("goes sxr yesterday", REG)
    assert p.ok and (p.end - p.start).days == 1


def test_unknown_source():
    p = parse("show me the weather for tomorrow", REG)
    assert not p.ok and "No data source" in p.errors[0]


def test_missing_date():
    p = parse("show me goes xrs", REG)
    assert not p.ok and "No date" in p.errors[0]
