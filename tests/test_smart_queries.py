"""Query shapes that used to need a human to rewrite them.

Segments that name only an option, quantities named without their instrument,
bare dates, and the errors that come back when none of it works.
"""
import pytest

from solarapp.core import summary as summary_view
from solarapp.core.parser import names_only_a_date, parse
from solarapp.core.registry import get_registry

REG = get_registry()


def sources(query):
    p = parse(query, REG)
    assert p.ok, p.errors
    return [(r.source, r.extras) for r in p.requests]


# -- a segment that names only an option ---------------------------------


def test_a_list_of_channels_repeats_the_instrument():
    assert sources("aia 171, 193 and 211 yesterday") == [
        ("sdo_images", {"channel": "aia_171"}),
        ("sdo_images", {"channel": "aia_193"}),
        ("sdo_images", {"channel": "aia_211"}),
    ]


def test_the_instrument_carries_across_conjunctions():
    assert sources("euvs 304 and 121.6 yesterday") == [
        ("goes_euvs", {"channel": "irr_304"}),
        ("goes_euvs", {"channel": "irr_1216"}),
    ]


def test_a_second_instrument_still_starts_its_own_request():
    assert [s for s, _ in sources("aia 171, 304 and goes xrs yesterday")] == [
        "sdo_images", "sdo_images", "goes_xrs",
    ]


def test_duplicate_segments_collapse():
    assert len(sources("aia 171 and aia 171 yesterday")) == 1


# -- a quantity named without its instrument -----------------------------


@pytest.mark.parametrize("query,source", [
    ("sunspots yesterday", "sdo_images"),
    ("magnetogram yesterday", "sdo_images"),
    ("zirconium channel yesterday", "proba2_lyra"),
])
def test_an_option_can_name_its_own_source(query, source):
    assert sources(query)[0][0] == source


def test_an_option_shared_by_several_sources_is_a_question():
    p = parse("he ii yesterday", REG)
    assert not p.ok
    assert "could mean several instruments" in p.errors[0]
    assert "GOES EUVS" in p.errors[0] and "SDO EVE" in p.errors[0]


# -- bare dates ----------------------------------------------------------


def test_a_bare_date_composes_an_overview():
    got = sources("2017-09-06")
    assert [s for s, _ in got] == ["goes_xrs", "flares", "solar_regions",
                                   "sdo_images", "soho_images"]
    p = parse("2017-09-06", REG)
    assert "overview" in p.summary and "GOES XRS" in p.summary


def test_the_overview_drops_instruments_that_were_not_flying():
    got = [s for s, _ in sources("everything on 2003-10-28")]
    assert "sdo_images" not in got  # SDO launched in 2010
    assert got == ["goes_xrs", "flares", "solar_regions", "soho_images"]


def test_asking_for_a_summary_by_name_needs_no_apology():
    p = parse("what happened yesterday", REG)
    assert p.ok and p.summary is None  # they asked; no need to explain
    assert len(p.requests) == summary_view.MAX_SOURCES


def test_a_date_before_every_instrument_is_an_honest_error():
    p = parse("summary of 1985-01-01", REG)
    assert not p.ok
    assert "was observing" in p.errors[0]


def test_a_query_that_names_something_unsupported_is_not_an_overview():
    """The overview must not become a wrong answer to every question."""
    p = parse("solar wind speed yesterday", REG)
    assert not p.ok
    assert not names_only_a_date("solar wind speed yesterday")
    assert names_only_a_date("show me 2017-09-06")
    assert names_only_a_date("what about last wednesday")


def test_summary_membership_is_declared_by_the_plugins():
    ranked = [s.name for s in REG.summary_sources()]
    assert ranked[0] == "goes_xrs"  # lowest summary_rank wins
    assert all(REG.get(n).summary_rank is not None for n in ranked)
    p = parse("2017-09-06", REG)
    assert len(summary_view.compose(REG, p.start, p.end)) <= summary_view.MAX_SOURCES


# -- errors that help ----------------------------------------------------


def test_an_unknown_word_gets_a_suggestion():
    p = parse("show me the coronograph yesterday", REG)
    # "coronograph" is corrected to "coronagraph" and routes to LASCO
    assert p.ok, p.errors
    assert p.source == "soho_images"


def test_a_hopeless_query_lists_what_is_available():
    p = parse("neutrino flux yesterday", REG)
    assert not p.ok
    assert "Available:" in p.errors[0]
    assert "overview" in p.errors[0]
