"""Multi-source query parsing ("goes and aia 304 yesterday")."""
from datetime import datetime, timezone

from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

REG = get_registry()


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


def req_sources(p):
    return [r.source for r in p.requests]


def test_two_sources():
    p = parse("goes xrs and aia 304 yesterday", REG)
    assert p.ok
    assert req_sources(p) == ["goes_xrs", "sdo_images"]
    assert p.requests[1].extras == {"channel": "aia_304"}
    assert p.source == "goes_xrs"  # back-compat: first request


def test_three_sources_commas():
    p = parse("show goes, stix and lasco c2 for 2026-07-10", REG)
    assert p.ok
    assert req_sources(p) == ["goes_xrs", "solo_stix", "soho_images"]
    assert p.start == d(2026, 7, 10)


def test_range_survives_and_split():
    p = parse("goes and stix between 2026-07-10 and 2026-07-12", REG)
    assert p.ok
    assert req_sources(p) == ["goes_xrs", "solo_stix"]
    assert p.start == d(2026, 7, 10)
    assert p.end == d(2026, 7, 13)  # inclusive end day


def test_same_source_two_channels():
    p = parse("aia 171 and aia 304 yesterday", REG)
    assert p.ok
    assert req_sources(p) == ["sdo_images", "sdo_images"]
    assert [r.extras["channel"] for r in p.requests] == ["aia_171", "aia_304"]


def test_duplicate_request_deduped():
    p = parse("goes and goes xrs yesterday", REG)
    assert p.ok
    assert req_sources(p) == ["goes_xrs"]


def test_single_source_unchanged():
    p = parse("show me the GOES SXR data for the 10th of July 2026", REG)
    assert p.ok
    assert req_sources(p) == ["goes_xrs"]
    assert p.start == d(2026, 7, 10)


def test_conjunction_inside_source_name_not_split():
    # "solar orbiter stix" must stay one source (STIX), not match EUI too
    p = parse("solar orbiter stix yesterday", REG)
    assert p.ok
    assert req_sources(p) == ["solo_stix"]


def test_unmatched_segment_ignored():
    p = parse("goes xrs and the kitchen sink yesterday", REG)
    assert p.ok
    assert req_sources(p) == ["goes_xrs"]


def test_mixed_kinds():
    p = parse("wind waves and eve he ii 304 for 2026-07-11", REG)
    assert p.ok
    assert req_sources(p) == ["wind_waves", "sdo_eve"]
