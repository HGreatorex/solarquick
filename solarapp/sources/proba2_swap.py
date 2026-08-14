"""PROBA2 SWAP 174 Å EUV imager quicklook via Helioviewer. Coverage from 2010.

Query examples: "swap yesterday", "proba2 swap 2026-08-01".
"""
from __future__ import annotations

from solarapp.core.helioviewer import HelioviewerImageSource

CHANNELS = {
    "swap_174": {
        "source_id": 32, "layer": "[PROBA2,SWAP,SWAP,174,1,100]",
        "label": "PROBA2 SWAP 174 Å",
        "scale": 3.2,  # SWAP 54' FOV (~3240") in 1024 px
    },
}


class Proba2Swap(HelioviewerImageSource):
    name = "proba2_swap"
    display_name = "PROBA2 SWAP (EUV imager)"
    # "proba 2" also matches "proba-2" and "proba2" — the matcher treats
    # internal separators as optional (see registry.keyword_regex)
    keywords = ["swap", "proba 2", "euv"]
    channels = CHANNELS
    default_channel = "swap_174"
