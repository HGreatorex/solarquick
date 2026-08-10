"""Wind/WAVES radio dynamic spectra (RAD1 + RAD2 receivers).

Daily L2 CDFs from NASA SPDF, read with cdflib. Rendered as two spectrogram
panels: RAD2 (1.075–13.825 MHz) above RAD1 (20–1040 kHz), the usual layout
for interplanetary type II/III burst quicklooks. Intensity is shown as dB
above the daily galactic background (the standard WAVES normalisation).

Coverage from 1994-11; the L2 archive lags real time by a few weeks.

Query examples: "wind waves yesterday", "waves 2026-07-11",
"radio spectrum 2026-07-11".
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np

from solarapp.core.fetchcache import cached_download, listing
from solarapp.core.registry import DataSource, PlotSpec

SPDF = "https://spdf.gsfc.nasa.gov/pub/data/wind/waves"
MAX_DAYS = 2          # 256 freqs x ~1800 sweeps/day/receiver
MAX_COLS_PER_DAY = 400

RECEIVERS = [
    # (dir name, label, divisor from Hz, axis label)
    ("rad2", "RAD2", 1e6, "Frequency (MHz)"),
    ("rad1", "RAD1", 1e3, "Frequency (kHz)"),
]


class WindWaves(DataSource):
    name = "wind_waves"
    display_name = "Wind/WAVES (radio spectra)"
    keywords = ["waves", "wind waves", "wind", "radio spectrum", "radio spectra",
                "type ii", "type iii", "radio burst"]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        if end - start > timedelta(days=MAX_DAYS):
            raise RuntimeError(
                f"Wind/WAVES spectrogram requests are limited to {MAX_DAYS} days."
            )
        days = []
        d = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while d < end:
            days.append(d)
            d += timedelta(days=1)

        panels = []
        for rdir, label, divisor, ylabel in RECEIVERS:
            times, freqs, z = self._receiver_spectrogram(rdir, days, start, end)
            if times:
                panels.append({
                    "kind": "spectrogram",
                    "title": f"Wind/WAVES {label}",
                    "times": times,
                    "freqs": [f / divisor for f in freqs],
                    "z": z,
                    "zrange": [0, 20],
                    "zlabel": "dB > background",
                    "yaxis": {"label": ylabel, "type": "log"},
                    "xaxis": {"label": "Time (UT)"},
                })
        if not panels:
            raise RuntimeError(
                "No Wind/WAVES L2 files found for this interval "
                "(the SPDF archive lags real time by a few weeks)."
            )
        return {
            "kind": "panels",
            "panels": panels,
            "title": f"Wind/WAVES  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "provenance": "Wind/WAVES L2 (NASA SPDF), dB above galactic background",
        }

    # -- helpers ----------------------------------------------------------

    def _receiver_spectrogram(self, rdir: str, days: list[datetime],
                              start: datetime, end: datetime):
        import cdflib

        times, freqs, cols = [], None, []
        for day in days:
            try:
                path = self._day_file(rdir, day)
            except Exception:
                continue  # missing day: leave a gap
            c = cdflib.CDF(str(path))
            epoch = cdflib.cdfepoch.to_datetime(c.varget("Epoch"))
            fr = np.asarray(c.varget("FREQUENCY"), dtype=float)  # Hz, fill-padded
            good = fr > 0
            freqs = fr[good].tolist()
            psd = np.asarray(c.varget("PSD_V2_S"), dtype=float)[:, good]
            bg = np.asarray(c.varget("BACKGROUND_S"), dtype=float)[good]
            with np.errstate(divide="ignore", invalid="ignore"):
                db = 10.0 * np.log10(psd / bg)
            db[~np.isfinite(db)] = 0.0

            stride = max(1, len(epoch) // MAX_COLS_PER_DAY)
            for i in range(0, len(epoch), stride):
                t = epoch[i].astype("datetime64[s]").item().replace(tzinfo=timezone.utc)
                if start <= t <= end:
                    times.append(t.isoformat())
                    cols.append(np.clip(db[i], 0, None).round(1).tolist())

        if not times:
            return [], [], []
        # Plotly heatmap wants z[freq][time]
        z = np.asarray(cols).T.tolist()
        return times, freqs, z

    def _day_file(self, rdir: str, day: datetime):
        """Resolve the day's CDF (version suffix varies) and download via cache."""
        base = f"{SPDF}/{rdir}_l2/{day:%Y}"
        pattern = rf"wi_l2_wav_{rdir}_{day:%Y%m%d}_v\d+\.cdf"
        names = re.findall(pattern, listing(f"{base}/"))
        if not names:
            raise FileNotFoundError(f"no {rdir} file for {day:%Y-%m-%d}")
        name = sorted(set(names))[-1]  # newest version
        return cached_download(f"{base}/{name}", name)
