"""Broadened query understanding: spelling variants, plain-language terms,
explicit ambiguity, and times of day.

These are the shapes real users typed that the strict keyword matcher rejected
("xrays yesterday", "radio today", "wind today") or answered at the wrong
precision ("aia 193 on 2026-08-01 between 10:00 and 12:00" returning a whole
day). Where a broadened term genuinely names several instruments the parser
must ask rather than pick — see test_ambiguous_*.
"""
from datetime import datetime, timedelta, timezone

from solarapp.core.parser import parse
from solarapp.core.registry import get_registry, keyword_regex

REG = get_registry()


def d(y, m, dd, h=0, mi=0):
    return datetime(y, m, dd, h, mi, tzinfo=timezone.utc)


def sources(p):
    return [r.source for r in p.requests]


def yesterday():
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=1)


# -- keyword spelling variants -------------------------------------------


def test_keyword_regex_tolerates_separators_and_plural():
    rx = keyword_regex("x-ray")
    for text in ["x-ray", "x ray", "xray", "x-rays", "xrays", "X Rays"]:
        assert rx.search(text), text
    assert not rx.search("xrayed something")  # still word-boundary matched


def test_keyword_regex_does_not_match_across_words():
    assert not keyword_regex("wind waves").search("wind speed and waves")


def test_xray_variants_route_to_goes():
    for q in ["xrays yesterday", "x-rays yesterday", "xray yesterday",
              "x ray yesterday", "soft xray yesterday"]:
        p = parse(q, REG)
        assert p.ok and sources(p) == ["goes_xrs"], q


def test_hard_xray_still_outscores_bare_xray():
    p = parse("hard xrays 2003-10-28", REG)
    assert p.ok and sources(p) == ["rhessi"]


def test_proba2_spelling_variants():
    for q in ["proba2 today", "proba-2 today", "proba 2 today"]:
        p = parse(q, REG)
        assert p.ok and sources(p) == ["proba2_swap"], q


# -- radio / Wind ---------------------------------------------------------


def test_bare_radio_is_wind_waves():
    p = parse("radio today", REG)
    assert p.ok and sources(p) == ["wind_waves"]


def test_bare_wind_is_the_spacecraft():
    for q in ["wind today", "wind yesterday", "wind/waves yesterday"]:
        p = parse(q, REG)
        assert p.ok and sources(p) == ["wind_waves"], q


def test_solar_wind_is_still_refused():
    """The plasma stream is not served here; it must not silently become radio."""
    for q in ["solar wind speed today", "solar wind density yesterday"]:
        p = parse(q, REG)
        assert not p.ok, q


def test_solar_wind_wording_does_not_block_a_real_radio_request():
    p = parse("solar wind radio burst yesterday", REG)
    assert p.ok and sources(p) == ["wind_waves"]


# -- ambiguity is reported, never guessed --------------------------------


def test_ambiguous_euv_lists_the_candidates():
    p = parse("euv today", REG)
    assert not p.ok
    msg = p.errors[0]
    for expected in ["SDO images", "SOHO images", "Solar Orbiter EUI",
                     "PROBA2 SWAP", "SDO EVE", "GOES EUVS"]:
        assert expected in msg, msg
    assert "euv today" in msg


def test_ambiguity_resolves_once_an_instrument_is_named():
    for q, want in [("euv images today", "sdo_images"),
                    ("sdo euv today", "sdo_images"),
                    ("euvs today", "goes_euvs"),
                    ("eui euv today", "solo_eui")]:
        p = parse(q, REG)
        assert p.ok and sources(p) == [want], q


def test_where_is_beats_the_instrument_name_it_contains():
    """Previously a 13-13 tie decided by plugin load order."""
    p = parse("where is solar orbiter today", REG)
    assert p.ok and sources(p) == ["positions"]


# -- time of day ----------------------------------------------------------


def test_time_range_with_between_and():
    p = parse("aia 193 on 2026-08-01 between 10:00 and 12:00", REG)
    assert p.ok
    assert (p.start, p.end) == (d(2026, 8, 1, 10), d(2026, 8, 1, 12))


