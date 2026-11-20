"""The Solar Region Summary source: reading the file, and shaping the block.

`fetch` is the only part that needs the network, and it is reached here with
`load_days` stubbed, so everything below runs offline: the two eras of the
file format, the day a summary is actually valid for, the disk projection,
and the three shapes a query can ask for.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from solarapp.core import export
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry
from solarapp.sources import solar_regions as sr

REG = get_registry()
SOURCE = REG.get("solar_regions")

# A modern file, complete: spots, a plage, a region due to return.
MODERN = """:Product: 0907SRS.txt
:Issued: 2017 Sep 07 0030 UTC
# Prepared jointly by the U.S. Dept. of Commerce, NOAA,
# Space Weather Prediction Center and the U.S. Air Force.
#
Joint USAF/NOAA Solar Region Summary
SRS Number 250 Issued at 0030Z on 07 Sep 2017
Report compiled from data received at SWO on 06 Sep
I.  Regions with Sunspots.  Locations Valid at 06/2400Z
Nmbr Location  Lo  Area  Z   LL   NN Mag Type
2673 S09W45   120  0960 Ekc  11   24 Beta-Gamma-Delta
2674 N14W28   104  0680 Fki  19   10 Beta
2677 N18E25   051  0010 Axx  01   01 Alpha
IA. H-alpha Plages without Spots.  Locations Valid at 06/2400Z Sep
Nmbr  Location  Lo
2676  S09W90   166
II. Regions Due to Return 07 Sep to 09 Sep
Nmbr Lat    Lo
2671 N14    320
"""

# 1996: upper case throughout, a COMMENT column, NONE for every section, and
# the NNN terminator.
ARCHAIC = """:Product: 19960115SRS.txt
:Issued: 1996 Jan 15 0030 UTC
#
JOINT USAF/NOAA SOLAR REGION SUMMARY
SRS NUMBER 015 ISSUED AT 0030Z ON 15 JANUARY 1996
I. REGIONS WITH SUNSPOTS. LOCATIONS VALID AT 14/2400Z JANUARY
NMBR LOCATION  LO  AREA  Z   LL   NN MAG TYPE
NONE
IA. H-ALPHA PLAGES WITHOUT SPOTS.  LOCATIONS VALID AT 14/2400Z
NMBR  LOCATION  LO            COMMENT
NONE
II. REGIONS DUE TO RETURN 15 JANUARY TO 17 JANUARY
NMBR LAT    LO
NONE


