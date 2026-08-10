"""SDO image quicklook source (AIA EUV channels + HMI) via the Helioviewer API.

Query examples: "sdo aia 304 yesterday", "aia 171 on 10 July 2026",
"hmi magnetogram 2026-08-01".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource, match_wavelength

AIA_WAVELENGTHS = [94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500]

# Helioviewer data source ids: https://api.helioviewer.org/docs/v2/appendix/data_sources.html
CHANNELS = {
    f"aia_{wl}": {
        "source_id": sid,
        "layer": f"[SDO,AIA,AIA,{wl},1,100]",
        "label": f"SDO AIA {wl} Å",
        "scale": 2.4,
    }
    for wl, sid in [(94, 8), (131, 9), (171, 10), (193, 11), (211, 12),
                    (304, 13), (335, 14), (1600, 15), (1700, 16), (4500, 17)]
}
CHANNELS["hmi_continuum"] = {
    "source_id": 18, "layer": "[SDO,HMI,HMI,continuum,1,100]",
    "label": "SDO HMI continuum", "scale": 2.4,
}
CHANNELS["hmi_magnetogram"] = {
    "source_id": 19, "layer": "[SDO,HMI,HMI,magnetogram,1,100]",
    "label": "SDO HMI magnetogram", "scale": 2.4,
}


class SdoImages(HelioviewerImageSource):
    name = "sdo_images"
    display_name = "SDO images (AIA / HMI)"
    keywords = [
        "sdo", "aia", "hmi", "magnetogram", "continuum",
        "solar image", "sun image", "euv image",
    ]
    channels = CHANNELS
    default_channel = "aia_171"

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        if "magnetogram" in ql:
            return {"channel": "hmi_magnetogram"}, []
        if "continuum" in ql or "intensitygram" in ql or "white light" in ql:
            return {"channel": "hmi_continuum"}, []
        wl = match_wavelength(ql, AIA_WAVELENGTHS)
        if wl is not None:
            return {"channel": f"aia_{wl}"}, [str(wl)]
        return {"channel": "aia_171"}, []
