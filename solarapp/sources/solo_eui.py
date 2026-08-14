"""Solar Orbiter EUI image quicklook via Helioviewer.

FSI (Full Sun Imager) 174/304 Å and HRI (High Resolution Imager) 174/1216 Å.
Coverage from 2021 with a release lag of weeks to months (SOAR public
releases); apparent image scale varies with the spacecraft's distance.

Query examples: "solar orbiter eui yesterday", "eui fsi 304 2026-05-01",
"eui hri 174 for 2026-03-10".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource, match_wavelength

CHANNELS = {
    "fsi_174": {
        "source_id": 84, "layer": "[SOLO,EUI,FSI,174,1,100]",
        "label": "SolO EUI FSI 174 Å", "scale": 7,
    },
    "fsi_304": {
        "source_id": 85, "layer": "[SOLO,EUI,FSI,304,1,100]",
        "label": "SolO EUI FSI 304 Å", "scale": 7,
    },
    "hri_174": {
        "source_id": 86, "layer": "[SOLO,EUI,HRI,174,1,100]",
        "label": "SolO EUI HRI 174 Å", "scale": 1.0,
    },
    "hri_1216": {
        "source_id": 87, "layer": "[SOLO,EUI,HRI,1216,1,100]",
        "label": "SolO EUI HRI Lyman-α", "scale": 1.0,
    },
}


class SoloEui(HelioviewerImageSource):
    name = "solo_eui"
    display_name = "Solar Orbiter EUI (FSI / HRI)"
    keywords = ["eui", "fsi", "hri", "solar orbiter", "solo", "euv"]
    channels = CHANNELS
    default_channel = "fsi_174"

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        det = "hri" if "hri" in ql else "fsi"
        wl = match_wavelength(ql, [174, 304, 1216])
        consumed = [str(wl)] if wl is not None else []
        if "lyman" in ql:
            det, wl = "hri", 1216
        if wl is None:
            wl = 174
        key = f"{det}_{wl}"
        if key not in self.channels:  # e.g. fsi_1216 / hri_304 don't exist
            key = f"{det}_174"
        return {"channel": key}, consumed
