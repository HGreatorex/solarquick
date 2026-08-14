"""SOHO image quicklook: EIT EUV full disk + LASCO C2/C3 coronagraphs.

Via Helioviewer (see core/helioviewer.py). Coverage from 1996; EIT runs at
reduced cadence since 2010 (SDO/AIA took over — a few images per day).

Query examples: "soho eit 195 for 2003-10-28", "lasco c3 yesterday",
"coronagraph 2026-08-01".
"""
from __future__ import annotations

import re

from solarapp.core.helioviewer import HelioviewerImageSource, match_wavelength

EIT_WAVELENGTHS = [171, 195, 284, 304]

CHANNELS = {
    f"eit_{wl}": {
        "source_id": sid,
        "layer": f"[SOHO,EIT,EIT,{wl},1,100]",
        "label": f"SOHO EIT {wl} Å",
        "scale": 2.6,  # EIT plate scale 2.63"/px; full disk in 1024 px
    }
    for wl, sid in [(171, 0), (195, 1), (284, 2), (304, 3)]
}
CHANNELS["lasco_c2"] = {
    "source_id": 4, "layer": "[SOHO,LASCO,C2,white-light,1,100]",
    "label": "SOHO LASCO C2", "scale": 12,   # ±6.4 R_sun across 1024 px
}
CHANNELS["lasco_c3"] = {
    "source_id": 5, "layer": "[SOHO,LASCO,C3,white-light,1,100]",
    "label": "SOHO LASCO C3", "scale": 60,   # ±32 R_sun across 1024 px
}


class SohoImages(HelioviewerImageSource):
    name = "soho_images"
    display_name = "SOHO images (EIT / LASCO)"
    keywords = ["soho", "eit", "lasco", "coronagraph", "cme", "euv"]
    channels = CHANNELS
    default_channel = "eit_195"

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        m = re.search(r"\bc([23])\b", ql)
        if m:
            return {"channel": f"lasco_c{m.group(1)}"}, [m.group(0)]
        if "lasco" in ql or "coronagraph" in ql or "cme" in ql:
            return {"channel": "lasco_c2"}, []
        wl = match_wavelength(ql, EIT_WAVELENGTHS)
        if wl is not None:
            return {"channel": f"eit_{wl}"}, [str(wl)]
        return {"channel": "eit_195"}, []
