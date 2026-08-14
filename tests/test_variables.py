"""Declared source variables: the options inside each instrument.

These are the queries a user writes when they know the instrument and want one
thing out of it — a channel, a line, an energy band, a detector. Every one of
them used to fall back to the source default and silently answer a different
question.
"""
import pytest

from solarapp.core.parser import parse
from solarapp.core.registry import DataSource, Variable, get_registry, keyword_regex
from solarapp.core.tsutil import select_bands

REG = get_registry()


@pytest.mark.parametrize("query,source,extras", [
    # GOES XRS: one channel, and the cadence, are separate dimensions
    ("goes xrs long channel yesterday", "goes_xrs", {"channel": "xrsb"}),
    ("goes xrs short channel yesterday", "goes_xrs", {"channel": "xrsa"}),
    ("goes xrs short channel high resolution 2014-06-10", "goes_xrs",
     {"channel": "xrsa", "resolution": "flx1s"}),
    # GOES EUVS by ion name and by nm, not just by the bare number
    ("euvs he ii yesterday", "goes_euvs", {"channel": "irr_304"}),
    ("euvs fe xv yesterday", "goes_euvs", {"channel": "irr_284"}),
    ("euvs mg ii yesterday", "goes_euvs", {"channel": "MgII_standard"}),
    ("euvs 121.6 nm yesterday", "goes_euvs", {"channel": "irr_1216"}),
    # SDO images: ion names and nm spellings of the AIA channels
    ("aia fe xii yesterday", "sdo_images", {"channel": "aia_193"}),
    ("aia 30.4 nm yesterday", "sdo_images", {"channel": "aia_304"}),
    ("sdo continuum yesterday", "sdo_images", {"channel": "hmi_continuum"}),
    ("hmi yesterday", "sdo_images", {"channel": "hmi_magnetogram"}),
    # SOHO: a bare "lasco" still means C2, and C3 outscores it
    ("lasco yesterday", "soho_images", {"channel": "lasco_c2"}),
    ("lasco c3 yesterday", "soho_images", {"channel": "lasco_c3"}),
    ("soho eit 304 yesterday", "soho_images", {"channel": "eit_304"}),
    # instrument-specific options that had no query path at all before
    ("lyra zirconium yesterday", "proba2_lyra", {"channel": "CHANNEL4"}),
    ("lyra herzberg yesterday", "proba2_lyra", {"channel": "CHANNEL2"}),
    ("rhessi 25-50 kev 2003-10-28", "rhessi", {"band": "25-50"}),
    ("stix 15-25 kev yesterday", "solo_stix", {"band": "15-25"}),
    ("gbm n3 2017-09-06", "fermi_gbm", {"detector": "n3"}),
    ("wind waves rad1 2026-07-11", "wind_waves", {"receiver": "rad1"}),
    ("eui hri lyman alpha yesterday", "solo_eui", {"channel": "hri_1216"}),
    ("eui 304 yesterday", "solo_eui", {"channel": "fsi_304"}),
])
def test_variable_routing(query, source, extras):
    p = parse(query, REG)
    assert p.ok, p.errors
    assert p.source == source
    assert p.extras == extras


def test_groups_combine_but_alternatives_do_not():
    """Options in different groups stack; options in one group are exclusive."""
    xrs = REG.get("goes_xrs")
    extras, _ = xrs.parse_extras("goes xrs long channel high resolution")
    assert extras == {"channel": "xrsb", "resolution": "flx1s"}
    # two channels named: the better-scoring one wins, never both
    extras, _ = xrs.parse_extras("goes xrs long channel short channel")
    assert list(extras) == ["channel"]


def test_variable_tokens_are_consumed_before_date_parsing():
    """A wavelength must not survive into the date parser as a day number."""
    p = parse("aia 193 on 10 July 2026", REG)
    assert p.ok, p.errors
    assert (p.start.year, p.start.month, p.start.day) == (2026, 7, 10)
    assert p.extras == {"channel": "aia_193"}


def test_ion_without_a_wavelength_resolves_by_name():
    """"eve fe xv" must not be pinned to whatever nm this table records."""
    extras, _ = REG.get("sdo_eve").parse_extras("eve fe xv 2013-05-15")
    assert extras == {"line_ion": "Fe XV"}
    extras, _ = REG.get("sdo_eve").parse_extras("eve he ii 304 yesterday")
    assert extras == {"line_nm": 30.4, "line_ion": "He II"}


def test_energy_bands_match_on_numbers_not_spacing():
    names = ["3 - 6 keV", "6 - 12 keV", "25 - 50 keV", "50 - 100 keV"]
    assert select_bands(names, "25-50") == ["25 - 50 keV"]
    assert select_bands(names, None) == names
    assert select_bands(names, "hard") == ["25 - 50 keV", "50 - 100 keV"]
    assert select_bands(names, "thermal") == ["3 - 6 keV", "6 - 12 keV"]
    # an unknown band shows everything rather than an empty plot
    assert select_bands(names, "999-1000") == names


def test_a_trailing_plural_is_not_allowed_on_a_number():
    """"the 1600s" is a decade, not an AIA channel."""
    assert keyword_regex("1600").search("aia 1600")
    assert not keyword_regex("1600").search("the 1600s")
    assert keyword_regex("x-ray").search("xrays yesterday")


def test_an_over_long_request_says_what_window_it_understood():
    """A too-long request is often a misread date, not a greedy user — so the
    error quotes the span it read, which is what makes the misreading visible."""
    from datetime import datetime, timezone

    from solarapp.core.tsutil import check_max_days

    start = datetime(2024, 5, 1, tzinfo=timezone.utc)
    check_max_days(start, start, 2, "Wind/WAVES spectrogram")  # in range: silent
    with pytest.raises(RuntimeError) as e:
        check_max_days(start, datetime(2025, 1, 1, tzinfo=timezone.utc), 2,
                       "Wind/WAVES spectrogram")
    assert "limited to 2 days" in str(e.value)
    assert "2024-05-01 00:00 – 2025-01-01 00:00 UT" in str(e.value)
    assert "245.0 days" in str(e.value)


def test_every_source_declares_a_coverage_that_parses():
    from datetime import datetime, timezone

    for src in REG.sources.values():
        for date in src.coverage:
            if date is not None:
                datetime.fromisoformat(date).replace(tzinfo=timezone.utc)


def test_coverage_refuses_out_of_span_requests_before_fetching():
    from datetime import datetime, timezone

    rhessi = REG.get("rhessi")
    after = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert not rhessi.covers(after, after)
    with pytest.raises(RuntimeError, match="stopped observing"):
        rhessi.check_coverage(after, after)
    before = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="data starts"):
        rhessi.check_coverage(before, before)


def test_a_new_plugin_teaches_the_registry_its_words():
    """The whole point: one file, and the vocabulary grows."""

    class Fake(DataSource):
        name = "fake"
        display_name = "Fake"
        keywords = ["fakeinstrument"]
        variables = [Variable("k", "Kryptonite channel", ["kryptonite"])]

        def fetch(self, start, end, **extras):
            return {}

    reg = type(REG)()
    reg._sources["fake"] = Fake()
    assert "kryptonite" in reg.vocabulary()
    assert "fakeinstrument" in reg.vocabulary()
    extras, consumed = Fake().parse_extras("fakeinstrument kryptonite today")
    assert extras == {"channel": "k"} and consumed == ["kryptonite"]
