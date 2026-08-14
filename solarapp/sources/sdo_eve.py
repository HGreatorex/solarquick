"""SDO EVE level-2 irradiance from LASP (MEGS spectra and extracted lines).

Two modes, chosen by the query:
- A line is named ("eve he ii 304 yesterday", "eve fe xviii ...", "eve 30.4 nm")
  → that line's irradiance timeseries from the daily EVL (lines) files.
- No line ("eve spectrum yesterday", "sdo eve 2026-08-01")
  → an EVE L2 spectrum: the daily EVS spectral file (0.02 nm bins), averaged
  over the requested interval.

Wavelengths may be given in nm (30.4) or Å (304). Data from the level2b
merged daily files at LASP. Coverage from 2010-05-01. MEGS-A (~6–37 nm) was
lost on 2014-05-26, so short-wavelength lines end there; MEGS-B (~33–106 nm)
continues at a reduced duty cycle.

Query examples: "eve he ii 304 yesterday", "sdo eve spectrum 2026-08-01",
"eve fe xv 2013-05-15".
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np

from solarapp.core.fetchcache import cached_download, listing
from solarapp.core.registry import DataSource, PlotSpec

LASP = "https://lasp.colorado.edu/eve/data_access/eve_data/products/level2b"
MAX_LINE_DAYS = 7
MEGS_A_END = datetime(2014, 5, 26, tzinfo=timezone.utc)
WAVE_TOL_NM = 0.7

_ION_RE = re.compile(
    r"\b(he|h|c|n|o|ne|na|mg|al|si|s|ar|ca|fe|ni)\s+"
    r"(xxi{0,3}|xix|xvi{0,3}|xiv|xv|xi{1,3}|ix|x|vi{0,3}|iv|v|i{1,3})\b"
)
# bare wavelength: needs a decimal point or ≥3 digits; must not touch date
# punctuation, and 4-digit numbers starting 19/20 are read as years
_WAVE_RE = re.compile(r"(?<![-/\d.])(\d{1,3}\.\d+|(?!19|20)\d{3,4}|\d{3})(?![-/\d.])")


class SdoEve(DataSource):
    name = "sdo_eve"
    display_name = "SDO EVE (EUV irradiance: lines & spectra)"
    keywords = ["eve", "sdo eve", "megs", "eve spectrum", "irradiance", "euv"]

    def parse_extras(self, query: str) -> tuple[dict, list[str]]:
        ql = query.lower()
        extras: dict = {}
        consumed: list[str] = []

        m = _ION_RE.search(ql)
        if m:
            extras["line_ion"] = f"{m.group(1).capitalize()} {m.group(2).upper()}"
            consumed.append(m.group(0))

        w = _WAVE_RE.search(ql)
        if w:
            v = float(w.group(1))
            nm = v if v <= 106 else (v / 10 if v / 10 <= 106 else None)
            if nm is not None and nm >= 6:
                extras["line_nm"] = nm
                consumed.append(w.group(1))
        return extras, consumed

    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec:
        if end < datetime(2010, 5, 1, tzinfo=start.tzinfo):
            raise RuntimeError("SDO EVE data starts 2010-05-01.")
        if "line_ion" in extras or "line_nm" in extras:
            return self._fetch_line(start, end, extras.get("line_ion"),
                                    extras.get("line_nm"))
        return self._fetch_spectrum(start, end)

    # -- line timeseries (EVL files) --------------------------------------

    def _fetch_line(self, start, end, ion, nm) -> PlotSpec:
        from astropy.io import fits

        if end - start > timedelta(days=MAX_LINE_DAYS):
            raise RuntimeError(f"EVE line requests are limited to {MAX_LINE_DAYS} days.")

        idx = None
        times: list[str] = []
        values: list[float] = []
        meta = None
        for day in self._days(start, end):
            try:
                path = self._day_file("EVL", day)
            except Exception:
                continue
            with fits.open(path) as h:
                lm = h["LinesMeta"].data
                if idx is None:
                    meta = [(i, float(lm["WAVE_CENTER"][i]), str(lm["NAME"][i]).strip())
                            for i in range(len(lm))]
                    idx = self._resolve_line(meta, ion, nm, start)
                d = h["LinesData"].data
                irr = d["LINE_IRRADIANCE"][:, idx]
                for ydoy, sod, v in zip(d["YYYYDOY"], d["SOD"], irr):
                    if v <= 0:
                        continue
                    t = (datetime(int(ydoy) // 1000, 1, 1, tzinfo=timezone.utc)
                         + timedelta(days=int(ydoy) % 1000 - 1, seconds=float(sod)))
                    if start <= t <= end:
                        times.append(t.isoformat())
                        values.append(float(v))
        if idx is None:
            raise RuntimeError(
                "No EVE EVL files found for this interval "
                "(the LASP archive lags real time by a few days)."
            )
        _, wave, name = meta[idx]
        if not values and wave < 33.3 and end > MEGS_A_END:
            raise RuntimeError(
                f"{name} {wave:.2f} nm is a MEGS-A line — MEGS-A was lost on "
                "2014-05-26. Lines above ~33 nm (MEGS-B) are still observed."
            )
        return {
            "title": f"SDO EVE {name} {wave:.2f} nm  "
                     f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UT",
            "series": [{"name": f"{name} {wave:.2f} nm", "times": times, "values": values}],
            "yaxis": {"label": "Irradiance (W m⁻²)", "type": "log"},
            "xaxis": {"label": "Time (UT)"},
            "provenance": "SDO EVE L2B lines (LASP); MEGS-B observes at reduced duty cycle",
        }

    def _resolve_line(self, meta, ion, nm, start) -> int:
        cands = meta
        if ion:
            cands = [c for c in cands if c[2].lower() == ion.lower()]
        if nm is not None:
            cands = [c for c in cands if abs(c[1] - nm) < WAVE_TOL_NM]
        if not cands:
            hint = ", ".join(f"{n} {w:.1f}" for _, w, n in meta[11:25])
            raise RuntimeError(
                f"No EVE line matching "
                f"{ion or ''} {f'{nm} nm' if nm else ''}".strip()
                + f". Nearby MEGS-B lines include: {hint}, …"
            )
        # prefer MEGS-B lines (still observed) when the request is post-2014
        if len(cands) > 1 and start > MEGS_A_END:
            still_alive = [c for c in cands if c[1] >= 33.3]
            cands = still_alive or cands
        return cands[0][0]

    # -- averaged spectrum (EVS files) ------------------------------------

    def _fetch_spectrum(self, start, end) -> PlotSpec:
        from astropy.io import fits

        mid = start + (end - start) / 2
        day = mid.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            path = self._day_file("EVS", day)
        except Exception:
            raise RuntimeError(
                f"No EVE EVS spectral file for {day:%Y-%m-%d} "
                "(the LASP archive lags real time by a few days)."
            ) from None
        with fits.open(path) as h:
            waves = np.asarray(h["SpectrumMeta"].data["WAVELENGTH"], dtype=float)
            d = h["Spectrum"].data
            sod = np.asarray(d["SOD"], dtype=float)
            irr = np.asarray(d["IRRADIANCE"], dtype=float)

        day_s = (start - day).total_seconds() if start >= day else 0
        day_e = (end - day).total_seconds()
        valid = irr.max(axis=1) > 0
        in_range = valid & (sod >= day_s) & (sod <= day_e)
        rows = in_range if in_range.any() else valid
        note = None if in_range.any() else (
            "No spectra inside the requested hours — averaged the whole day instead."
        )
        if not rows.any():
            raise RuntimeError(f"EVS file for {day:%Y-%m-%d} contains no valid spectra.")

        sel = irr[rows]
        sel[sel <= 0] = np.nan
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(sel, axis=0)
        keep = np.isfinite(mean) & (mean > 0)
        return {
            "title": f"SDO EVE L2 spectrum  {day:%Y-%m-%d} "
                     f"(mean of {int(rows.sum())} × 10 s spectra)",
            "series": [{
                "name": "EVE MEGS",
                "x": waves[keep].round(3).tolist(),
                "values": mean[keep].tolist(),
            }],
            "yaxis": {"label": "Irradiance (W m⁻² nm⁻¹)", "type": "log"},
            "xaxis": {"label": "Wavelength (nm)"},
            "provenance": "SDO EVE L2B spectra (LASP). MEGS-A (6–37 nm) absent "
                          "after 2014-05-26; MEGS-B has a reduced duty cycle.",
            **({"note": note} if note else {}),
        }

    # -- LASP access ------------------------------------------------------

    @staticmethod
    def _days(start, end):
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < end:
            yield day
            day += timedelta(days=1)

    def _day_file(self, kind: str, day: datetime):
        base = f"{LASP}/{day:%Y}/{day:%j}"
        pattern = rf"{kind}_L2B_{day:%Y%j}_\d+_\d+\.fit\.gz"
        names = re.findall(pattern, listing(f"{base}/"))
        if not names:
            raise FileNotFoundError(f"no {kind} file for {day:%Y-%m-%d}")
        name = sorted(set(names))[-1]
        return cached_download(f"{base}/{name}", name)
