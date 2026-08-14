"""SOHO image quicklook: EIT EUV full disk + LASCO C2/C3 coronagraphs.

Via Helioviewer (see core/helioviewer.py). Coverage from 1996; EIT runs at
reduced cadence since 2010 (SDO/AIA took over — a few images per day).

Query examples: "soho eit 195 for 2003-10-28", "lasco c3 yesterday",
"coronagraph 2026-08-01".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource
from solarapp.core.registry import Variable

EIT_WAVELENGTHS = [171, 195, 284, 304]
EIT_ALIASES = {
    171: ["17.1 nm", "fe ix"],
    195: ["19.5 nm", "fe xii"],
    284: ["28.4 nm", "fe xv"],
    304: ["30.4 nm", "he ii", "helium ii"],
}

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
    keywords = ["soho", "eit", "lasco", "coronagraph", "cme",
                "coronal mass ejection", "white light corona", "euv"]
    coverage = ("1996-01-11", None)
    summary_rank = 40  # a coronagraph filmstrip is the other half of a day
    summary_extras = {"channel": "lasco_c2"}

    channels = CHANNELS
    default_channel = "eit_195"
    variables = [
        Variable("lasco_c2", "LASCO C2 coronagraph (±6 R☉)",
                 ["c2", "lasco", "lasco c2", "coronagraph", "cme",
                  "coronal mass ejection"]),
        Variable("lasco_c3", "LASCO C3 coronagraph (±32 R☉)",
                 ["c3", "lasco c3", "wide field coronagraph"]),
    ] + [
        Variable(f"eit_{wl}", f"EIT {wl} Å", [str(wl), *EIT_ALIASES[wl]])
        for wl in EIT_WAVELENGTHS
    ]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        extras, consumed = self.variable_extras(query)
        extras.setdefault("channel", self.default_channel)
        return extras, consumed
