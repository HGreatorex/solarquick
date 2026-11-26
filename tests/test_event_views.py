"""Event-view composition (core/events.expand) with HEK stubbed out.

expand() is the piece that turns "biggest flare since 2014" into a composed
multi-source request, so these tests pin the composition rules (era gating,
crop propagation, window narrowing) without touching the network.
"""
from datetime import datetime, timedelta, timezone

import pytest

from solarapp.core import events, hek, viewpoints
from solarapp.sources import viewpoints as vp_source
from solarapp.core.parser import ParsedQuery, SourceRequest


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def make_flare(peak, cls="X1.0", x=515.0, y=-232.0, dur_min=20):
    return hek.Flare(
        goes_class=cls,
        start=peak - timedelta(minutes=dur_min // 2),
        peak=peak,
        end=peak + timedelta(minutes=dur_min // 2),
        hpc_x=x, hpc_y=y, ar=12673,
    )


def parsed_for(extras, start=utc(2014, 1, 1), end=utc(2026, 8, 12)):
    p = ParsedQuery()
    p.requests = [SourceRequest("flares", dict(extras))]
    p.source, p.extras = "flares", p.requests[0].extras
    p.start, p.end = start, end
    return p


@pytest.fixture
def stub_hek(monkeypatch):
    def install(flare):
        monkeypatch.setattr(hek, "biggest_flare", lambda s, e: flare)
        monkeypatch.setattr(hek, "flare_near", lambda t, **kw: flare)
        monkeypatch.setattr(events.hek, "biggest_flare", lambda s, e: flare)
        monkeypatch.setattr(events.hek, "flare_near", lambda t, **kw: flare)
    # VIEWPOINTS needs ephemerides and Helioviewer: offline here, no extra images
    monkeypatch.setattr(viewpoints, "companions", lambda t, **site: [])
    monkeypatch.setattr(vp_source, "region_site", lambda flare: None)
    return install


def sources_of(p):
    return [r.source for r in p.requests]


# -- composition ---------------------------------------------------------

def test_sdo_era_composes_xrs_aia_and_stix(stub_hek):
    stub_hek(make_flare(utc(2022, 4, 20, 3, 57)))
    p = parsed_for({"superlative": True})
    events.expand(p)
    assert sources_of(p) == ["flares", "goes_xrs", "sdo_images", "sdo_images", "solo_stix", "fism",
                             "viewpoints"]
    assert [r.extras["channel"] for r in p.requests if r.source == "sdo_images"] \
        == ["aia_131", "aia_304"]
    vp = p.requests[-1].extras
    assert vp["hpc"] == (515.0, -232.0) and vp["peak"] == utc(2022, 4, 20, 3, 57).isoformat() \
        and "estimated" not in vp


def test_other_vantage_points_join_the_image_grid(stub_hek, monkeypatch):
    stub_hek(make_flare(utc(2024, 5, 14, 16, 51)))
    monkeypatch.setattr(viewpoints, "companions", lambda t, **site: [
        ("solo_eui", {"channel": "fsi_174"}), ("stereo_euvi", {"channel": "euvi_195"})])
    p = parsed_for({"superlative": True})
    events.expand(p)
    assert sources_of(p) == ["flares", "goes_xrs", "sdo_images", "sdo_images", "solo_eui",
                             "stereo_euvi", "solo_stix", "fism", "viewpoints"]


def test_a_flare_that_cannot_be_placed_still_gets_viewpoints_without_a_site(stub_hek):
    stub_hek(make_flare(utc(2022, 4, 20, 3, 57), x=0.0, y=-104.0))
    p = parsed_for({"superlative": True})
    events.expand(p)
    vp = p.requests[-1]
    assert vp.source == "viewpoints" and "hpc" not in vp.extras and "hgs" not in vp.extras


def test_pre_sdo_falls_back_to_eit_without_stix(stub_hek):
    stub_hek(make_flare(utc(2003, 10, 28, 11, 10), cls="X17.2"))
    p = parsed_for({"superlative": True}, start=utc(2003, 1, 1), end=utc(2004, 1, 1))
    events.expand(p)
    assert sources_of(p) == ["flares", "goes_xrs", "soho_images", "fism"]
    assert p.requests[2].extras == {"channel": "eit_195"}


def test_pre_stix_sdo_era_has_no_stix(stub_hek):
    stub_hek(make_flare(utc(2014, 3, 29, 17, 48)))
    p = parsed_for({"superlative": True})
    events.expand(p)
    assert "solo_stix" not in sources_of(p)
    assert "sdo_images" in sources_of(p)


def test_crop_propagated_when_position_known(stub_hek):
    stub_hek(make_flare(utc(2022, 4, 20, 3, 57), x=515.0, y=-232.0))
    p = parsed_for({"superlative": True})
    events.expand(p)
    aia = [r for r in p.requests if r.source == "sdo_images"]
    for r in aia:
        assert r.extras["center_x"] == 515.0
        assert r.extras["center_y"] == -232.0
        assert r.extras["fov"] == events.CROP_FOV


def test_no_crop_when_position_missing(stub_hek):
    # SWPC reports hpc_x = 0 when the location is unknown
    stub_hek(make_flare(utc(2022, 4, 20, 3, 57), x=0.0, y=-104.0))
    p = parsed_for({"superlative": True})
    events.expand(p)
    for r in (r for r in p.requests if r.source == "sdo_images"):
        assert "fov" not in r.extras and "center_x" not in r.extras


def test_window_narrowed_to_event_and_pinned(stub_hek):
    peak = utc(2022, 4, 20, 3, 57)
    stub_hek(make_flare(peak, dur_min=20))
    p = parsed_for({"superlative": True})
    events.expand(p)
    assert p.start < peak < p.end
    assert (p.end - p.start) < timedelta(hours=2)  # zoomed in, not the decade
    assert p.requests[0].extras["pin_peak"] == peak.isoformat()
    assert "superlative" not in p.requests[0].extras


def test_class_filter_dropped_inside_event_window(stub_hek):
    stub_hek(make_flare(utc(2022, 4, 20, 3, 57)))
    p = parsed_for({"superlative": True, "min_class": "X"})
    events.expand(p)
    assert "min_class" not in p.requests[0].extras


def test_user_companions_respected(stub_hek):
    stub_hek(make_flare(utc(2022, 4, 20, 3, 57)))
    p = parsed_for({"superlative": True})
    p.requests.append(SourceRequest("goes_euvs", {}))
    events.expand(p)
    assert sources_of(p) == ["flares", "goes_euvs"]  # no auto-composition


def test_plain_list_untouched():
    p = parsed_for({}, start=utc(2026, 8, 11), end=utc(2026, 8, 12))
    events.expand(p)
    assert sources_of(p) == ["flares"]
    assert p.start == utc(2026, 8, 11) and p.end == utc(2026, 8, 12)


def test_unresolvable_superlative_raises(monkeypatch):
    monkeypatch.setattr(events.hek, "biggest_flare", lambda s, e: None)
    p = parsed_for({"superlative": True})
    with pytest.raises(RuntimeError, match="No flares found"):
        events.expand(p)


def test_non_flare_query_is_noop():
    p = ParsedQuery()
    p.requests = [SourceRequest("goes_xrs", {})]
    p.start, p.end = utc(2026, 7, 10), utc(2026, 7, 11)
    events.expand(p)
    assert sources_of(p) == ["goes_xrs"]


# -- Flare.has_position guard --------------------------------------------

@pytest.mark.parametrize("x,y,expected", [
    (515.0, -232.0, True),
    (0.0, -104.0, False),      # SWPC "unknown" sentinel
    (0.0, 0.0, False),
    (-702.7, 11.1, True),
    (5000.0, 0.0, False),      # nonsense, off-disk beyond any imager
])
def test_has_position(x, y, expected):
    assert make_flare(utc(2022, 1, 1), x=x, y=y).has_position is expected


def test_flare_at_a_minute_looks_at_that_minute(monkeypatch):
    asked = []
    monkeypatch.setattr(events.hek, "flare_near", lambda t, **kw: asked.append(t) or None)
    p = parsed_for({"at": True}, start=utc(2024, 5, 14, 16, 51), end=utc(2024, 5, 14, 17, 51))
    with pytest.raises(RuntimeError):
        events.expand(p)
    p = parsed_for({"at": True}, start=utc(2024, 5, 14), end=utc(2024, 5, 15))
    with pytest.raises(RuntimeError):
        events.expand(p)
    assert asked == [utc(2024, 5, 14, 16, 51), utc(2024, 5, 14, 12)]
