"""STEREO-A SECCHI/EUVI image quicklook via Helioviewer.

171/195/284/304 Å full-disk EUV from the STEREO-A vantage point (it drifts
ahead of Earth by ~22°/yr; back near Earth in 2023). Helioviewer carries
EUVI-A from 2006-12 with a few days' latency.

Query examples: "stereo euvi 195 2024-05-14", "stereo-a 304 yesterday".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource
from solarapp.core.registry import Variable

WAVELENGTHS = {171: 20, 195: 21, 284: 22, 304: 23}   # Helioviewer source ids
CHANNELS = {
    f"euvi_{wl}": {
        "source_id": sid,
        "layer": f"[STEREO_A,SECCHI,EUVI,{wl},1,100]",
        "label": f"STEREO-A EUVI {wl} Å",
        "scale": 2.4,
    }
    for wl, sid in WAVELENGTHS.items()
}
ALIASES = {171: ["17.1 nm", "fe ix"], 195: ["19.5 nm", "fe xii"], 284: ["28.4 nm", "fe xv"],
           304: ["30.4 nm", "he ii"]}


class StereoEuvi(HelioviewerImageSource):
    name = "stereo_euvi"
    display_name = "STEREO-A EUVI (171/195/284/304 Å)"
    keywords = ["stereo", "stereo a", "stereo-a", "stereo ahead", "euvi", "secchi"]
    coverage = ("2006-12-04", None)
    channels = CHANNELS
    default_channel = "euvi_195"
    variables = [Variable(f"euvi_{wl}", f"EUVI {wl} Å", [str(wl), *ALIASES[wl]]) for wl in WAVELENGTHS]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        extras, consumed = self.variable_extras(query)
        extras.setdefault("channel", self.default_channel)
        return extras, consumed
