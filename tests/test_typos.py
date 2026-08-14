"""Typo tolerance: corrections are applied on retry and always reported."""
from datetime import datetime, timezone

import pytest

from solarapp.core.parser import _autocorrect, parse
from solarapp.core.registry import get_registry

REG = get_registry()


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


def sources(p):
    return [r.source for r in p.requests]


# -- corrections that should work ----------------------------------------

def test_misspelled_instrument():
    p = parse("goess xrs yesterday", REG)
    assert p.ok and p.source == "goes_xrs"
    assert ("goess", "goes") in p.corrections


def test_misspelled_date_word():
    p = parse("goes xrs yesterdy", REG)
    assert p.ok
    assert (p.end - p.start).days == 1
    assert ("yesterdy", "yesterday") in p.corrections


def test_misspelled_month():
    p = parse("flares in Januray 2025", REG)
    assert p.ok
    assert p.start == d(2025, 1, 1) and p.end == d(2025, 2, 1)
    assert ("Januray", "january") in p.corrections


def test_multiple_typos():
    p = parse("goess xrs yesterdy", REG)
    assert p.ok and len(p.corrections) == 2


def test_typo_in_multi_source_query():
    p = parse("goes xrs and terminater today", REG)
    assert p.ok
    assert sources(p) == ["goes_xrs", "earth_illumination"]


def test_typo_in_superlative():
    p = parse("bigest flare since 2014", REG)
    assert p.ok
    assert p.source == "flares" and p.extras.get("superlative") is True


def test_correction_note_is_human_readable():
    p = parse("goess xrs yesterday", REG)
    assert "goess" in p.correction_note and "goes" in p.correction_note


def test_transposition():
    # difflib scores transpositions harshly; the cutoff must still admit them
    p = parse("lasoc c3 today", REG)
    assert p.ok and p.source == "soho_images"
    assert ("lasoc", "lasco") in p.corrections


def test_short_grammar_words_corrected():
    p = parse("goes xrs lst 3 days", REG)
    assert p.ok
    assert (p.end - p.start).days == 3
    assert ("lst", "last") in p.corrections


def test_two_short_typos():
    p = parse("goes xrs pst 2 weks", REG)
    assert p.ok and (p.end - p.start).days == 14


# -- guardrails ----------------------------------------------------------

def test_valid_query_untouched():
    p = parse("goes xrs 2026-07-10", REG)
    assert p.ok and p.corrections == []


def test_short_acronyms_not_fuzzy_matched():
    # sot/sdo and eve/eui/eit are 1-2 edits apart; a 3-letter typo must not
    # confidently resolve to the wrong instrument
    corrected, corrections = _autocorrect("xyz 2026-07-10", REG)
    assert corrections == []


@pytest.mark.parametrize("query,expected", [
    ("sot 2013-11-15", "hinode_sot"),
    ("eve yesterday", "sdo_eve"),
    ("eui yesterday", "solo_eui"),
    ("eit today", "soho_images"),
    ("aia 171 today", "sdo_images"),
    ("hmi magnetogram today", "sdo_images"),
])
def test_real_acronyms_resolve_untouched(query, expected):
    p = parse(query, REG)
    assert p.ok and p.source == expected
    assert p.corrections == []


@pytest.mark.parametrize("query", [
    "goes xrs 3",        # a bare number is not a date
    "flares 3 days",     # "3 days" without last/past is not a range
    "goes xrs lst",      # corrected, but still no quantity
])
def test_bare_numbers_do_not_become_year_spans(query):
    # dateutil reads "3" as a day-of-month, which the precision logic would
    # widen to a whole year — a silently enormous request
    p = parse(query, REG)
    assert not p.ok
    assert "No date" in p.errors[0]


def test_autocorrect_preserves_numbers_and_punctuation():
    corrected, _ = _autocorrect("aia 304 from 2026-07-10 to 2026-07-12", REG)
    assert "304" in corrected
    assert "2026-07-10" in corrected and "2026-07-12" in corrected


def test_nonsense_still_fails_with_original_error():
    p = parse("show me the weather in Belfast tomorrow", REG)
    assert not p.ok
    assert "No instrument or quantity" in p.errors[0]


def test_failed_correction_keeps_original_error():
    # a typo'd instrument that corrects to nothing useful should not produce
    # a confusing error about the corrected text
    p = parse("qqqqqqq zzzzzzz", REG)
    assert not p.ok
    assert "No instrument or quantity" in p.errors[0]


def test_real_keywords_are_in_vocabulary():
    # keyword words must never be "corrected" into something else
    for query in ["lasco c3 today", "rhessi 2003-10-28", "hinode sot 2013-11-15",
                  "wind waves 2026-07-11", "eve spectrum yesterday"]:
        _, corrections = _autocorrect(query, REG)
        assert corrections == [], f"{query} was altered: {corrections}"