NNN
"""

UPPER = """:Product: 19980714SRS.txt
:Issued: 1998 Jul 14 0030 UTC
#
JOINT USAF/NOAA SOLAR REGION SUMMARY
I.  REGIONS WITH SUNSPOTS.  LOCATIONS VALID AT 13/2400Z
NMBR LOCATION  LO  AREA  Z   LL   NN MAG TYPE
8270 S21E26   216  0090 DAO  08   15 BETA-GAMMA
IA. H-ALPHA PLAGES WITHOUT SPOTS.  LOCATIONS VALID AT 13/2400Z JUL
NMBR  LOCATION  LO
8266  N18W31   273   SOME COMMENT
II. REGIONS DUE TO RETURN 14 JUL TO 16 JUL
NMBR LAT    LO
8257 N29    127
"""


# -- reading the file ----------------------------------------------------


def test_a_modern_file_yields_every_section():
    snap = sr.parse_srs(MODERN)
    assert snap.issued == date(2017, 9, 7)
    # issued on the 7th, describing the 6th at 2400 UT
    assert snap.valid == date(2017, 9, 6)
    assert [r.nmbr for r in snap.section(sr.SPOTS)] == ["2673", "2674", "2677"]
    assert [r.nmbr for r in snap.section(sr.PLAGE)] == ["2676"]
    assert [r.nmbr for r in snap.section(sr.RETURN)] == ["2671"]


def test_a_spot_row_carries_every_column():
    region = sr.parse_srs(MODERN).section(sr.SPOTS)[0]
    assert (region.lat, region.lon) == (-9.0, 45.0)  # S09W45, west positive
    assert (region.carrington, region.area) == (120, 960)
    assert (region.mcintosh, region.ll, region.spots) == ("Ekc", 11, 24)
    assert region.mag == "Beta-Gamma-Delta"
    assert region.delta and region.complexity == "delta"


def test_a_returning_region_has_a_latitude_but_no_disk_position():
    region = sr.parse_srs(MODERN).section(sr.RETURN)[0]
    assert region.lat == 14.0 and region.lon is None
    assert region.carrington == 320


@pytest.mark.parametrize("mag,complexity", [
    ("Alpha", "alpha"), ("Beta", "beta"), ("Beta-Gamma", "beta-gamma"),
    ("Beta-Delta", "delta"), ("Beta-Gamma-Delta", "delta"), (None, "alpha"),
])
def test_complexity_reads_the_hale_class(mag, complexity):
    region = sr.Region(nmbr="0001", section=sr.SPOTS, valid=date(2020, 1, 1), mag=mag)
    assert region.complexity == complexity
    assert region.delta is (complexity == "delta")


def test_an_empty_summary_is_empty_not_an_error():
    """A spotless Sun is data. NONE rows and the NNN terminator are not rows."""
    snap = sr.parse_srs(ARCHAIC)
    assert snap.valid == date(1996, 1, 14)
    assert snap.regions == []


def test_the_1990s_upper_case_format_reads_the_same():
    snap = sr.parse_srs(UPPER)
    region = snap.section(sr.SPOTS)[0]
    # classes are normalised, so one era's strings match the other's
    assert region.mcintosh == "Dao" and region.mag == "Beta-Gamma"
    assert (region.lat, region.lon) == (-21.0, -26.0)
    # the plage row's trailing COMMENT column is not mistaken for data
    plage = snap.section(sr.PLAGE)[0]
    assert plage.location == "N18W31" and plage.carrington == 273


def test_a_corrected_copy_is_still_a_summary():
    text = MODERN.replace("Joint USAF/NOAA Solar Region Summary",
                          "Joint USAF/NOAA Solar Region Summary\n"
                          "::::::::::Corrected Copy::::::::::")
    assert len(sr.parse_srs(text).section(sr.SPOTS)) == 3


def test_something_that_is_not_a_summary_is_refused():
    with pytest.raises(ValueError):
        sr.parse_srs("<html>NGDC is having a moment</html>")


def test_validity_comes_from_the_file_not_from_the_issue_date():
    """A file issued late still says which midnight it describes."""
    late = MODERN.replace(":Issued: 2017 Sep 07", ":Issued: 2017 Sep 08")
    late = late.replace("Issued at 0030Z on 07 Sep", "Issued at 0030Z on 08 Sep")
    assert sr.parse_srs(late).valid == date(2017, 9, 6)


# -- geometry ------------------------------------------------------------


def test_b0_matches_sunpy():
    """The tilt is computed here rather than imported; keep it honest."""
    sunpy_sun = pytest.importorskip("sunpy.coordinates.sun")
    Time = pytest.importorskip("astropy.time").Time
    for iso in ("1996-01-15", "2003-10-29", "2014-06-11", "2026-03-06", "2026-09-08"):
        mine = sr.b0_degrees(datetime.fromisoformat(iso))
        assert abs(mine - float(sunpy_sun.B0(Time(iso)).deg)) < 0.01


def test_the_projection_puts_west_right_and_north_up():
    # disk centre with no tilt
    assert sr.project(0, 0, 0) == (0.0, 0.0)
    x, _ = sr.project(0, 90, 0)
    assert x == pytest.approx(1.0)          # W90 is the west limb, on the right
    _, y = sr.project(90, 0, 0)
    assert y == pytest.approx(1.0)          # the north pole is up
    # a positive tilt shows more of the north, pushing disk centre south
    assert sr.project(0, 0, 7.25)[1] == pytest.approx(-0.1262, abs=1e-3)


def test_a_region_past_the_limb_is_pinned_to_the_edge():
    """W92 is on the far side; its projection would land back on the disk."""
    region = sr.Region(nmbr="3663", section=sr.SPOTS, valid=date(2024, 5, 10),
                       location="N27W92", lat=27.0, lon=92.0, area=300)
    row = sr._row(region, date(2024, 5, 10))
    assert row["behind"] is True
    assert (row["x"] ** 2 + row["y"] ** 2) == pytest.approx(1.0, abs=1e-3)


# -- routing -------------------------------------------------------------


@pytest.mark.parametrize("query,extras", [
    ("active regions today", {}),
    ("solar region summary yesterday", {}),
    ("srs 2017-09-06", {}),
    ("sunspot regions on 2014-06-10", {}),
    ("sunspot area june 2014", {}),
    ("hale class yesterday", {}),
    ("mcintosh classification 2003-10-28", {}),
    ("ar 4503 last week", {"ar": "4503"}),
    ("AR 14503 last week", {"ar": "4503"}),      # the same region, both spellings
    ("noaa 12673 2017-09-06", {"ar": "2673"}),
    ("region 2673 september 2017", {"ar": "2673"}),
    ("delta regions last week", {"only_delta": True}),
    ("plages yesterday", {"section": "plage"}),
    ("regions due to return today", {"section": "return"}),
])
def test_queries_that_reach_the_summary(query, extras):
    p = parse(query, REG)
    assert p.ok, p.errors
    assert [(r.source, r.extras) for r in p.requests] == [("solar_regions", extras)]


def test_a_region_number_is_not_eaten_by_the_date_parser():
    p = parse("ar 4503 on 2026-08-10", REG)
    assert p.ok and p.extras == {"ar": "4503"}
    assert (p.start.date(), p.end.date()) == (date(2026, 8, 10), date(2026, 8, 11))


def test_a_bare_number_after_region_is_read_as_a_year_not_a_region():
    """"region 2014" is a date far more often than it is AR 12014."""
    assert sr._region_number("regions 2014") is None
    assert sr._region_number("ar 2014")[0] == "2014"


def test_a_flare_question_is_not_answered_with_a_region_table():
    """The region is the qualifier in "flares from active region 4503"."""
    p = parse("flares from active region 4503 last week", REG)
    assert p.ok and [r.source for r in p.requests] == ["flares"]
    # ...but asking for both still gets both
    p = parse("active regions and flares yesterday", REG)
    assert [r.source for r in p.requests] == ["solar_regions", "flares"]


def test_bare_sunspots_still_belongs_to_the_white_light_imager():
    p = parse("sunspots yesterday", REG)
    assert [r.source for r in p.requests] == ["sdo_images"]


def test_the_overview_includes_the_regions_that_were_on_the_disk():
    p = parse("2017-09-06", REG)
    assert "solar_regions" in [r.source for r in p.requests]


def test_a_date_before_the_archive_is_refused_without_a_fetch():
    with pytest.raises(RuntimeError, match="starts 1996-01-01"):
        SOURCE.fetch(datetime(1985, 1, 1, tzinfo=timezone.utc),
                     datetime(1985, 1, 2, tzinfo=timezone.utc))


def test_a_year_long_request_is_refused_with_the_window_it_read():
    with pytest.raises(RuntimeError, match="limited to 62 days"):
        SOURCE.fetch(datetime(2014, 1, 1, tzinfo=timezone.utc),
                     datetime(2015, 1, 1, tzinfo=timezone.utc))


# -- the block ------------------------------------------------------------


def _days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]


def _snapshot(day: date, regions: list[tuple]) -> sr.Snapshot:
    """A snapshot of (nmbr, lat, lon, area, spots, mag) tuples."""
    return sr.Snapshot(valid=day, issued=day + timedelta(days=1), regions=[
        sr.Region(nmbr=n, section=sr.SPOTS, valid=day, location=f"N{lat:02d}W{lon:02d}",
                  lat=float(lat), lon=float(lon), carrington=100, area=area,
                  mcintosh="Dao", ll=5, spots=spots, mag=mag)
        for n, lat, lon, area, spots, mag in regions])


def _stub(monkeypatch, snapshots, live=None, unread=None):
    def fake(days, today):
        return ({s.valid: s for s in snapshots}, live, unread or {})
    monkeypatch.setattr(sr, "load_days", fake)


def _fetch(monkeypatch, start: date, end: date, snapshots, live=None,
           unread=None, **extras):
    _stub(monkeypatch, snapshots, live, unread)
    return SOURCE.fetch(datetime(*start.timetuple()[:3], tzinfo=timezone.utc),
                        datetime(*end.timetuple()[:3], tzinfo=timezone.utc), **extras)


def test_one_day_is_a_disk_and_a_table(monkeypatch):
    snap = sr.parse_srs(MODERN)
    spec = _fetch(monkeypatch, date(2017, 9, 6), date(2017, 9, 7), [snap])
    assert spec["kind"] == "regions"
    assert "valid 2017-09-06 2400 UT" in spec["title"]
    assert spec["ref_valid"] == "2017-09-06" and spec["table_section"] == sr.SPOTS
    assert "trend" not in spec and "track" not in spec
    # every section travels with the block; the frontend chooses what to table
    assert {r["section"] for r in spec["regions"]} == {sr.SPOTS, sr.PLAGE, sr.RETURN}


def test_a_range_adds_a_trend_and_keeps_every_days_rows(monkeypatch):
    days = _days(date(2024, 5, 8), 3)
    snapshots = [
        _snapshot(days[0], [("3664", 17, 10, 1000, 20, "Beta")]),
        _snapshot(days[1], [("3664", 17, 20, 2000, 40, "Beta-Gamma-Delta"),
                            ("3665", 10, 5, 100, 2, "Alpha")]),
        _snapshot(days[2], [("3664", 17, 30, 2400, 58, "Beta-Gamma-Delta")]),
    ]
    spec = _fetch(monkeypatch, days[0], days[-1] + timedelta(days=1), snapshots)
    assert spec["ref_valid"] == "2024-05-10"
    assert "2024-05-08 – 2024-05-10" in spec["title"]
    assert len(spec["regions"]) == 4  # not just the day the disk shows
    area, spots, regions = spec["trend"]
    assert area["series"][0]["values"] == [1000, 2100, 2400]
    assert spots["series"][0]["values"] == [20, 42, 58]
    assert [s["values"] for s in regions["series"]] == [[1, 2, 1], [0, 1, 1]]
    # each point sits at that day's 2400 UT, which is the next midnight
    assert area["series"][0]["times"][0].startswith("2024-05-09T00:00")


def test_a_named_region_is_tracked_across_the_days(monkeypatch):
    days = _days(date(2017, 9, 4), 3)
    snapshots = [
        _snapshot(days[0], [("2673", -8, 16, 680, 28, "Beta-Gamma-Delta"),
                            ("2674", 14, 5, 100, 3, "Beta")]),
        _snapshot(days[1], [("2673", -9, 30, 880, 33, "Beta-Gamma-Delta")]),
        _snapshot(days[2], [("2673", -9, 45, 960, 24, "Beta-Gamma-Delta")]),
    ]
    spec = _fetch(monkeypatch, days[0], days[-1] + timedelta(days=1), snapshots,
                  ar="2673")
    assert spec["title"].startswith("AR 2673")
    assert {r["nmbr"] for r in spec["regions"]} == {"2673"}
    assert len(spec["track"]["x"]) == 3
    assert spec["track"]["x"][0] < spec["track"]["x"][-1]  # rotating westward
    assert spec["trend"][0]["series"][0]["values"] == [680, 880, 960]


def test_a_region_that_never_appears_says_so(monkeypatch):
    snapshots = [_snapshot(date(2017, 9, 4), [("2673", -8, 16, 680, 28, "Beta")])]
    with pytest.raises(RuntimeError, match="AR 9999 does not appear"):
        _fetch(monkeypatch, date(2017, 9, 4), date(2017, 9, 5), snapshots, ar="9999")


def test_delta_only_keeps_the_regions_that_have_a_delta(monkeypatch):
    day = date(2003, 10, 28)
    snapshots = [_snapshot(day, [("0486", -17, -4, 2120, 55, "Beta-Gamma-Delta"),
                                 ("0487", 13, -32, 170, 10, "Beta")])]
    spec = _fetch(monkeypatch, day, day + timedelta(days=1), snapshots,
                  only_delta=True)
    assert [r["nmbr"] for r in spec["regions"]] == ["0486"]
    assert spec["title"].startswith("Delta regions")


def test_today_is_told_that_tonights_summary_does_not_exist_yet(monkeypatch):
    """The commonest query of all, and the one place a lie would be easy."""
    today = datetime.now(timezone.utc).date()
    live = sr.parse_srs(MODERN)
    live.valid = today - timedelta(days=1)
    for region in live.regions:
        region.valid = live.valid
    spec = _fetch(monkeypatch, today, today + timedelta(days=1), [], live=live)
    assert "No summary is valid at" in spec["note"]
    assert f"valid {live.valid:%Y-%m-%d} 2400 UT" in spec["note"]
    assert spec["ref_valid"] == live.valid.isoformat()


def test_a_day_the_archive_refused_is_not_reported_as_a_quiet_sun(monkeypatch):
    days = _days(date(2017, 9, 4), 3)
    snapshots = [_snapshot(days[0], [("2673", -8, 16, 680, 28, "Beta")]),
                 _snapshot(days[2], [("2673", -9, 45, 960, 24, "Beta")])]
    spec = _fetch(monkeypatch, days[0], days[-1] + timedelta(days=1), snapshots,
                  unread={days[1]: "503 Server Error"})
    assert "Could not read 1 of 3 days" in spec["note"]
    assert "not from the Sun" in spec["note"]


def test_a_day_that_was_never_published_says_that_instead(monkeypatch):
    days = _days(date(2017, 9, 4), 3)
    snapshots = [_snapshot(days[0], [("2673", -8, 16, 680, 28, "Beta")]),
                 _snapshot(days[2], [("2673", -9, 45, 960, 24, "Beta")])]
    spec = _fetch(monkeypatch, days[0], days[-1] + timedelta(days=1), snapshots)
    assert "No summary was published for 2017-09-05" in spec["note"]


def test_nothing_readable_at_all_is_an_error_not_an_empty_disk(monkeypatch):
    with pytest.raises(RuntimeError, match="Could not read"):
        _fetch(monkeypatch, date(2017, 9, 4), date(2017, 9, 5), [],
               unread={date(2017, 9, 4): "503 Server Error"})


# -- export ---------------------------------------------------------------


def test_the_csv_carries_every_day_not_just_the_one_on_the_disk(monkeypatch):
    days = _days(date(2024, 5, 8), 2)
    snapshots = [_snapshot(days[0], [("3664", 17, 10, 1000, 20, "Beta")]),
                 _snapshot(days[1], [("3664", 17, 20, 2000, 40, "Beta-Gamma-Delta")])]
    spec = _fetch(monkeypatch, days[0], days[-1] + timedelta(days=1), snapshots)
    csv = export.spec_to_csv(spec, "active regions 2024-05-08 to 2024-05-09")
    lines = [line for line in csv.splitlines() if not line.startswith("#")]
    assert lines[0].startswith("region,valid_utc,section,location")
    assert len(lines) == 3
    assert lines[1].startswith("3664,2024-05-08,spots,")
    assert export.filename(spec, "csv").startswith("solarquick_active_regions")
