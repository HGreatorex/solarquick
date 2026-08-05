"""SDO image quicklook source (AIA EUV channels + HMI) via the Helioviewer API.

Quicklook philosophy: no FITS downloads, no local processing. The backend asks
Helioviewer which images actually exist near each sample time (getClosestImage),
then hands the browser takeScreenshot URLs that render full-disk PNGs on
Helioviewer's servers. These are JPEG2000 browse images — fine for "what did the
Sun look like", not for photometry.

Query examples: "sdo aia 304 yesterday", "aia 171 on 10 July 2026",
"hmi magnetogram 2026-08-01".
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import requests

from solarapp.core.registry import DataSource, PlotSpec

HV = "https://api.helioviewer.org/v2"
IMAGE_PX = 1024
# AIA full field is 4096 px × 0.6″/px ≈ 2458″; fit it into IMAGE_PX
IMAGE_SCALE = 2.4  # arcsec / pixel
MAX_FRAMES = 8

# Helioviewer data source ids: https://api.helioviewer.org/docs/v2/appendix/data_sources.html
CHANNELS = {
    f"aia_{wl}": {
        "source_id": sid,
        "layer": f"[SDO,AIA,AIA,{wl},1,100]",
        "label": f"AIA {wl} Å",
    }
    for wl, sid in [(94, 8), (131, 9), (171, 10), (193, 11), (211, 12),
                    (304, 13), (335, 14), (1600, 15), (1700, 16), (4500, 17)]
}
CHANNELS["hmi_continuum"] = {
    "source_id": 18, "layer": "[SDO,HMI,HMI,continuum,1,100]", "label": "HMI continuum",
}
CHANNELS["hmi_magnetogram"] = {
    "source_id": 19, "layer": "[SDO,HMI,HMI,magnetogram,1,100]", "label": "HMI magnetogram",
}
AIA_WAVELENGTHS = [94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500]


def sample_times(start: datetime, end: datetime, max_frames: int = MAX_FRAMES) -> list[datetime]:
    """Evenly spaced sample times in [start, end): one per hour, capped at max_frames."""
    duration = end - start
    hours = duration.total_seconds() / 3600
    n = max(1, min(max_frames, math.ceil(hours)))
    return [start + duration * i / n for i in range(n)]


class SdoImages(DataSource):
    name = "sdo_images"
    display_name = "SDO images (AIA / HMI)"
    keywords = [
        "sdo", "aia", "hmi", "magnetogram", "continuum",
        "solar image", "sun image", "euv image",
    ]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        if "magnetogram" in ql:
            return {"channel": "hmi_magnetogram"}, []
        if "continuum" in ql or "intensitygram" in ql or "white light" in ql:
            return {"channel": "hmi_continuum"}, []
        for wl in AIA_WAVELENGTHS:
            if re.search(rf"\b{wl}\b", ql):
                return {"channel": f"aia_{wl}"}, [str(wl)]
        return {"channel": "aia_171"}, []

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        channel = CHANNELS[extras.get("channel", "aia_171")]
        frames, seen_ids, max_gap_h = [], set(), 0.0

        for t in sample_times(start, end):
            actual, img_id = self._closest_image(t, channel["source_id"])
            if img_id is not None and img_id in seen_ids:
                continue  # data gap: same image matched two sample times
            seen_ids.add(img_id)
            max_gap_h = max(max_gap_h, abs((actual - t).total_seconds()) / 3600)
            frames.append({
                "time": actual.isoformat() + "+00:00",
                "url": self._screenshot_url(actual, channel["layer"]),
                "caption": f"{channel['label']}  {actual:%Y-%m-%d %H:%M:%S} UT",
            })

        spec: PlotSpec = {
            "kind": "images",
            "title": f"SDO {channel['label']}  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "images": frames,
            "provenance": "Helioviewer.org (JPEG2000 browse images — quicklook, not science data)",
        }
        if max_gap_h > 12:
            spec["note"] = (
                f"Nearest available image is {max_gap_h:.0f} h from the requested "
                "time — the archive may not cover this interval."
            )
        return spec

    # -- Helioviewer API --------------------------------------------------

    def _closest_image(self, t: datetime, source_id: int) -> tuple[datetime, str | None]:
        """Actual timestamp (and id) of the archive image nearest t.

        Falls back to the requested time if the metadata call fails — the
        screenshot endpoint snaps to the nearest image on its own.
        """
        try:
            r = requests.get(
                f"{HV}/getClosestImage/",
                params={"date": f"{t:%Y-%m-%dT%H:%M:%SZ}", "sourceId": source_id},
                timeout=15,
            )
            r.raise_for_status()
            info = r.json()
            return datetime.fromisoformat(info["date"]).replace(tzinfo=t.tzinfo), str(info["id"])
        except (requests.RequestException, KeyError, ValueError):
            return t, None

    def _screenshot_url(self, t: datetime, layer: str) -> str:
        return (
            f"{HV}/takeScreenshot/?date={t:%Y-%m-%dT%H:%M:%SZ}"
            f"&imageScale={IMAGE_SCALE}&layers={layer}"
            f"&x0=0&y0=0&width={IMAGE_PX}&height={IMAGE_PX}"
            "&display=true&watermark=false"
        )
