"""Tiny download cache for plugins that fetch files outside sunpy/Fido.

Files land in ~/.cache/solarapp/<name>; a file that already exists is not
re-downloaded. Callers pass a stable name (usually the remote filename).
"""
from __future__ import annotations

from pathlib import Path

from solarapp.core import httpclient

CACHE_DIR = Path.home() / ".cache" / "solarapp"


def cached_download(url: str, name: str, timeout: int = 300) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return path
    r = httpclient.session().get(url, timeout=timeout)
    r.raise_for_status()
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.rename(path)
    return path


def listing(url: str, timeout: int = 60) -> str:
    """Fetch a directory-listing page (HTML) as text, for filename regexes."""
    r = httpclient.session().get(url, timeout=timeout)
    r.raise_for_status()
    return r.text
