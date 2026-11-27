"""GOES-class queries: "8 may 2024 01:41 X1.0" used to fail outright, and
"X1.0 flare 8 may 2024" silently searched 8 May 2001."""
from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core import events, hek, viewpoints
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry
from solarapp.sources import viewpoints as vp_source

REG = get_registry()
UTC = timezone.utc


def t(*a):
    return datetime(*a, tzinfo=UTC)


def fl(cls, peak):
    return hek.Flare(cls, peak - timedelta(minutes=20), peak, peak + timedelta(minutes=30),
                     500.0, -200.0, 3664)


# 8 May 2024: two X1.0s and an M-class between them
MAY8 = [fl("X1.0", t(2024, 5, 8, 1, 41)), fl("M7.2", t(2024, 5, 8, 3, 27)),
        fl("X1.0", t(2024, 5, 8, 5, 9))]


@pytest.fixture
def catalog(monkeypatch):
    calls = []

    def search(start, end, min_class=None):
        calls.append((start, end, min_class))
        floor = hek.class_rank(min_class + ("1.0" if min_class and len(min_class) == 1 else "")) if min_class else 0
        return [f for f in rows if start <= f.peak <= end and f.rank >= floor]

    rows = list(MAY8)
    monkeypatch.setattr(hek, "search_flares", search)
    monkeypatch.setattr(viewpoints, "companions", lambda t, **site: [])
    monkeypatch.setattr(vp_source, "region_site", lambda flare: None)
    return rows


def resolve(q):
    p = parse(q, REG)
    assert p.ok, p.errors
    events.expand(p)
    return p


# -- parsing ---------------------------------------------------------------

@pytest.mark.parametrize("q, cls, start", [
    ("8 may 2024 01:41 X1.0", "X1.0", t(2024, 5, 8, 1, 41)),
    ("X1.0 flare 8 may 2024", "X1.0", t(2024, 5, 8)),          # was 2001-05-08
    ("X1 class flare 8 may 2024", "X1.0", t(2024, 5, 8)),      # was 2001-05-08
    ("the X9.3 flare 2017-09-06", "X9.3", t(2017, 9, 6)),
    ("m5.2 2024-05-08", "M5.2", t(2024, 5, 8)),
    ("X17 28 oct 2003", "X17.0", t(2003, 10, 28)),
])
def test_class_names_a_flare(q, cls, start):
    p = parse(q, REG)
    assert p.ok and p.source == "flares"
    assert p.extras["goes_class"] == cls and p.start == start


def test_class_beside_an_instrument_keeps_both():
    p = parse("goes xrs and aia 131 X1.0 8 may 2024", REG)   # was year 0131
    assert [r.source for r in p.requests] == ["goes_xrs", "sdo_images", "flares"]
    assert p.requests[1].extras == {"channel": "aia_131"} and p.start == t(2024, 5, 8)


@pytest.mark.parametrize("q, extras", [
    ("flares above m5 yesterday", {"min_class": "M5"}),
    ("x-class flares in july 2026", {"min_class": "X"}),
])
def test_thresholds_are_not_classes(q, extras):
    assert parse(q, REG).extras == extras


def test_lasco_c3_is_not_a_flare():
    assert parse("lasco c3 8 may 2024", REG).requests[0].extras == {"channel": "lasco_c3"}


def test_bare_class_searches_the_catalog():
    p = parse("X9.3", REG)
    assert p.ok and p.extras["catalog_wide"] and p.start == t(1996, 1, 1)


@pytest.mark.parametrize("q, at", [
    ("flare 8 may 2024 01:41", True),
    ("flares 8 may 2024 01:41", False),
    ("flare list 8 may 2024 01:41", False),
    ("flare 8 may 2024", False),
])
def test_singular_flare_at_a_minute_pinpoints(q, at):
    assert parse(q, REG).extras.get("at", False) is at


@pytest.mark.parametrize("q, start", [
    ("2024/05/08 01:41", t(2024, 5, 8, 1, 41)),     # was 5 August
    ("8th May 2024 01:41", t(2024, 5, 8, 1, 41)),   # "th" was unknown
    ("8 May 2024 at 1:41am", t(2024, 5, 8, 1, 41)), # "am" was unknown
])
def test_date_spellings(q, start):
    p = parse(q, REG)
    assert p.ok and p.start == start


# -- resolution ------------------------------------------------------------

def test_the_failed_prompt_opens_the_0141_flare(catalog):
    p = resolve("8 may 2024 01:41 X1.0")
    flares = next(r for r in p.requests if r.source == "flares")
    assert flares.extras["pin_peak"] == "2024-05-08T01:41:00+00:00"
    assert "goes_xrs" in [r.source for r in p.requests]  # event view companions
    assert not p.notes


def test_time_picks_the_nearer_of_two(catalog):
    p = resolve("X1.0 8 may 2024 05:00")
    assert next(r for r in p.requests if r.source == "flares").extras["pin_peak"].startswith("2024-05-08T05:09")


def test_a_day_with_two_gives_the_list(catalog):
    p = resolve("X1.0 8 may 2024")
    assert p.requests[0].extras["class_eq"] == "X1.0" and len(p.requests) == 1
    assert "2 X1.0 flares" in p.correction_note


def test_one_of_its_class_on_the_day(catalog):
    p = resolve("M7.2 8 may 2024")
    assert p.requests[0].extras["pin_peak"].startswith("2024-05-08T03:27")


def test_near_class_is_used_and_said(catalog):
    p = resolve("8 may 2024 03:20 M7.0")
    assert p.requests[0].extras["pin_peak"].startswith("2024-05-08T03:27")
    assert "No M7.0" in p.correction_note


def test_no_flare_of_that_letter_raises(catalog):
    with pytest.raises(RuntimeError, match="No C-class flare"):
        resolve("8 may 2024 01:41 C3.4")


def test_catalog_wide_x_only(catalog):
    catalog.append(fl("X9.3", t(2017, 9, 6, 12, 2)))
    p = resolve("X9.3")
    assert p.requests[0].extras["pin_peak"].startswith("2017-09-06T12:02")
    with pytest.raises(RuntimeError, match="too common"):
        resolve("M5.2")


def test_fetch_filters_the_list_to_the_class(catalog, monkeypatch):
    from solarapp.sources import flares as src
    monkeypatch.setattr(src, "flare_sites_from_regions", lambda f: {})
    spec = REG.get("flares").fetch(t(2024, 5, 8), t(2024, 5, 9), class_eq="X1.0")
    assert [e["class"] for e in spec["events"]] == ["X1.0", "X1.0"]
