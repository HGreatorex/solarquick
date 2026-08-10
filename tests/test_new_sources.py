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
