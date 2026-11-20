"""FISM2 flare spectral irradiance (LASP) as a looping spectral movie.

FISM2's flare product is an empirical model spectrum, 0.01–190 nm in 0.1 nm
bins, one spectrum every 60 s, from 2003 onwards. This plugin serves it raw:
model irradiance in W m⁻² nm⁻¹ exactly as LASP publishes it — no adjustment to
GOES/EVE observations (that is what fism-obs does) and no normalisation to a
pre-flare or peak spectrum.

The result is a `spectral_movie` PlotSpec: every 60 s spectrum in the window
as a frame, plus the FISM 0.1–0.8 nm band integrated into a light curve so the
frame can be placed on the flare. The browser loops it.

Data: the daily netCDF files LASP serves for the flare product (~17 MB each,
cached in ~/.cache/solarapp). The archive lags real time by about a week.

Query examples: "fism 2017-09-10 15:30 to 18:00", "fism movie yesterday
afternoon"; and every flare event view ("biggest flare since 2014", "flare at
2017-09-06 12:02") adds it automatically.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

import numpy as np

from solarapp.core import fetchcache
from solarapp.core.fetchcache import cached_download, listing
from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import check_max_days

FISM = "https://lasp.colorado.edu/eve/data_access/evewebdata/fism/flare_hr_data/netcdf"
MAX_DAYS = 1
MAX_FRAMES = 240          # ~456k values to the browser; longer windows are strided
BAND_NM = (0.1, 0.8)      # light-curve band: GOES XRS-B equivalent
BIN_NM = 0.1


def _sig(a: np.ndarray, digits: int = 4) -> list:
    """Round to `digits` significant figures with a short JSON repr.

    Going through text is what keeps the payload small: a float32 widened to
    float64 serialises as 1.8690613501348707e-08, the parsed '1.869e-08' as
    itself. Non-positive or missing values become null (gaps, not zeros, on a
    log axis).
    """
    a = np.asarray(a, dtype=float)
    out = np.char.mod(f"%.{digits}g", np.where(np.isfinite(a), a, 0.0)).astype(float)
    return [v if v > 0 else None for v in out.tolist()]


class Fism(DataSource):
    name = "fism"
    display_name = "FISM2 flare spectrum (model, 0.1 nm, 60 s)"
    keywords = ["fism", "fism2", "fism spectrum", "fism movie", "fism flare"]
    coverage = ("2003-01-01", None)

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)
        check_max_days(start, end, MAX_DAYS, "FISM2")
        times, waves, irr = self._read(start, end)
        if not len(times):
            raise RuntimeError(
                f"FISM2 has no spectra between {start:%Y-%m-%d %H:%M} and "
                f"{end:%H:%M} UT."
            )

        in_band = (waves > BAND_NM[0]) & (waves < BAND_NM[1])
        band = np.nansum(np.where(irr[:, in_band] > 0, irr[:, in_band], 0.0), axis=1) * BIN_NM

        n = len(times)
        stride = max(1, math.ceil(n / MAX_FRAMES))
        idx = np.arange(0, n, stride)
        sampling = None
        if stride > 1:
            sampling = (f"every {stride}th 60 s spectrum ({len(idx)} of {n} frames); "
                        "the light curve keeps full 60 s cadence")

        pos = irr[irr > 0]
        lo, hi = (float(pos.min()), float(pos.max())) if pos.size else (1e-10, 1e-2)
        span = f"{times[0]:%Y-%m-%d %H:%M}–{times[-1]:%H:%M} UT"
        return {
            "kind": "spectral_movie",
            "title": f"FISM2 flare spectrum  {span}",
            "times": [t.isoformat() for t in times[idx]],
            "wavelengths": np.round(waves, 2).tolist(),
            "frames": [_sig(irr[i]) for i in idx],
            "xaxis": {"label": "Wavelength (nm)"},
            # fixed for the whole movie so frames are comparable
            "yaxis": {"label": "Irradiance (W m⁻² nm⁻¹)", "type": "log",
                      "range": [lo / 2, hi * 2]},
            "lightcurve": {
                "name": f"FISM2 {BAND_NM[0]:g}–{BAND_NM[1]:g} nm",
                "times": [t.isoformat() for t in times],
                "values": _sig(band),
                "yaxis": {"label": "Irradiance (W m⁻²)", "type": "log"},
            },
            "cadence_s": 60 * stride,
            "provenance": "FISM2 flare product (LASP), raw model irradiance — "
                          "not adjusted to observations",
            **({"sampling": sampling} if sampling else {}),
        }

    # -- LASP access ------------------------------------------------------

    def _read(self, start: datetime, end: datetime):
        import h5netcdf

        times, rows, waves = [], [], None
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < end:
            path = self._day_file(day)
            with h5netcdf.File(path, "r") as f:
                sod = np.asarray(f.variables["utc"][:], dtype=float)
                t = np.array([day + timedelta(seconds=s) for s in sod])
                keep = np.nonzero((t >= start) & (t <= end))[0]
                if keep.size:
                    # contiguous slice: h5py fancy-indexing is slow on 1440×1900
                    block = np.asarray(f.variables["irradiance"][keep[0]:keep[-1] + 1, :],
                                       dtype=float)
                    rows.append(block[keep - keep[0]])
                    times.extend(t[keep])
                if waves is None:
                    waves = np.asarray(f.variables["wavelength"][:], dtype=float)
            day += timedelta(days=1)
        irr = np.vstack(rows) if rows else np.empty((0, 0))
        return np.array(times), waves, irr

    def _day_file(self, day: datetime):
        stem = f"FISM_60sec_{day:%Y%j}"
        # a cached day needs no directory listing (one ~400-entry page per year)
        cached = sorted(fetchcache.CACHE_DIR.glob(f"{stem}_v*.nc"))
        if cached:
            return cached[-1]
        base = f"{FISM}/{day:%Y}"
        try:
            names = re.findall(rf"{stem}_v\d+_\d+\.nc", listing(f"{base}/"))
        except Exception:
            names = []
        if not names:
            raise RuntimeError(
                f"No FISM2 flare file for {day:%Y-%m-%d} yet (LASP publishes "
                "about a week behind real time)."
            )
        name = sorted(set(names))[-1]
        return cached_download(f"{base}/{name}", name)
