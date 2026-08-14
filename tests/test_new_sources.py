"""Offline routing/extras tests for the v0.3+ source plugins."""
from datetime import datetime, timezone

import pytest

from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

REG = get_registry()


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


@pytest.mark.parametrize("query,source,extras", [
    ("lasco c3 yesterday", "soho_images", {"channel": "lasco_c3"}),
    ("lasco c2 2026-08-01", "soho_images", {"channel": "lasco_c2"}),
    ("soho coronagraph yesterday", "soho_images", {"channel": "lasco_c2"}),
    ("soho eit 304 2003-10-28", "soho_images", {"channel": "eit_304"}),
    ("soho yesterday", "soho_images", {"channel": "eit_195"}),
    ("swap yesterday", "proba2_swap", {}),
    ("proba2 swap 2026-08-01", "proba2_swap", {}),
    ("solar orbiter eui 304 yesterday", "solo_eui", {"channel": "fsi_304"}),
    ("eui hri 2026-03-10", "solo_eui", {"channel": "hri_174"}),
    ("eui hri lyman alpha 2026-03-10", "solo_eui", {"channel": "hri_1216"}),
    ("solar orbiter stix yesterday", "solo_stix", {}),
    ("stix 2026-08-01", "solo_stix", {}),
    ("rhessi 2003-10-28", "rhessi", {}),
    ("lyra yesterday", "proba2_lyra", {}),
    ("fermi gbm 2017-09-06", "fermi_gbm", {}),
    ("gbm n3 for 2022-04-20", "fermi_gbm", {"detector": "n3"}),
    ("sdo aia 304 yesterday", "sdo_images", {"channel": "aia_304"}),
    ("goes sxr yesterday", "goes_xrs", {}),
    ("wind waves yesterday", "wind_waves", {}),
    ("radio spectrum 2026-07-11", "wind_waves", {}),
    ("euvs yesterday", "goes_euvs", {}),
    ("goes euvs 304 2026-07-01", "goes_euvs", {"channel": "irr_304"}),
    ("goes-16 euvs lyman alpha 2023-01-01", "goes_euvs",
     {"satellite": 16, "channel": "irr_1216"}),
    ("euvs mg ii yesterday", "goes_euvs", {"channel": "MgII_standard"}),
    ("euvs 2014-06-10", "goes_euvs", {}),
    ("goes-14 euvs 2015-06-01", "goes_euvs", {"satellite": 14}),
    ("goes xrs high resolution 2014-06-10", "goes_xrs", {"resolution": "flx1s"}),
    ("goes xrs hi-res 2003-10-28", "goes_xrs", {"resolution": "flx1s"}),
    ("goes-13 xrs 2014-03-01", "goes_xrs", {"satellite": 13}),
    ("goes-12 xrs high cadence 2003-10-28", "goes_xrs",
     {"satellite": 12, "resolution": "flx1s"}),
    ("eve he ii 304 yesterday", "sdo_eve", {"line_ion": "He II", "line_nm": 30.4}),
    ("eve 30.4 nm yesterday", "sdo_eve", {"line_nm": 30.4}),
    ("sdo eve spectrum 2026-08-05", "sdo_eve", {}),
    ("eve fe xv 2013-05-15", "sdo_eve", {"line_ion": "Fe XV"}),
    ("hinode sot 2013-11-15", "hinode_sot", {}),
])
def test_routing(query, source, extras):
    p = parse(query, REG)
    assert p.ok, p.errors
    assert p.source == source
    assert p.extras == extras


def test_gbm_detector_not_a_date():
    p = parse("gbm n3 for 2022-04-20", REG)
    assert p.start == d(2022, 4, 20)


def test_mission_span_guard():
    src = REG.get("rhessi")
    with pytest.raises(RuntimeError, match="stopped observing"):
        src.fetch(d(2026, 1, 1), d(2026, 1, 2))
    with pytest.raises(RuntimeError, match="starts"):
        src.fetch(d(1999, 1, 1), d(1999, 1, 2))


def test_stix_max_days():
    src = REG.get("solo_stix")
    with pytest.raises(RuntimeError, match="limited"):
        src.fetch(d(2026, 1, 1), d(2026, 3, 1))


def test_eve_wavelength_never_eats_dates():
    # "304" is a wavelength, "10"/"2026" are date parts; ISO dates untouched
    p = parse("eve 304 on 10 July 2026", REG)
    assert p.extras == {"line_nm": 30.4}
    assert p.start == d(2026, 7, 10)
    p2 = parse("eve 2026-07-10", REG)
    assert p2.extras == {}
    assert p2.start == d(2026, 7, 10)


def test_euvs_primary_satellite():
    from solarapp.sources.goes_euvs import primary_satellite
    assert primary_satellite(d(2016, 1, 1)) is None
    assert primary_satellite(d(2020, 6, 1)) == 16
    assert primary_satellite(d(2026, 8, 1)) == 19


# -- legacy GOES 13-15 archives ------------------------------------------


def test_euvs_legacy_satellite():
    from solarapp.sources.goes_euvs import legacy_satellite
    assert legacy_satellite(d(2009, 1, 1)) is None      # before any L2 EUVS
    assert legacy_satellite(d(2009, 10, 1)) == 14       # GOES-14 campaigns only
    assert legacy_satellite(d(2014, 6, 10)) == 15       # GOES-15 once it is up


def test_euvs_before_legacy_coverage_is_refused():
    with pytest.raises(RuntimeError, match="starts 2009-09-01"):
        REG.get("goes_euvs").fetch(d(2005, 1, 1), d(2005, 1, 2))


def test_euvs_legacy_has_no_304():
    """The pre-2017 L2 archive populates Lyman-alpha only; say so, don't fetch."""
    with pytest.raises(RuntimeError, match="Lyman"):
        REG.get("goes_euvs").fetch(d(2014, 6, 10), d(2014, 6, 11), channel="irr_304")


def test_euvs_named_legacy_satellite_beats_the_date():
    """GOES-15 EUVS ran to 2020, three years into the GOES-R era."""
    with pytest.raises(RuntimeError, match="stopped observing"):
        REG.get("goes_euvs").fetch(d(2021, 6, 1), d(2021, 6, 2), satellite=15)


def test_goes_satellite_number_is_not_eaten_from_a_date():
    """"goes 10 july 2014" is a day of the month, not GOES-10."""
    p = parse("goes xrs 10 july 2014", REG)
    assert p.ok and p.extras == {}
    assert p.start == d(2014, 7, 10)
    p = parse("euvs goes 16 july 2018", REG)
    assert p.ok and p.extras == {}
    assert p.start == d(2018, 7, 16)


def test_hires_words_survive_the_spell_corrector():
    for q in ["goes xrs high resolution 2014-06-10", "xrs full cadence 2014-06-10"]:
        p = parse(q, REG)
        assert p.ok and p.corrections == [], (q, p.corrections)
        assert p.extras == {"resolution": "flx1s"}
        assert p.start == d(2014, 6, 10)


def test_waves_max_days():
    with pytest.raises(RuntimeError, match="limited"):
        REG.get("wind_waves").fetch(d(2026, 7, 1), d(2026, 7, 10))


def test_sot_mission_span():
    with pytest.raises(RuntimeError, match="stopped observing"):
        REG.get("hinode_sot").fetch(d(2020, 1, 1), d(2020, 1, 2))
