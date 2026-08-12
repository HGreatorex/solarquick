"""CSV/JSON export of PlotSpec blocks."""
import csv
import io

import pytest

from solarapp.core import export


def parse_csv(text):
    """Split an export into (header comment lines, csv rows)."""
    lines = text.splitlines()
    header = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#") and ln.strip()]
    return header, list(csv.reader(io.StringIO("\n".join(body))))


TIMESERIES = {
    "title": "GOES XRS  2026-08-11 00:00 – 2026-08-12 00:00 UT",
    "series": [
        {"name": "XRS-A 0.5–4 Å", "times": ["2026-08-11T00:00:00+00:00",
                                            "2026-08-11T00:01:00+00:00"],
         "values": [1.2e-8, 1.3e-8]},
        {"name": "XRS-B 1–8 Å", "times": ["2026-08-11T00:00:00+00:00",
                                          "2026-08-11T00:01:00+00:00"],
         "values": [4.5e-7, 4.6e-7]},
    ],
    "yaxis": {"label": "Flux (W m⁻²)", "type": "log"},
    "provenance": "NOAA SWPC realtime (primary GOES)",
}


# -- timeseries ----------------------------------------------------------

def test_timeseries_csv_shape():
    header, rows = parse_csv(export.spec_to_csv(TIMESERIES, "goes sxr yesterday"))
    assert rows[0] == ["time_utc", "XRS-A 0.5–4 Å", "XRS-B 1–8 Å"]
    assert len(rows) == 3  # header + 2 samples
    assert rows[1][0] == "2026-08-11T00:00:00+00:00"
    assert float(rows[1][1]) == pytest.approx(1.2e-8)
    assert float(rows[1][2]) == pytest.approx(4.5e-7)


def test_header_records_provenance_and_query():
    header, _ = parse_csv(export.spec_to_csv(TIMESERIES, "goes sxr yesterday"))
    blob = "\n".join(header)
    assert "goes sxr yesterday" in blob
    assert "NOAA SWPC realtime" in blob
    assert "GOES XRS" in blob
    assert "SolarQuick" in blob


def test_sampling_is_disclosed_when_binned():
    spec = {**TIMESERIES, "sampling": "mean-resampled into 4 s bins from 21600 archive samples"}
    header, _ = parse_csv(export.spec_to_csv(spec))
    assert any("mean-resampled into 4 s bins" in h for h in header), \
        "a binned export must say so, or it looks like raw instrument data"


def test_ragged_series_are_aligned_not_truncated():
    # two series on different time grids must both survive, padded with blanks
    spec = {"title": "t", "series": [
        {"name": "a", "times": ["2026-01-01T00:00:00+00:00"], "values": [1.0]},
        {"name": "b", "times": ["2026-01-01T00:00:30+00:00"], "values": [2.0]},
    ]}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert rows[0] == ["time_utc", "a", "b"]
    assert rows[1] == ["2026-01-01T00:00:00+00:00", "1.0", ""]
    assert rows[2] == ["2026-01-01T00:00:30+00:00", "", "2.0"]


def test_rows_are_time_sorted():
    spec = {"title": "t", "series": [
        {"name": "a", "times": ["2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00"],
         "values": [2.0, 1.0]},
    ]}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert [r[0] for r in rows[1:]] == ["2026-01-01T00:00:00+00:00",
                                        "2026-01-02T00:00:00+00:00"]


def test_spectrum_uses_numeric_x_axis():
    # EVE spectra plot irradiance against wavelength, not time
    spec = {"title": "EVE spectrum", "xaxis": {"label": "Wavelength (nm)"},
            "series": [{"name": "irradiance", "x": [30.4, 33.5], "values": [1.0, 2.0]}]}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert rows[0] == ["wavelength_nm", "irradiance"]
    assert rows[1] == ["30.4", "1.0"]


# -- other kinds ---------------------------------------------------------

