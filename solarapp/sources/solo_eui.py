"""Solar Orbiter EUI image quicklook via Helioviewer.

FSI (Full Sun Imager) 174/304 Å and HRI (High Resolution Imager) 174/1216 Å.
Coverage from 2021 with a release lag of weeks to months (SOAR public
releases); apparent image scale varies with the spacecraft's distance.

Query examples: "solar orbiter eui yesterday", "eui fsi 304 2026-05-01",
"eui hri 174 for 2026-03-10".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource
from solarapp.core.registry import Variable

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
    keywords = ["eui", "fsi", "hri", "solar orbiter", "solo",
                "full sun imager", "high resolution imager", "euv"]
    coverage = ("2020-05-30", None)
    channels = CHANNELS
    default_channel = "fsi_174"

    # Detector and wavelength are separate dimensions here, and not every
    # combination exists — parse_extras reconciles them below.
    variables = [
        Variable("fsi", "FSI full-sun imager", ["fsi", "full sun imager"],
                 extras={"detector": "fsi"}, group="detector"),
        Variable("hri", "HRI high-resolution imager",
                 ["hri", "high resolution imager"],
                 extras={"detector": "hri"}, group="detector"),
        Variable("174", "174 Å", ["174", "17.4 nm", "fe ix", "fe x"],
                 extras={"wavelength": 174}, group="wavelength"),
        Variable("304", "304 Å", ["304", "30.4 nm", "he ii"],
                 extras={"wavelength": 304}, group="wavelength"),
        Variable("1216", "Lyman-α 1216 Å",
                 ["1216", "121.6 nm", "lyman alpha", "lyman"],
                 extras={"wavelength": 1216}, group="wavelength"),
    ]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        picked, consumed = self.variable_extras(query)
        wl = picked.get("wavelength")
        det = picked.get("detector", "hri" if wl == 1216 else "fsi")
        key = f"{det}_{wl or 174}"
        if key not in self.channels:
            # fsi_1216 and hri_304 don't exist: Lyman-α is an HRI channel,
            # and anything else falls back to the detector's 174 Å.
            key = "hri_1216" if wl == 1216 else f"{det}_174"
        return {"channel": key}, consumed
