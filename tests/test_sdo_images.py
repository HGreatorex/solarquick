from datetime import datetime, timedelta, timezone

from solarapp.core.helioviewer import MAX_FRAMES, sample_times
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry
from solarapp.sources.sdo_images import CHANNELS, SdoImages

REG = get_registry()


def d(y, m, dd):
    return datetime(y, m, dd, tzinfo=timezone.utc)


def test_discovered():
    assert REG.get("sdo_images") is not None


def test_wavelength_query():
    p = parse("sdo aia 304 on 10 July 2026", REG)
    assert p.ok
    assert p.source == "sdo_images"
    assert p.extras == {"channel": "aia_304"}
    assert p.start == d(2026, 7, 10)


def test_wavelength_not_mistaken_for_date():
    # "171" must be consumed as a wavelength, not fed to the date parser
    p = parse("aia 171 on 10 July 2026", REG)
    assert p.ok
    assert p.extras == {"channel": "aia_171"}
    assert p.start == d(2026, 7, 10) and p.end == d(2026, 7, 11)


def test_default_channel():
    p = parse("sdo yesterday", REG)
    assert p.ok and p.extras == {"channel": "aia_171"}


def test_hmi_magnetogram():
    p = parse("hmi magnetogram 2026-08-01", REG)
    assert p.ok
    assert p.extras == {"channel": "hmi_magnetogram"}
    assert p.start == d(2026, 8, 1)


def test_goes_unaffected():
    p = parse("goes sxr 2026-07-10", REG)
    assert p.ok and p.source == "goes_xrs" and p.extras == {}


def test_all_channels_have_layers():
    for key, ch in CHANNELS.items():
        assert ch["layer"].startswith("[SDO,")
        assert ch["source_id"] > 0
        assert ch["label"]


def test_sample_times_full_day():
    start, end = d(2026, 7, 10), d(2026, 7, 11)
    ts = sample_times(start, end)
    assert len(ts) == MAX_FRAMES
    assert ts[0] == start
    assert ts[-1] < end
    assert ts[1] - ts[0] == timedelta(hours=3)


def test_sample_times_short_interval():
    start = d(2026, 7, 10)
    ts = sample_times(start, start + timedelta(minutes=30))
    assert ts == [start]


def test_screenshot_url():
    src = SdoImages()
    url = src._screenshot_url(d(2026, 7, 10), CHANNELS["aia_171"])
    assert "date=2026-07-10T00:00:00Z" in url
    assert "[SDO,AIA,AIA,171,1,100]" in url
    assert "imageScale=2.4" in url
    assert "takeScreenshot" in url
