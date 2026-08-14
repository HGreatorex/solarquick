"""SDO image quicklook source (AIA EUV channels + HMI) via the Helioviewer API.

Query examples: "sdo aia 304 yesterday", "aia 171 on 10 July 2026",
"hmi magnetogram 2026-08-01".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource
from solarapp.core.registry import Variable, keyword_regex

AIA_WAVELENGTHS = [94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500]

# Dominant ion (and the nm spelling) per AIA channel: a solar physicist is as
# likely to ask for "aia fe xii" or "19.3 nm" as for "193".
AIA_ALIASES = {
    94: ["9.4 nm", "fe xviii"],
    131: ["13.1 nm", "fe xxi", "fe viii"],
    171: ["17.1 nm", "fe ix"],
    193: ["19.3 nm", "fe xii", "fe xxiv"],
    211: ["21.1 nm", "fe xiv"],
    304: ["30.4 nm", "he ii", "helium ii", "chromosphere"],
    335: ["33.5 nm", "fe xvi"],
    1600: ["160 nm", "c iv", "transition region"],
    1700: ["170 nm", "photosphere uv"],
    4500: ["450 nm", "aia white light"],
}

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
        "solar image", "sun image", "solar dynamics observatory",
        "picture of the sun", "euv", "euv image",
    ]
    coverage = ("2010-05-01", None)
    summary_rank = 30
    summary_extras = {"channel": "aia_193"}

    channels = CHANNELS
    default_channel = "aia_171"
    variables = [
        Variable(f"aia_{wl}", f"AIA {wl} Å", [str(wl), *AIA_ALIASES[wl]])
        for wl in AIA_WAVELENGTHS
    ] + [
        Variable("hmi_magnetogram", "HMI magnetogram",
                 ["magnetogram", "magnetic field", "magnetic", "los magnetogram",
                  "magnetograms"]),
        Variable("hmi_continuum", "HMI continuum (white light)",
                 ["continuum", "intensitygram", "white light", "sunspot",
                  "visible light"]),
    ]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        extras, consumed = self.variable_extras(query)
        if "channel" not in extras:
            # "hmi yesterday" named an instrument that has no EUV channel, so
            # falling through to the AIA default would answer a different
            # question; anything else means AIA's workhorse channel.
            hmi = keyword_regex("hmi").search(query)
            extras["channel"] = "hmi_magnetogram" if hmi else "aia_171"
        return extras, consumed
