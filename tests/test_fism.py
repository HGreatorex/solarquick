"""FISM2 spectral movie: routing, the netCDF read, frame striding and export.

The LASP download is replaced by synthetic daily files written with h5netcdf
in FISM's own layout (utc × wavelength), so nothing touches the network.
"""
from datetime import datetime, timedelta, timezone

import h5netcdf
import numpy as np
import pytest

from solarapp.core import export
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry
from solarapp.sources import fism as fism_mod
from solarapp.sources.fism import Fism, _sig

REG = get_registry()
WAVES = np.arange(1900) * 0.1 + 0.05


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def write_day(path, day_index):
    """1440 one-minute spectra; irradiance = (minute + 1) × 1e-6 × (day + 1)."""
    utc_s = np.arange(1440, dtype=np.int32) * 60 + 2
    irr = ((np.arange(1440)[:, None] + 1) * 1e-6 * (day_index + 1)
           * np.ones((1, 1900))).astype(np.float32)
    with h5netcdf.File(path, "w") as f:
        f.dimensions = {"utc": 1440, "wavelength": 1900}
        f.create_variable("utc", ("utc",), data=utc_s)
        f.create_variable("wavelength", ("wavelength",), data=WAVES.astype(np.float32))
        f.create_variable("irradiance", ("utc", "wavelength"), data=irr)


@pytest.fixture
def stub_files(tmp_path, monkeypatch):
    base = utc(2017, 9, 10)

    def day_file(self, day):
        k = (day - base).days
        path = tmp_path / f"day{k}.nc"
        if not path.exists():
            write_day(path, k)
        return path

    monkeypatch.setattr(Fism, "_day_file", day_file)


def test_routes_to_fism_with_window():
    p = parse("fism 2017-09-10 15:30 to 18:00", REG)
    assert [r.source for r in p.requests] == ["fism"]
    assert (p.start, p.end) == (utc(2017, 9, 10, 15, 30), utc(2017, 9, 10, 18, 0))


def test_movie_spec(stub_files):
    s = Fism().fetch(utc(2017, 9, 10, 15, 30), utc(2017, 9, 10, 18, 0))
    assert s["kind"] == "spectral_movie"
    assert len(s["frames"]) == len(s["times"]) == 150
    assert len(s["frames"][0]) == len(s["wavelengths"]) == 1900
    assert s["times"][0] == "2017-09-10T15:30:02+00:00"
    # raw values, untouched: minute 930 → 931e-6
    assert s["frames"][0][0] == pytest.approx(931e-6, rel=1e-3)
    # fixed y range spans every frame
    lo, hi = s["yaxis"]["range"]
    assert lo < 931e-6 and hi > 1080e-6
    # 0.1–0.8 nm band = 7 bins × 0.1 nm
    assert s["lightcurve"]["values"][0] == pytest.approx(931e-6 * 0.7, rel=1e-3)
    assert "sampling" not in s


def test_long_window_is_strided_and_disclosed(stub_files):
    s = Fism().fetch(utc(2017, 9, 10, 0, 0), utc(2017, 9, 10, 23, 59))
    assert len(s["frames"]) <= fism_mod.MAX_FRAMES
    assert len(s["lightcurve"]["values"]) == 1439
    assert "every 6th" in s["sampling"]


def test_window_crossing_midnight_reads_both_days(stub_files):
    s = Fism().fetch(utc(2017, 9, 10, 23, 50), utc(2017, 9, 11, 0, 10))
    assert s["times"][0].startswith("2017-09-10T23:50")
    assert s["times"][-1].startswith("2017-09-11T00:09")
    assert s["frames"][-1][0] == pytest.approx(10e-6 * 2, rel=1e-3)


def test_over_one_day_refused():
    with pytest.raises(RuntimeError, match="limited to 1 days"):
        Fism().fetch(utc(2017, 9, 10), utc(2017, 9, 12))


def test_before_2003_refused():
    with pytest.raises(RuntimeError, match="starts 2003"):
        Fism().fetch(utc(2002, 5, 1), utc(2002, 5, 1, 2))


def test_sig_rounds_and_nulls():
    assert _sig(np.array([1.8690613e-08, 0.0, -1.0, np.nan])) == [1.869e-08, None, None, None]


def test_movie_csv_is_wavelength_by_time(stub_files):
    s = Fism().fetch(utc(2017, 9, 10, 15, 30), utc(2017, 9, 10, 15, 33))
    lines = [l for l in export.spec_to_csv(s, "fism").splitlines() if not l.startswith("#")]
    assert lines[0].split(",")[0] == "wavelength_nm"
    assert len(lines[0].split(",")) == 1 + 3
    assert len(lines) == 1 + 1900
