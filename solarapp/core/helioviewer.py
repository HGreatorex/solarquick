"""Shared machinery for image sources backed by the Helioviewer API.

Any imager whose JPEG2000 browse products Helioviewer ingests (SDO, SOHO,
PROBA2, Solar Orbiter EUI, ...) gets quicklook support by subclassing
HelioviewerImageSource with a channel table — no download or rendering code.

Per channel: {source_id, layer, label, scale}. `scale` is arcsec/pixel for the
1024 px screenshot, chosen per instrument to frame its field of view (2.4 for
AIA's full disk, 60 for LASCO C3's ±30 R_sun).
"""
from __future__ import annotations

import math
import re
from datetime import datetime

import requests

from solarapp.core.registry import DataSource, PlotSpec

HV = "https://api.helioviewer.org/v2"
IMAGE_PX = 1024
MAX_FRAMES = 8


def sample_times(start: datetime, end: datetime, max_frames: int = MAX_FRAMES) -> list[datetime]:
    """Evenly spaced sample times in [start, end): one per hour, capped at max_frames."""
    duration = end - start
    hours = duration.total_seconds() / 3600
    n = max(1, min(max_frames, math.ceil(hours)))
    return [start + duration * i / n for i in range(n)]


def match_wavelength(query_lower: str, wavelengths: list[int]) -> int | None:
    """Return the first instrument wavelength that appears as a word in the query."""
    for wl in wavelengths:
        if re.search(rf"\b{wl}\b", query_lower):
            return wl
    return None


class HelioviewerImageSource(DataSource):
    """Base class: subclasses set channels, default_channel, name/keywords/parse_extras."""

    channels: dict[str, dict] = {}
    default_channel: str = ""

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        channel = self.channels[extras.get("channel", self.default_channel)]
        # spatial crop: center (arcsec from disk center) + field of view (arcsec)
        crop = None
        if "fov" in extras:
            crop = (float(extras.get("center_x", 0.0)),
                    float(extras.get("center_y", 0.0)),
                    float(extras["fov"]))
        frames, seen_ids, max_gap_h = [], set(), 0.0

        for t in sample_times(start, end):
            actual, img_id = self._closest_image(t, channel["source_id"])
            if img_id is not None and img_id in seen_ids:
                continue  # data gap: same image matched two sample times
            seen_ids.add(img_id)
            max_gap_h = max(max_gap_h, abs((actual - t).total_seconds()) / 3600)
            frames.append({
                "time": actual.isoformat() + "+00:00",
                "url": self._screenshot_url(actual, channel, crop),
                "caption": f"{channel['label']}  {actual:%Y-%m-%d %H:%M:%S} UT",
            })

        title = f"{channel['label']}  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT"
        if crop:
            title += f"  ·  {crop[2]:.0f}″ crop at ({crop[0]:.0f}″, {crop[1]:.0f}″)"
        spec: PlotSpec = {
            "kind": "images",
            "title": title,
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

    def _screenshot_url(self, t: datetime, channel: dict, crop: tuple | None = None) -> str:
        if crop:
            cx, cy, fov = crop
            # crop centers arrive as helioprojective (north-positive) arcsec;
            # Helioviewer's y0 is image-space (south-positive), hence the flip
            x0, y0 = cx, -cy
            scale = fov / IMAGE_PX
        else:
            x0, y0, scale = 0.0, 0.0, channel["scale"]
        return (
            f"{HV}/takeScreenshot/?date={t:%Y-%m-%dT%H:%M:%SZ}"
            f"&imageScale={scale}&layers={channel['layer']}"
            f"&x0={x0:.1f}&y0={y0:.1f}&width={IMAGE_PX}&height={IMAGE_PX}"
            "&display=true&watermark=false"
        )
