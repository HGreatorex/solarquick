"""The ways people write "when".

"Now" is pinned to Friday 2026-08-14 12:00 UT throughout, so weekday and
relative phrasings have a fixed answer. Everything here is offline.
"""
from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core import timegrammar as tg
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

REG = get_registry()
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)  # a Friday


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(tg, "_now", lambda: NOW)


def d(y, m, day, h=0, mi=0):
    return datetime(y, m, day, h, mi, tzinfo=timezone.utc)


def span(query):
    p = parse(query, REG)
    assert p.ok, p.errors
    return p.start, p.end


# -- weekdays ------------------------------------------------------------


def test_a_bare_weekday_is_the_most_recent_one():
    assert span("xrays wednesday") == (d(2026, 8, 12), d(2026, 8, 13))


def test_a_weekday_that_is_today_means_today():
    assert span("xrays friday") == (d(2026, 8, 14), d(2026, 8, 15))


def test_last_weekday_skips_today():
    assert span("goes xrs last friday") == (d(2026, 8, 7), d(2026, 8, 8))


def test_next_weekday_looks_forward():
    assert span("goes xrs next monday") == (d(2026, 8, 17), d(2026, 8, 18))


def test_abbreviated_weekdays_work():
    assert span("xrs weds") == (d(2026, 8, 12), d(2026, 8, 13))


def test_sun_and_sat_are_not_weekdays():
    """"sun" is the star and "sat" is a satellite far more often than a day."""
    assert tg._weekday_span("picture of the sun") is None
    assert tg._weekday_span("goes sat 15") is None


def test_a_weekday_can_carry_a_time_of_day():
    assert span("aia 304 wednesday morning") == (d(2026, 8, 12, 6), d(2026, 8, 12, 12))


# -- relative words ------------------------------------------------------


def test_spelled_out_counts():
    assert span("goes sxr a couple of days ago") == (d(2026, 8, 12), d(2026, 8, 13))
    assert span("goes sxr three days ago") == (d(2026, 8, 11), d(2026, 8, 12))
    assert span("goes xrs last two weeks")[0] == NOW - timedelta(weeks=2)


def test_fortnight():
    assert span("lyra last fortnight")[0] == NOW - timedelta(weeks=2)


def test_last_night_spans_the_evening_and_the_dawn():
    assert span("flares last night") == (d(2026, 8, 13, 18), d(2026, 8, 14, 6))


def test_parts_of_the_day():
    assert span("goes xrs yesterday afternoon") == (d(2026, 8, 13, 12), d(2026, 8, 13, 18))
    assert span("goes xrs yesterday morning") == (d(2026, 8, 13, 6), d(2026, 8, 13, 12))
    # with no date at all, a part of the day is today's
    assert span("goes xrs this morning") == (d(2026, 8, 14, 6), d(2026, 8, 14, 12))


def test_weekend():
    assert span("xrs this weekend") == (d(2026, 8, 8), d(2026, 8, 10))


def test_the_tenth_is_the_most_recent_tenth():
    assert span("goes xrs the 10th") == (d(2026, 8, 10), d(2026, 8, 11))


def test_a_month_and_day_with_no_year_looks_backwards():
    """On 14 August, "5 December" is last December — not four months from now."""
    assert span("goes xrs 5 december") == (d(2025, 12, 5), d(2025, 12, 6))
    assert span("goes xrs 5 july") == (d(2026, 7, 5), d(2026, 7, 6))


# -- written dates and ranges --------------------------------------------


def test_slash_and_dot_dates_are_day_first():
    assert span("aia 193 10/07/2026") == (d(2026, 7, 10), d(2026, 7, 11))
    assert span("goes xrs 10.07.26") == (d(2026, 7, 10), d(2026, 7, 11))


def test_a_hyphenated_date_is_a_date_not_a_range():
    """Reported from the field: "wind 8-may-2024" asked for one day and got
    eight months, because the hyphen was read as a range separator and the
    query became "May" → "2024". Wind/WAVES caught it on its 2-day limit;
    "flares may-2024" would have returned eight months of catalogue in
    silence."""
    assert span("wind 8-may-2024") == (d(2024, 5, 8), d(2024, 5, 9))
    assert span("lasco 28-oct-2003") == (d(2003, 10, 28), d(2003, 10, 29))
    assert span("flares may-2024") == (d(2024, 5, 1), d(2024, 6, 1))
    assert span("goes xrs 8-5-2024") == (d(2024, 5, 8), d(2024, 5, 9))


def test_hyphenated_dates_still_range_when_a_range_is_written():
    assert span("euvs 8-may-2024 to 10-may-2024") == (d(2024, 5, 8), d(2024, 5, 11))
    # a *spaced* hyphen is a separator; an unspaced one is part of a date
    assert span("flares 1-march-2024 - 5-march-2024") == (d(2024, 3, 1), d(2024, 3, 6))


def test_natural_language_ranges():
    assert span("goes xrs from 10 july to 12 july 2026") == (d(2026, 7, 10), d(2026, 7, 13))
    assert span("goes xrs 10 to 12 july 2026") == (d(2026, 7, 10), d(2026, 7, 13))
    assert span("flares july to august 2014") == (d(2014, 7, 1), d(2014, 9, 1))
    assert span("flares between 2014 and 2016") == (d(2014, 1, 1), d(2017, 1, 1))


def test_a_range_written_with_and_is_not_two_instruments():
    p = parse("stix between 10 july and 12 july 2026", REG)
    assert p.ok, p.errors
    assert [r.source for r in p.requests] == ["solo_stix"]


def test_am_pm_is_shared_across_a_range():
    assert span("goes xrs from 3 to 4pm yesterday") == (d(2026, 8, 13, 15), d(2026, 8, 13, 16))
    # but only when it keeps the range in order
    assert span("goes xrs from 10 to 2pm yesterday") == (d(2026, 8, 13, 10), d(2026, 8, 13, 14))


def test_day_of_year():
    assert span("stix doy 150 2024") == (d(2024, 5, 29), d(2024, 5, 30))
    assert span("goes xrs day of year 1 2020") == (d(2020, 1, 1), d(2020, 1, 2))


def test_iso_forms_still_win():
    assert span("goes xrs 2026-07-10") == (d(2026, 7, 10), d(2026, 7, 11))
    assert span("goes xrs from 2026-07-10 to 2026-07-12") == (d(2026, 7, 10), d(2026, 7, 13))
    assert span("goes xrs 2026-07-10 14:00 to 15:30") == (d(2026, 7, 10, 14), d(2026, 7, 10, 15, 30))


def test_since_accepts_a_weekday():
    start, end = span("flares since wednesday")
    assert start == d(2026, 8, 12) and end == NOW


def test_a_bare_number_is_still_not_a_date():
    """The guard that stops "3" becoming a whole year must survive all this."""
    assert tg.span_by_precision("3") is None
    assert tg.extract("goes xrs 3", []) is None
