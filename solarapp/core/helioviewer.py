"""Shared machinery for image sources backed by the Helioviewer API.

Any imager whose JPEG2000 browse products Helioviewer ingests (SDO, SOHO,
PROBA2, Solar Orbiter EUI, ...) gets quicklook support by subclassing
HelioviewerImageSource with a channel table — no download or rendering code.

Per channel: {source_id, layer, label, scale}. `scale` is arcsec/pixel for the
1024 px screenshot, chosen per instrument to frame its field of view (2.4 for
AIA's full disk, 60 for LASCO C3's ±30 R_sun).

Each frame is published at two sizes. The filmstrip showed eight 1 MiB
full-resolution renders to fill eight 72 px thumbnails — ~8 MiB and ~20 s of
Helioviewer render time per query. `thumb_url` is the same frame at THUMB_PX
(~20 KiB), and the browser only loads `url` for the frame actually on display.
Field of view is held constant by scaling arcsec/pixel inversely with the pixel
width, so a thumbnail frames the same view rather than cropping into it.
"""
from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from solarapp.core import httpclient
from solarapp.core.registry import DataSource, PlotSpec

HV = "https://api.helioviewer.org/v2"
IMAGE_PX = 1024
GRID_PX = 512    # one cell of the UI's image grid
THUMB_PX = 128
MAX_FRAMES = 8
MAX_REQUESTED_FRAMES = 60  # the UI's movie/stills timeline (`frames` extra)
METADATA_WORKERS = 8       # per source; several sources run at once

# pooled connections live in core.httpclient — these calls are small, numerous
# and all to one host, so a fresh TLS handshake each time dominated their cost


def sample_times(start: datetime, end: datetime, max_frames: int = MAX_FRAMES) -> list[datetime]:
    """Evenly spaced sample times in [start, end): one per hour, capped at max_frames."""
    duration = end - start
    hours = duration.total_seconds() / 3600
    n = max(1, min(max_frames, math.ceil(hours)))
    return [start + duration * i / n for i in range(n)]


def even_times(start: datetime, end: datetime, n: int) -> list[datetime]:
    """`n` times spanning [start, end] inclusive — the UI's frame timeline."""
    n = max(1, min(int(n), MAX_REQUESTED_FRAMES))
    if n == 1:
        return [(start + (end - start) / 2).replace(microsecond=0)]
    return [(start + (end - start) * i / (n - 1)).replace(microsecond=0)
            for i in range(n)]


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
    display = "images"

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        self.check_coverage(start, end)
        channel = self.channels[extras.get("channel", self.default_channel)]
        crop = self._crop(extras)
        # `frames` (from the UI) asks for an evenly spaced timeline at any
        # cadence; without it, the text route keeps its hourly ≤8 filmstrip
        times = (even_times(start, end, extras["frames"]) if extras.get("frames")
                 else sample_times(start, end))
        # independent metadata lookups: run together rather than paying ~470 ms
        # of round trip each in series. Results are collected in request order
        # so the dedupe below still sees frames chronologically.
        if len(times) > 1:
            with ThreadPoolExecutor(max_workers=min(len(times), METADATA_WORKERS)) as pool:
                resolved = list(pool.map(
                    lambda t: self._closest_image(t, channel["source_id"]), times))
        else:
            resolved = [self._closest_image(times[0], channel["source_id"])]

        frames, seen_ids, max_gap_h = [], set(), 0.0
        for t, (actual, img_id) in zip(times, resolved):
            if img_id is not None and img_id in seen_ids:
                continue  # data gap: same image matched two sample times
            seen_ids.add(img_id)
            max_gap_h = max(max_gap_h, abs((actual - t).total_seconds()) / 3600)
            frames.append(self._frame(actual, t, channel, crop))

        title = f"{channel['label']}  {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT"
        if crop:
            title += f"  ·  {crop[2]:.0f}″ crop at ({crop[0]:.0f}″, {crop[1]:.0f}″)"
        spec: PlotSpec = {
            "kind": "images",
            "title": title,
            "label": channel["label"],
            "images": frames,
            "provenance": "Helioviewer.org (JPEG2000 browse images — quicklook, not science data)",
        }
        if max_gap_h > 12:
            spec["note"] = (
                f"Nearest available image is {max_gap_h:.0f} h from the requested "
                "time — the archive may not cover this interval."
            )
        return spec

    def frame_at(self, t: datetime, **extras) -> dict:
        """The single frame nearest `t` (the UI's flare-peak tick)."""
        channel = self.channels[extras.get("channel", self.default_channel)]
        actual, _ = self._closest_image(t, channel["source_id"])
        return self._frame(actual, t, channel, self._crop(extras))

    @staticmethod
    def _crop(extras: dict) -> tuple | None:
        """center (arcsec from disk center) + field of view (arcsec), if asked."""
        if "fov" not in extras:
            return None
        return (float(extras.get("center_x", 0.0)), float(extras.get("center_y", 0.0)),
                float(extras["fov"]))

    def _frame(self, actual: datetime, requested: datetime, channel: dict,
               crop: tuple | None) -> dict:
        return {
            # already tz-aware: appending an offset produced the
            # unparseable "...T09:59:53+00:00+00:00" in every JSON export
            "time": actual.isoformat(),
            # the timeline slot this frame fills, shared by every panel
            "t_req": requested.isoformat(),
            "url": self._screenshot_url(actual, channel, crop),
            "grid_url": self._screenshot_url(actual, channel, crop, px=GRID_PX),
            "thumb_url": self._screenshot_url(actual, channel, crop, px=THUMB_PX),
            "caption": f"{channel['label']}  {actual:%Y-%m-%d %H:%M:%S} UT",
        }

    # -- Helioviewer API --------------------------------------------------

    def _closest_image(self, t: datetime, source_id: int) -> tuple[datetime, str | None]:
        """Actual timestamp (and id) of the archive image nearest t.

        Falls back to the requested time if the metadata call fails — the
        screenshot endpoint snaps to the nearest image on its own.
        """
        try:
            r = httpclient.session().get(
                f"{HV}/getClosestImage/",
                params={"date": f"{t:%Y-%m-%dT%H:%M:%SZ}", "sourceId": source_id},
                timeout=15,
            )
            r.raise_for_status()
            info = r.json()
            return datetime.fromisoformat(info["date"]).replace(tzinfo=t.tzinfo), str(info["id"])
        except (requests.RequestException, KeyError, ValueError):
            return t, None

    def _screenshot_url(self, t: datetime, channel: dict, crop: tuple | None = None,
                        px: int = IMAGE_PX) -> str:
        """Render URL for one frame at `px` square.

        arcsec/pixel scales inversely with `px` so every size frames the same
        field of view: a 128 px thumbnail shows the whole disk, not a crop of
        its middle.
        """
        zoom = IMAGE_PX / px
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
            f"&imageScale={scale * zoom:g}&layers={channel['layer']}"
            f"&x0={x0:.1f}&y0={y0:.1f}&width={px}&height={px}"
            "&display=true&watermark=false"
        )
