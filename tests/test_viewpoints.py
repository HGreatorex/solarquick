"""Multi-viewpoint geometry, STIX flare-list shifting and the VIEWPOINTS block — offline."""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core import httpclient, viewpoints as vp
from solarapp.core.registry import get_registry
from solarapp.sources import solar_regions as sr

UTC = timezone.utc
T = datetime(2024, 5, 14, 16, 51, tzinfo=UTC)


def body(positions):
    return lambda name, slot: positions[name]


POS = {"Earth": (0.0, -3.0, 1.01), "Solar Orbiter": (-80.0, 2.0, 0.74), "STEREO-A": (-12.0, 4.0, 0.96)}


@pytest.mark.parametrize("lat,lon,b0", [(-9, 30, 7.0), (20, -60, -5.0), (0, 85, 1.0)])
def test_hpc_to_stonyhurst_inverts_the_disk_projection(lat, lon, b0):
    x, y = sr.project(lat, lon, b0)
    la, lo, beyond = vp.hgs_from_hpc(x * vp.RSUN_ARCSEC_1AU, y * vp.RSUN_ARCSEC_1AU, b0)
    # project() rounds to 1e-4 R: ~0.01°
    assert la == pytest.approx(lat, abs=0.02) and lo == pytest.approx(lon, abs=0.02) and not beyond


def test_a_site_off_the_disk_is_put_on_the_limb():
    la, lo, beyond = vp.hgs_from_hpc(1000.0, 0.0, 0.0)
    assert beyond and lo == pytest.approx(90) and la == pytest.approx(0)


def test_classify_on_disk_limb_behind_and_far_side():
    earth = {"lat": 0.0, "lon": 0.0, "r_au": 1.0}
    assert vp.classify(0, 30, earth)["status"] == "on disk"
    assert vp.classify(0, 80, earth)["status"] == "near limb"
    behind = vp.classify(0, 95, earth)
    limb = math.degrees(math.acos(vp.RSUN_AU))
    want = vp.RSUN_KM / 1000 * (1 / math.cos(math.radians(95 - limb)) - 1)
    assert behind["status"] == "behind limb" and behind["visible_above_mm"] == pytest.approx(want, abs=0.1)
    assert vp.classify(0, 150, earth)["status"] == "far side"
    # a spacecraft 90° ahead sees the same site face on
    assert vp.classify(0, 95, {"lat": 0.0, "lon": 90.0, "r_au": 0.9})["theta"] == pytest.approx(5, abs=0.01)
    # closer observers see less than a hemisphere
    assert vp.classify(0, 0, {"lat": 0.0, "lon": 0.0, "r_au": 0.3})["limb"] < 89.2


def test_light_lead():
    assert vp.light_lead_s({"r_au": 0.3}, 1.0) == pytest.approx(0.7 * vp.AU_KM / vp.C_KM_S, abs=0.1)


def test_stix_flare_list_is_queried_and_shown_in_earth_time(monkeypatch):
    sent = {}

    class S:
        def post(self, url, data, timeout):
            sent.update(data)
            class R:
                def raise_for_status(self): pass
                def json(self): return [{"flare_id": "2405141647", "peak_UTC": "2024-05-14T16:47:32.086",
                                        "start_UTC": "2024-05-14T16:43:32.086", "end_UTC": "2024-05-14T17:02:16.088",
                                        "GOES_class": "X8.8", "goes_estimated_mean_class": "X8",
                                        "goes_estimated_min_class": "X4", "goes_estimated_max_class": "X18",
                                        "att_in": True, "LC0_PEAK_COUNTS_4S": 13148990}]
            return R()
    monkeypatch.setattr(httpclient, "session", lambda: S())
    got = vp.stix_flares(T - timedelta(hours=1), T + timedelta(hours=1), 137.0)
    assert sent["start_utc"] == "2024-05-14T15:48:43"           # Earth window moved to SolO time
    assert got[0]["peak"].startswith("2024-05-14T16:49:49") and got[0]["peak_solo"].startswith("2024-05-14T16:47:32")
    assert got[0]["est"] == "X8" and got[0]["est_min"] == "X4" and got[0]["att_in"]


def test_companions_skip_the_far_side_and_missing_images():
    x, y = 500.0, -200.0                                         # western hemisphere from Earth
    seen = vp.companions(T, hpc=(x, y), body=body(POS), image_check=lambda sid, t: True)
    assert [s for s, _ in seen] == ["stereo_euvi"]               # SolO at E80 has it on its far side
    assert vp.companions(T, hpc=(x, y), body=body(POS), image_check=lambda sid, t: False) == []


def test_build_has_every_observer_and_the_stix_block():
    spec = vp.build(T, "X8.7 2024-05-14 16:51 UT", hpc=(500.0, -200.0), body=body(POS),
                    stix=lambda a, b, lead: [{"peak": "x", "lead": lead}])
    names = [o["name"] for o in spec["observers"]]
    assert names == ["Earth", "Solar Orbiter", "STEREO-A"]
    so = spec["observers"][1]
    assert so["status"] == "far side" and so["light_lead_s"] > 0
    assert spec["stix"]["flares"][0]["lead"] == so["light_lead_s"] and "+" in spec["stix"]["note"]
    assert spec["flare"]["lon"] > 0 and spec["kind"] == "viewpoints"
    json.dumps(spec, allow_nan=False)


def test_build_survives_a_missing_spacecraft_and_stix_failure():
    def b(name, slot):
        if name == "STEREO-A":
            raise OSError("Horizons down")
        return POS[name]
    def boom(*a):
        raise RuntimeError("STIX down")
    spec = vp.build(T, "flare", hpc=(500.0, -200.0), body=b, stix=boom)
    assert [o["name"] for o in spec["observers"]] == ["Earth", "Solar Orbiter"]
    assert "STEREO-A: no ephemeris" in spec["note"] and spec["stix"] == {"error": "STIX down"}
    pre_stix = vp.build(datetime(2012, 3, 7, tzinfo=UTC), "flare", hpc=(0.0, 0.0), body=body(POS))
    assert "stix" not in pre_stix and [o["name"] for o in pre_stix["observers"]] == ["Earth", "STEREO-A"]


def test_sources_are_registered():
    reg = get_registry()
    assert reg.get("stereo_euvi").channels["euvi_195"]["source_id"] == 21
    assert reg.get("viewpoints").coverage[0] == "2006-10-27"


def test_a_site_given_in_stonyhurst_is_used_as_is():
    spec = vp.build(T, "flare", hgs=(-9.0, 96.0), estimated="AR 3664 rotated", body=body(POS),
                    stix=lambda a, b, lead: [])
    earth = spec["observers"][0]
    assert spec["flare"]["lon"] == 96.0 and earth["status"] == "behind limb" and earth["visible_above_mm"] > 0
    assert "Site estimated: AR 3664 rotated." in spec["note"]


def test_an_unplaced_flare_still_gets_positions_and_stix():
    spec = vp.build(T, "X2.2", body=body(POS), stix=lambda a, b, lead: [{"est": "X2"}])
    assert spec["flare"]["lat"] is None and "site unknown" in spec["title"]
    assert all(o["status"] is None for o in spec["observers"]) and spec["stix"]["flares"]
    assert vp.companions(T, body=body(POS)) == []
