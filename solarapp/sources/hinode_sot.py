"""Hinode SOT filtergrams (high-resolution photosphere/chromosphere imaging).

Not on Helioviewer, so this plugin fetches FITS from the VSO (SDAC) and
renders PNGs server-side (matplotlib, percentile-clipped grayscale, embedded
as data URIs). The VSO serves several SOT products; only filtergrams render
as images, so results are filtered by archive path: FG (broadband/narrowband
filtergrams) preferred, FGIV (NFI Stokes filtergrams) as fallback, and the
SP spectropolarimeter slit data is skipped.

SOT imaged 2006-10-25 – 2016-02-25 (camera failure), usually in targeted
campaigns: many days have no data, and images cover a partial field of
view, not the full disk.

Query examples: "hinode sot 2013-11-15", "sot for 2012-07-12".
"""
from __future__ import annotations

import base64
import io
from datetime import datetime

import numpy as np

from solarapp.core.registry import DataSource, PlotSpec
from solarapp.core.tsutil import check_mission_span

MAX_FRAMES = 4


class HinodeSot(DataSource):
    name = "hinode_sot"
    display_name = "Hinode SOT (filtergrams)"
    keywords = ["sot", "hinode", "hinode sot", "filtergram"]

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        check_mission_span(start, end, "2006-10-25", "2016-02-25", "Hinode SOT")

        from sunpy.net import Fido
        from sunpy.net import attrs as a

        q = Fido.search(a.Time(start, end), a.Instrument("sot"))
        table = next((r for r in q if len(r)), None)
        if table is None:
            raise RuntimeError(
                "No Hinode SOT data for this interval. SOT observed in "
                "targeted campaigns (2006-10-25 – 2016-02-25); many days "
                "have no coverage."
            )

        # keep filtergrams only (fileid path encodes the product)
        fg = [i for i, row in enumerate(table) if "/FG/" in str(row["fileid"])]
        if not fg:
            fg = [i for i, row in enumerate(table) if "/FGIV/" in str(row["fileid"])]
        if not fg:
            raise RuntimeError(
                "Hinode SOT has data here, but only spectropolarimeter (SP) "
                "slit scans — no filtergram images to show. Try another day."
            )

        picks = sorted({fg[int(i * (len(fg) - 1) / max(1, MAX_FRAMES - 1))]
                        for i in range(min(MAX_FRAMES, len(fg)))})
        files = Fido.fetch(table[picks])
        if not files:
            raise RuntimeError("VSO returned no downloadable SOT files for this interval.")

        images = []
        for f in sorted(files):
            try:
                images.append(self._render(f))
            except Exception:
                continue  # skip unreadable files, keep the rest
        if not images:
            raise RuntimeError("Could not render any of the fetched SOT files.")

        return {
            "kind": "images",
            "title": f"Hinode SOT  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "images": images,
            "provenance": f"Hinode SOT filtergrams via VSO/SDAC ({len(fg)} exposures "
                          f"in interval, showing {len(images)}); partial-Sun field of view",
        }

    def _render(self, path: str) -> dict:
        # matplotlib.image.imsave (not pyplot: fetches may run in worker threads,
        # and pyplot's global figure state is not thread-safe)
        from matplotlib import image as mpimg
        from astropy.io import fits

        with fits.open(path) as h:
            hdu = h[0]
            data = np.asarray(hdu.data, dtype=float)
            while data.ndim > 2:  # FGIV etc. stack planes; show the first
                data = data[0]
            hdr = hdu.header

        lo, hi = np.nanpercentile(data, [1, 99.5])
        buf = io.BytesIO()
        mpimg.imsave(buf, np.clip(data, lo, hi), cmap="gray", origin="lower", format="png")
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        t = datetime.fromisoformat(hdr.get("DATE_OBS", hdr.get("DATE-OBS")))
        what = " ".join(str(hdr.get(k, "")) for k in ("OBS_TYPE", "WAVE_ID")).strip() \
            or "filtergram"
        return {
            "time": t.isoformat() + "+00:00",
            "url": uri,
            "caption": f"SOT {what}  {t:%Y-%m-%d %H:%M:%S} UT",
        }