def test_spectrogram_matrix():
    spec = {"kind": "spectrogram", "title": "WAVES RAD1",
            "times": ["t0", "t1"], "freqs": [20.0, 1040.0],
            "z": [[1, 2], [3, 4]], "yaxis": {"label": "Frequency (kHz)"}}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert rows[0] == ["frequency_khz", "t0", "t1"]
    assert rows[1] == ["20.0", "1", "2"]
    assert rows[2] == ["1040.0", "3", "4"]


def test_events_table():
    spec = {"kind": "events", "title": "Flares", "events": [
        {"class": "X9.3", "start": "2017-09-06T11:53:00+00:00",
         "peak": "2017-09-06T12:02:00+00:00", "end": "2017-09-06T12:10:00+00:00",
         "x": 515.1, "y": -231.9, "ar": 12673},
    ]}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert rows[0][0] == "goes_class" and "noaa_ar" in rows[0]
    assert rows[1][0] == "X9.3" and rows[1][-1] == "12673"


def test_positions_table():
    spec = {"kind": "positions", "title": "Positions", "bodies": [
        {"name": "Earth", "type": "planet", "x": 1.0, "y": 0.0, "r": 1.0, "lon_deg": 0.0},
    ]}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert rows[0] == ["body", "type", "x_au", "y_au", "r_au", "stonyhurst_lon_deg"]
    assert rows[1][0] == "Earth"


def test_earthmap_includes_track():
    spec = {"kind": "earthmap", "title": "Illumination",
            "points": [{"name": "subsolar", "lat": 15.6, "lon": 1.0}],
            "track": {"lat": [23.4, 23.3], "lon": [0.1, 0.2]}}
    _, rows = parse_csv(export.spec_to_csv(spec))
    assert rows[0] == ["point", "latitude_deg", "longitude_deg"]
    assert len(rows) == 4  # header + subsolar + 2 track samples


def test_panels_concatenated_with_own_headers():
    spec = {"kind": "panels", "title": "EUVS", "provenance": "NCEI",
            "panels": [
                {"title": "A", "series": [{"name": "a", "times": ["t"], "values": [1]}]},
                {"title": "B", "series": [{"name": "b", "times": ["t"], "values": [2]}]},
            ]}
    text = export.spec_to_csv(spec)
    assert text.count("# Title: A") == 1 and text.count("# Title: B") == 1
    # provenance falls through from the wrapper to each panel
    assert text.count("# Source: NCEI") == 2


# -- refusals ------------------------------------------------------------

def test_images_are_not_exportable_as_csv():
    spec = {"kind": "images", "title": "AIA 304", "images": [{"url": "x", "caption": "c"}]}
    with pytest.raises(export.NotExportable, match="frame download"):
        export.spec_to_csv(spec)


def test_multi_directs_to_per_block_export():
    with pytest.raises(export.NotExportable, match="separately"):
        export.spec_to_csv({"kind": "multi", "items": []})


def test_empty_series_refused():
    with pytest.raises(export.NotExportable):
        export.spec_to_csv({"title": "t", "series": []})


def test_empty_events_refused():
    with pytest.raises(export.NotExportable):
        export.spec_to_csv({"kind": "events", "title": "t", "events": []})


# -- filenames -----------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("GOES XRS  2026-08-11 00:00 – 2026-08-12 00:00 UT",
     "solarquick_goes_xrs_2026-08-11.csv"),
    ("SDO AIA 304 Å  2026-08-09 00:00 – 2026-08-10 00:00 UT",
     "solarquick_sdo_aia_304_2026-08-09.csv"),
])
def test_filename_is_descriptive(title, expected):
    assert export.filename({"title": title}, "csv") == expected


def test_filename_survives_a_missing_title():
    assert export.filename({}, "json") == "solarquick_export.json"


def test_blocks_flattens_multi():
    a, b = {"title": "a"}, {"title": "b"}
    assert export.blocks({"kind": "multi", "items": [a, b]}) == [a, b]
    assert export.blocks(a) == [a]