def test_time_range_on_a_spelled_date():
    p = parse("aia 171 on 10 July 2026 between 10:00 and 12:00", REG)
    assert p.ok
    assert (p.start, p.end) == (d(2026, 7, 10, 10), d(2026, 7, 10, 12))


def test_am_pm_range():
    p = parse("aia 304 on 2026-08-01 10am-2pm", REG)
    assert p.ok
    assert (p.start, p.end) == (d(2026, 8, 1, 10), d(2026, 8, 1, 14))


def test_bare_time_after_a_relative_day():
    p = parse("aia 171 yesterday 14:00", REG)
    assert p.ok
    assert p.start == yesterday().replace(hour=14)
    assert p.end - p.start == timedelta(hours=1)


def test_around_is_a_point_in_time():
    p = parse("aia 171 on 2026-08-01 around 9:30", REG)
    assert p.ok
    assert (p.start, p.end) == (d(2026, 8, 1, 9, 30), d(2026, 8, 1, 10, 30))


def test_at_accepts_a_bare_hour():
    p = parse("aia 171 on 2026-08-01 at 14 UT", REG)
    assert p.ok
    assert (p.start, p.end) == (d(2026, 8, 1, 14), d(2026, 8, 1, 15))


def test_range_crossing_midnight():
    for q in ["goes 2003-10-28 22:00 to 02:00", "goes on 28 October 2003 22:00 to 02:00"]:
        p = parse(q, REG)
        assert p.ok, q
        assert (p.start, p.end) == (d(2003, 10, 28, 22), d(2003, 10, 29, 2)), q


def test_bare_numbers_are_not_read_as_clock_times():
    """"from 3 to 4" is a count far more often than a clock, so it is ignored."""
    p = parse("from 3 to 4 goes 2026-07-10", REG)
    assert p.ok
    assert (p.start, p.end) == (d(2026, 7, 10), d(2026, 7, 11))


def test_wavelengths_are_not_read_as_clock_times():
    for q, want in [("aia 1600 on 2026-08-01", "aia_1600"),
                    ("aia 94 on 2026-08-01", "aia_94")]:
        p = parse(q, REG)
        assert p.ok and p.extras["channel"] == want, q
        assert p.end - p.start == timedelta(days=1), q


# -- relative dates -------------------------------------------------------


def test_now_is_the_last_hour():
    p = parse("magnetogram now", REG)
    assert p.ok and p.end - p.start == timedelta(hours=1)
    assert p.extras["channel"] == "hmi_magnetogram"


def test_latest_behaves_like_now():
    p = parse("latest lasco", REG)
    assert p.ok and p.end - p.start == timedelta(hours=1)


def test_n_days_ago_is_that_day_not_a_window():
    p = parse("goes 3 days ago", REG)
    assert p.ok and p.end - p.start == timedelta(days=1)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    assert p.start == today - timedelta(days=3)


def test_this_month_runs_from_the_first():
    p = parse("flares this month", REG)
    assert p.ok and p.start.day == 1 and p.start.hour == 0


def test_this_week_starts_on_monday():
    p = parse("goes this week", REG)
    assert p.ok and p.start.weekday() == 0


def test_current_year_is_a_year():
    """A bare current year used to be rejected as dateless."""
    year = datetime.now(timezone.utc).year
    p = parse(f"goes {year}", REG)
    assert p.ok
    assert (p.start, p.end) == (d(year, 1, 1), d(year + 1, 1, 1))


# -- HMI default ----------------------------------------------------------


def test_hmi_alone_is_a_magnetogram_not_aia():
    """HMI has no EUV channel, so the AIA default answered a different question."""
    p = parse("hmi yesterday", REG)
    assert p.ok and p.extras["channel"] == "hmi_magnetogram"


def test_hmi_continuum_still_wins_over_the_magnetogram_default():
    p = parse("hmi continuum yesterday", REG)
    assert p.ok and p.extras["channel"] == "hmi_continuum"


def test_sdo_alone_is_still_aia():
    p = parse("sdo yesterday", REG)
    assert p.ok and p.extras["channel"] == "aia_171"
