"""STATS page: flare statistics from NOAA SWPC's daily archives.

Data (NCEI copies of SWPC daily products, one file per day, cached on disk):
- Edited Events (`YYYYMMDDevents.txt`, 1996-07-31–): every GOES XRA event with its
  class, satellite and region. The same list HEK serves, without HEK's latency.
- RSGA (`YYYYMMDDRSGA.txt`): section III gives M/X probabilities for three
  *named* target days, so forecast and outcome line up from the file itself.
  The last day or so not yet archived comes from solar_probabilities.json,
  whose `date` is the issue date (checked against RSGA 2026-11-05..18).
- Solar Region Summary: the regions on the disk each day, for rates per
  McIntosh / magnetic class.

Conventions (each stated in the panel it affects):
- Classes are SWPC's published operational classes. Pre-GOES-R satellites
  (GOES-15 and earlier) carry the 0.7 XRS-B scaling; `rescale=True` divides
  those fluxes by 0.7 to put every era on the GOES-R (true-flux) scale.
  Forecast verification always uses published classes: that is what the
  forecasts were issued against.
- An event belongs to the UT day of its *peak*.
- "Class M" probabilities are verified against ≥ M1.0 (M or X) in the target day.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

import numpy as np

from solarapp.core import httpclient
from solarapp.core.fetchcache import CACHE_DIR
from solarapp.sources import solar_regions as sr

DAILY = "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/"
URLS = {
    "events": DAILY + "solar_event_reports/{d:%Y}/{d:%m}/{d:%Y%m%d}events.txt",
    "rsga": DAILY + "reports_solar_geophysical_activity/{d:%Y}/{d:%m}/{d:%Y%m%d}RSGA.txt",
    "srs": sr.ARCHIVE_URL,
}
SWPC = "https://services.swpc.noaa.gov"
FIRST_DAY = date(1996, 7, 31)     # first archived Edited Events file
MAX_DAYS = 4018          # 11 years: one solar cycle
WORKERS = 8
RETRIES = 2
SETTLED = 4              # days after which an archive file is final
MISSING_IS_FINAL = 30    # a 404 this old is a real gap, not lag
OLD_SCALE = 0.7          # GOES 8–15 XRS-B operational scaling
LETTERS = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}

Progress = Callable[[str, int, int], None]


# -- the daily-file cache -------------------------------------------------


def _cache_path(kind: str, day: date) -> Path:
    return CACHE_DIR / "swpc_daily" / kind / f"{day:%Y%m%d}.txt"


def _get_text(url: str) -> str | None:
    r = httpclient.session().get(url, timeout=(5, 15))   # NGDC sometimes hangs; don't wait 30 s
    if r.status_code == 404:
        return None
    r.raise_for_status()
    if not r.text.lstrip().startswith(":Product:"):
        # NGDC answers 200 with an HTML page under load; that is not "no file"
        raise ValueError("archive returned something that is not a SWPC product")
    return r.text


def daily_text(kind: str, day: date, today: date | None = None) -> str | None:
    """The archived product for `day` (None = not published), cached on disk.

    Files older than SETTLED days are final. A 404 older than
    MISSING_IS_FINAL days is remembered as a gap; younger ones are retried.
    Raises when the archive could not be read (reported, never read as a gap).
    """
    today = today or datetime.now(timezone.utc).date()
    path = _cache_path(kind, day)
    gap = path.with_suffix(".404")
    age = (today - day).days
    if path.exists() and (age > SETTLED or time.time() - path.stat().st_mtime < 3600):
        return path.read_text()
    if gap.exists():
        return None
    url = URLS[kind].format(d=day + timedelta(days=1) if kind == "srs" else day)
    failure = None
    for attempt in range(RETRIES):
        try:
            text = _get_text(url)
            break
        except Exception as e:  # noqa: BLE001 — retried, then reported
            failure = e
            time.sleep(0.4 * (attempt + 1))
    else:
        if path.exists():               # a stale copy beats no copy
            return path.read_text()
        raise RuntimeError(f"{kind} {day}: {failure}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        if age > MISSING_IS_FINAL:
            gap.write_text("")
        return None
    tmp = path.with_suffix(".part")
    tmp.write_text(text)
    tmp.rename(path)
    return text


def load_days(kind: str, days: list[date], progress: Progress | None = None
              ) -> tuple[dict[date, str], list[date], list[date]]:
    """{day: text}, days with no file, days that could not be read."""
    texts, absent, failed = {}, [], []
    done = 0

    def one(d):
        try:
            return d, daily_text(kind, d), None
        except Exception as e:  # noqa: BLE001
            return d, None, e

    with ThreadPoolExecutor(WORKERS) as pool:
        for d, text, err in pool.map(one, days):
            done += 1
            if err is not None:
                failed.append(d)
            elif text is None:
                absent.append(d)
            else:
                texts[d] = text
            if progress and (done % 20 == 0 or done == len(days)):
                progress(kind, done, len(days))
    return texts, sorted(absent), sorted(failed)


def day_range(start: date, end: date) -> list[date]:
    start = max(start, FIRST_DAY)
    if (end - start).days + 1 > MAX_DAYS:
        raise ValueError(f"at most {MAX_DAYS} days (one solar cycle) per run")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# -- flares -----------------------------------------------------------------


@dataclass
class Event:
    begin: datetime
    peak: datetime
    end: datetime | None
    cls: str              # as published, e.g. "M1.2"
    sat: int | None       # GOES number from the Obs column (G15 → 15)
    region: int | None    # four digits, as SWPC prints it
    source: str = "events"

    @property
    def flux(self) -> float:
        return class_flux(self.cls)

    def flux_on(self, rescale: bool) -> float:
        """Peak flux, optionally on the GOES-R scale (÷0.7 for GOES ≤ 15)."""
        f = self.flux
        return f / OLD_SCALE if rescale and self.sat is not None and self.sat <= 15 else f


def class_flux(cls: str) -> float:
    """"M1.2" → 1.2e-5 W m⁻²."""
    return LETTERS[cls[0].upper()] * float(cls[1:])


def flux_label(flux: float) -> str:
    for letter in "XMCBA":
        if flux >= LETTERS[letter] or letter == "A":
            return f"{letter}{flux / LETTERS[letter]:.1f}"
    return "?"


_CLASS = re.compile(r"^[ABCMX]\d+(\.\d*)?\+?$")
_HHMM = re.compile(r"(\d{4})")


def _clock(day: date, token: str) -> datetime | None:
    m = _HHMM.search(token)          # "B0012", "A2359", "U1015" → the clock
    if not m:
        return None
    hh, mm = int(m.group(1)[:2]), int(m.group(1)[2:])
    if hh > 23 or mm > 59:
        return None
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)


def parse_events(text: str, day: date) -> list[Event]:
    """GOES 1–8 Å XRA events from one Edited Events file (one per event number)."""
    out, seen = [], set()
    for line in text.splitlines():
        if line.startswith((":", "#")) or " XRA " not in f" {line} ":
            continue
        tok = line.split()
        if tok[1] == "+":
            tok = [tok[0]] + tok[2:]
        # num begin max end obs q type loc/frq class intflux [reg]
        if len(tok) < 9 or tok[6] != "XRA" or tok[7] != "1-8A" or not _CLASS.match(tok[8]):
            continue
        cls = tok[8].rstrip("+")          # "X28+": saturated, the value is a lower bound
        cls = cls[0] + f"{float(cls[1:]):.1f}"
        if tok[0] in seen:
            continue
        begin = _clock(day, tok[1])
        peak = _clock(day, tok[2]) or begin
        end = _clock(day, tok[3])
        if begin is None or peak is None:
            continue
        if peak < begin:
            peak += timedelta(days=1)
        if end is not None and end < begin:
            end += timedelta(days=1)
        sat = int(tok[4][1:]) if re.fullmatch(r"G\d{1,2}", tok[4]) else None
        # integrated flux (1.5E-03 or ////) may be absent; the region is the bare integer after it
        region = next((int(t) for t in tok[9:] if t.isdigit()), None)
        seen.add(tok[0])
        out.append(Event(begin, peak, end, cls, sat, region))
    return out


def _recent_events(days: set[date]) -> list[Event]:
    """Days the archive does not have yet, from SWPC's 7-day realtime list."""
    rows = httpclient.get_json_cached(SWPC + "/json/goes/primary/xray-flares-7-day.json", ttl=300)
    out = []
    for r in rows:
        if not r.get("max_time") or not r.get("max_class") or not _CLASS.match(r["max_class"]):
            continue
        t = lambda k: datetime.fromisoformat(r[k].replace("Z", "+00:00")) if r.get(k) else None  # noqa: E731
        peak = t("max_time")
        if peak.date() not in days:
            continue
        out.append(Event(t("begin_time") or peak, peak, t("end_time"), r["max_class"],
                         r.get("satellite"), None, "realtime"))
    return out


@dataclass
class Catalogue:
    events: list[Event]
    days: list[date]             # the window
    covered: set[date]           # days whose events are complete (file read, or realtime)
    absent: list[date]
    failed: list[date]
    realtime_days: list[date]

    def note(self) -> str | None:
        bits = []
        if self.absent:
            bits.append(f"{len(self.absent)} day(s) with no archived event list")
        if self.failed:
            bits.append(f"{len(self.failed)} day(s) the archive did not serve (try again)")
        if self.realtime_days:
            bits.append(f"{len(self.realtime_days)} recent day(s) from SWPC's realtime list "
                        f"(no region numbers)")
        return "; ".join(bits) or None


def catalogue(start: date, end: date, progress: Progress | None = None,
              today: date | None = None) -> Catalogue:
    today = today or datetime.now(timezone.utc).date()
    days = day_range(start, end)
    # a file holds events by *begin* day, so the day before can hold a peak in the window
    want = [days[0] - timedelta(days=1)] + days if days[0] > FIRST_DAY else days
    texts, absent, failed = load_days("events", want, progress)
    events = [e for d, t in texts.items() for e in parse_events(t, d)]
    covered = {d for d in days if d in texts}
    missing = {d for d in days if d not in texts and (today - d).days <= 6}
    realtime_days = []
    if missing:
        try:
            recent = _recent_events(missing)
            events += recent
            realtime_days = sorted(missing)
            covered |= missing
        except Exception:  # noqa: BLE001 — the archive part still stands
            pass
    window = set(days)
    events = sorted((e for e in events if e.peak.date() in window), key=lambda e: e.peak)
    return Catalogue(events, days, covered,
                     [d for d in absent if d in window and d not in covered],
                     [d for d in failed if d in window and d not in covered], realtime_days)


# -- S1: rates ----------------------------------------------------------------


def rates(cat: Catalogue, floor: float, rescale: bool, ssn: list[dict] | None) -> dict:
    months = sorted({(d.year, d.month) for d in cat.days})
    key = lambda y, m: f"{y:04d}-{m:02d}"  # noqa: E731
    counts = {k: Counter() for k in "BCMX"}
    for e in cat.events:
        f = e.flux_on(rescale)
        if f >= floor:
            counts[flux_label(f)[0]][key(e.peak.year, e.peak.month)] += 1
    days_in = Counter(key(d.year, d.month) for d in cat.days)
    cov = Counter(key(d.year, d.month) for d in cat.covered)
    labels = [key(y, m) for y, m in months]
    letters = [k for k in "BCMX" if LETTERS[k] * 10 > floor]
    ssn_by = {r["time-tag"]: r for r in (ssn or [])}

    def pick(month, field):            # the feed marks "no value" with -1
        v = ssn_by.get(month, {}).get(field)
        return v if v is not None and v >= 0 else None
    return {
        "months": labels,
        "counts": {k: [counts[k][m] for m in labels] for k in letters},
        "coverage": [round(cov[m] / days_in[m], 3) for m in labels],
        "ssn": [pick(m, "ssn") for m in labels],
        "f107": [pick(m, "f10.7") for m in labels],
        "note": ("Months with coverage < 1 are partly outside the window or have "
                 "days missing from the archive; counts there are not comparable."),
    }


# -- S2: frequency distribution, power law (Clauset, Shalizi & Newman 2009) --


def _dequantise(cls: list[str], scale: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Classes are published to 0.1 in magnitude: spread each uniformly over its
    ±0.05 bin, so the continuous MLE is not biased by ties at x_min."""
    mag = np.array([float(c[1:]) for c in cls])
    dec = np.array([LETTERS[c[0]] for c in cls])
    return (mag + rng.uniform(-0.05, 0.05, mag.size)) * dec * scale


def _fit_tail(xs: np.ndarray, logs: np.ndarray, i: int) -> tuple[float, float, int]:
    """(alpha, KS distance, n_tail) for the tail xs[i:] of a sorted array; logs = log(xs)."""
    t = xs[i:]
    n = t.size
    alpha = 1.0 + n / (logs[i:].sum() - n * logs[i])
    model = 1.0 - (t / t[0]) ** (1.0 - alpha)
    k = np.arange(n)
    d = max(np.max(np.abs((k + 1) / n - model)), np.max(np.abs(k / n - model)))
    return float(alpha), float(d), int(n)


def fit_power_law(x: np.ndarray, min_tail: int = 50, grid: int = 120) -> dict:
    """x_min by KS minimisation over candidate values (a quantile grid), then MLE."""
    xs = np.sort(np.asarray(x, float))
    if xs.size < min_tail:
        raise ValueError(f"need at least {min_tail} flares above the floor, have {xs.size}")
    logs = np.log(xs)
    top = xs.size - min_tail
    cands = np.unique(np.linspace(0, top, min(grid, top + 1)).astype(int))
    best = None
    for i in cands:
        a, d, n = _fit_tail(xs, logs, i)
        if best is None or d < best[1]:
            best = (a, d, n, xs[i])
    a, d, n, xm = best
    return {"alpha": a, "ks": d, "n_tail": n, "xmin": float(xm), "sigma": (a - 1) / math.sqrt(n)}


def _sample_power_law(alpha: float, xmin: float, n: int, rng) -> np.ndarray:
    return xmin * (1 - rng.uniform(size=n)) ** (-1 / (alpha - 1))


def distribution(cat: Catalogue, floor: float, rescale: bool, boots: int = 200,
                 gof_sims: int = 100, seed: int = 1) -> dict:
    rng = np.random.default_rng(seed)
    ev = [e for e in cat.events if e.flux_on(rescale) >= floor]
    if not ev:
        raise ValueError("no flares above the floor in this window")
    cls = [e.cls.upper() for e in ev]
    scale = np.array([1 / OLD_SCALE if rescale and e.sat is not None and e.sat <= 15 else 1.0 for e in ev])
    x = _dequantise(cls, scale, rng)
    fit = fit_power_law(x)

    # uncertainty: nonparametric bootstrap, x_min re-chosen each time
    a_bs, xm_bs = [], []
    for _ in range(boots):
        try:
            f = fit_power_law(rng.choice(x, x.size), grid=60)
            a_bs.append(f["alpha"]); xm_bs.append(f["xmin"])
        except ValueError:
            pass
    # goodness of fit: semi-parametric bootstrap p (Clauset et al. §4.1)
    body = x[x < fit["xmin"]]
    p_tail = fit["n_tail"] / x.size
    worse = 0
    for _ in range(gof_sims):
        k = rng.binomial(x.size, p_tail)
        synth = np.concatenate([_sample_power_law(fit["alpha"], fit["xmin"], k, rng),
                                rng.choice(body, x.size - k) if body.size else np.empty(0)])
        try:
            worse += fit_power_law(synth, grid=60)["ks"] >= fit["ks"]
        except ValueError:
            pass

    edges = 10 ** np.arange(math.floor(math.log10(x.min()) * 5) / 5,
                            math.log10(x.max()) + 0.2, 0.2)    # 5 bins per decade
    counts, _ = np.histogram(x, edges)
    density = counts / (x.size * np.diff(edges))
    xs = np.sort(x)
    ccdf = 1.0 - np.arange(xs.size) / xs.size
    step = max(1, xs.size // 1500)
    grid = np.logspace(math.log10(fit["xmin"]), math.log10(xs[-1]), 50)
    return {
        "n": int(x.size), **fit,
        "alpha_boot_sd": float(np.std(a_bs)) if a_bs else None,
        "xmin_boot_sd": float(np.std(xm_bs)) if xm_bs else None,
        "gof_p": worse / gof_sims if gof_sims else None,
        "xmin_class": flux_label(fit["xmin"]),
        "hist": {"edges": edges.tolist(), "counts": counts.tolist(), "density": density.tolist()},
        "ccdf": {"x": xs[::step].tolist(), "p": ccdf[::step].tolist()},
        "model": {"x": grid.tolist(),
                  "density": (p_tail * (fit["alpha"] - 1) / fit["xmin"]
                              * (grid / fit["xmin"]) ** -fit["alpha"]).tolist(),
                  "ccdf": (p_tail * (grid / fit["xmin"]) ** (1 - fit["alpha"])).tolist()},
        "note": ("Peak fluxes as catalogued (not background-subtracted). Below x_min the "
                 "list is incomplete — small flares are missed when the background is "
                 "high — so only the tail is fitted. Published classes are quantised to "
                 "0.1, spread uniformly within ±0.05 before fitting. gof_p > 0.1: a power "
                 "law is a plausible description of the tail."),
    }


# -- S4: forecast verification -------------------------------------------------

_PROB = re.compile(
    r"III\.\s+Event probabilities\s+(\d{1,2})\s+([A-Za-z]{3})\S*\s*-\s*(\d{1,2})\s+([A-Za-z]{3})\S*"
    r".*?Class M\s+(\d+)/(\d+)/(\d+).*?Class X\s+(\d+)/(\d+)/(\d+)", re.S | re.I)
_ISSUED = re.compile(r":Issued:\s*(\d{4})\s+([A-Za-z]{3})\s+(\d{1,2})", re.I)
_MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def parse_rsga(text: str) -> list[tuple[date, date, int, int, int]]:
    """[(issued, target, lead, pM, pX)] — targets read from the file's own dates."""
    mi, m = _ISSUED.search(text), _PROB.search(text)
    if not mi or not m:
        return []
    issued = date(int(mi.group(1)), _MON[mi.group(2).lower()], int(mi.group(3)))
    first_day, first_mon = int(m.group(1)), _MON[m.group(2).lower()[:3]]
    year = issued.year + (1 if first_mon < issued.month else 0)   # Dec 31 → Jan 1
    first = date(year, first_mon, first_day)
    pm, px = [int(m.group(i)) for i in (5, 6, 7)], [int(m.group(i)) for i in (8, 9, 10)]
    return [(issued, first + timedelta(days=k), k + 1, pm[k], px[k]) for k in range(3)]


def forecasts(days: list[date], progress: Progress | None = None
              ) -> tuple[dict[tuple[date, int], tuple[int, int]], str | None]:
    """{(target, lead): (pM, pX)} for targets in `days`; issue days start 3 before."""
    issue_days = [days[0] - timedelta(days=k) for k in (3, 2, 1)] + days
    texts, absent, failed = load_days("rsga", [d for d in issue_days if d >= FIRST_DAY], progress)
    out = {}
    for t in texts.values():
        for _, target, lead, pm, px in parse_rsga(t):
            out[(target, lead)] = (pm, px)
    filled = 0
    try:   # issues not archived yet: the JSON's date is the issue date
        for r in httpclient.get_json_cached(SWPC + "/json/solar_probabilities.json", ttl=3600):
            issued = date.fromisoformat(r["date"][:10])
            if issued in texts:
                continue
            for lead in (1, 2, 3):
                k = (issued + timedelta(days=lead), lead)
                if k not in out:
                    out[k] = (r[f"m_class_{lead}_day"], r[f"x_class_{lead}_day"]); filled += 1
    except Exception:  # noqa: BLE001
        pass
    note = f"{len(failed)} forecast issue(s) the archive did not serve" if failed else None
    return out, note


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, c - h), min(1.0, c + h))


def verify(p: np.ndarray, o: np.ndarray) -> dict:
    """Probabilistic scores for forecasts p ∈ [0,1] against outcomes o ∈ {0,1}."""
    n = p.size
    base = float(o.mean())
    bs = float(np.mean((p - o) ** 2))
    unc = base * (1 - base)
    # Murphy decomposition over the distinct forecast values (RSGA issues integers)
    rel = res = 0.0
    for v in np.unique(p):
        sel = p == v
        ok = o[sel].mean()
        rel += sel.sum() * (v - ok) ** 2
        res += sel.sum() * (ok - base) ** 2
    rel, res = rel / n, res / n
    bins = []
    edges = np.linspace(0, 1, 11)
    for i in range(10):
        sel = (p >= edges[i]) & ((p < edges[i + 1]) if i < 9 else (p <= 1))
        k, m = int(o[sel].sum()), int(sel.sum())
        lo, hi = _wilson(k, m)
        bins.append({"lo": edges[i], "hi": edges[i + 1], "n": m,
                     "mean_p": float(p[sel].mean()) if m else None,
                     "freq": k / m if m else None, "ci": [lo, hi] if m else None})
    roc = [(0.0, 0.0, None)]
    best = (-1.0, None)
    for t in sorted(np.unique(p), reverse=True):
        yes = p >= t
        hits, fa = int((yes & (o == 1)).sum()), int((yes & (o == 0)).sum())
        pos, neg = int(o.sum()), int(n - o.sum())
        pod = hits / pos if pos else math.nan
        pofd = fa / neg if neg else math.nan
        roc.append((pofd, pod, float(t)))
        if pos and neg and pod - pofd > best[0]:
            best = (pod - pofd, float(t))
    roc.append((1.0, 1.0, None))
    xs, ys = [r[0] for r in roc], [r[1] for r in roc]
    auc = float(getattr(np, "trapezoid", getattr(np, "trapz", None))(ys, xs)) if not any(map(math.isnan, xs + ys)) else None
    return {"n": n, "events": int(o.sum()), "base_rate": base, "brier": bs,
            "brier_clim": unc, "bss": 1 - bs / unc if unc > 0 else None,
            "reliability": rel, "resolution": res, "uncertainty": unc,
            "bins": bins, "roc": [{"pofd": a, "pod": b, "threshold": c} for a, b, c in roc],
            "auc": auc, "tss_max": best[0] if best[1] is not None else None,
            "tss_threshold": best[1]}


def verification(cat: Catalogue, probs: dict, lead: int, cls: str) -> dict:
    floor = LETTERS[cls]
    flaring = {e.peak.date() for e in cat.events if e.flux >= floor}   # published classes
    days = [d for d in cat.days if d in cat.covered and (d, lead) in probs]
    if len(days) < 10:
        raise ValueError(f"only {len(days)} day(s) with both a day-{lead} forecast and a complete event list")
    col = 0 if cls == "M" else 1
    p = np.array([probs[(d, lead)][col] / 100 for d in days])
    o = np.array([1 if d in flaring else 0 for d in days])
    out = verify(p, o)
    # persistence: "a flare of this class yesterday" → yes (needs yesterday covered)
    pers = [(1.0 if d - timedelta(days=1) in flaring else 0.0, o_)
            for d, o_ in zip(days, o) if d - timedelta(days=1) in cat.covered]
    if pers:
        pp, po = np.array([a for a, _ in pers]), np.array([b for _, b in pers])
        pos, neg = po.sum(), po.size - po.sum()
        out["persistence"] = {
            "n": int(po.size), "brier": float(np.mean((pp - po) ** 2)),
            "pod": float(((pp == 1) & (po == 1)).sum() / pos) if pos else None,
            "pofd": float(((pp == 1) & (po == 0)).sum() / neg) if neg else None}
    out.update({"lead": lead, "class": cls,
                "series": {"days": [d.isoformat() for d in days], "p": p.tolist(), "o": o.tolist()},
                "note": (f"NOAA day-{lead} '{cls}-class' probability vs ≥1 flare ≥ {cls}1.0 "
                         "(published class) peaking in that UT day. Climatology is the window's "
                         "own base rate. Persistence: 'yes if one happened the day before'.")})
    return out


# -- S5: regions ----------------------------------------------------------------


def _magnitude_sum(evs: Iterable[Event], letter: str) -> float:
    return sum(float(e.cls[1:]) for e in evs if e.cls[0].upper() == letter)


def flare_index(evs: list[Event]) -> float:
    """100·ΣX + 10·ΣM + ΣC + 0.1·ΣB (class magnitudes, published classes)."""
    return (100 * _magnitude_sum(evs, "X") + 10 * _magnitude_sum(evs, "M")
            + _magnitude_sum(evs, "C") + 0.1 * _magnitude_sum(evs, "B"))


def regions(cat: Catalogue, srs: dict[date, str] | None, top: int = 25) -> dict:
    by = defaultdict(list)
    for e in cat.events:
        if e.region:
            by[e.region % 10000].append(e)
    # SRS valid at D 2400 UT → the regions on the disk during D+1
    disk: dict[int, list[sr.Region]] = defaultdict(list)
    day_regions: dict[date, list[sr.Region]] = {}
    for d, text in (srs or {}).items():
        try:
            snap = sr.parse_srs(text)
        except Exception:  # noqa: BLE001
            continue
        spots = snap.section(sr.SPOTS)
        day_regions[snap.valid + timedelta(days=1)] = spots
        for r in spots:
            n = sr._ar_number(r.nmbr)
            if n is not None:
                disk[n].append(r)

    table = []
    for ar, evs in by.items():
        snaps = disk.get(ar, [])
        biggest = max(snaps, key=lambda r: r.area or 0, default=None)
        top_ev = max(evs, key=lambda e: e.flux)
        table.append({
            "region": ar, "fi": round(flare_index(evs), 1),
            "n": {k: sum(1 for e in evs if e.cls[0].upper() == k) for k in "CMX"},
            "largest": top_ev.cls, "largest_peak": top_ev.peak.isoformat(),
            "first": min(e.peak for e in evs).date().isoformat(),
            "last": max(e.peak for e in evs).date().isoformat(),
            "max_area": biggest.area if biggest else None,
            "mcintosh_at_max": biggest.mcintosh if biggest else None,
            "delta": any(r.delta for r in snaps) if snaps else None,
            "mag_max": max((r.complexity for r in snaps),
                           key=["alpha", "beta", "beta-gamma", "delta"].index, default=None),
            "days_on_disk": len(snaps) or None,
            "query": (f"ar {ar} from {min(e.peak for e in evs).date() - timedelta(days=3)} "
                      f"to {max(e.peak for e in evs).date() + timedelta(days=3)}"),
        })
    table.sort(key=lambda r: -r["fi"])

    out = {"table": table[:top], "n_regions": len(table), "unassigned": sum(1 for e in cat.events if not e.region)}
    if day_regions:
        out["rates"] = region_day_rates(cat, day_regions)
    return out


def region_day_rates(cat: Catalogue, day_regions: dict[date, list[sr.Region]]) -> dict:
    """Flares per region-day by class of region (the region as the SRS saw it
    at the start of that day), for C, M and X — the McIntosh-style rate table."""
    flares = defaultdict(lambda: Counter())          # (day, ar) → Counter of letters ≥ C
    for e in cat.events:
        if e.region and e.cls[0].upper() in "CMX":
            for k in "CMX"[: "CMX".index(e.cls[0].upper()) + 1]:  # an X counts as ≥C, ≥M, ≥X
                flares[(e.peak.date(), e.region % 10000)][k] += 1
    groups = {"zurich": defaultdict(list), "mcintosh": defaultdict(list), "mag": defaultdict(list)}
    for d, regs in day_regions.items():
        if d not in cat.covered:
            continue
        for r in regs:
            ar = sr._ar_number(r.nmbr)
            c = flares.get((d, ar), Counter())
            row = (c["C"], c["M"], c["X"])
            if r.mcintosh:
                groups["zurich"][r.mcintosh[0].upper()].append(row)
                groups["mcintosh"][r.mcintosh[:3]].append(row)
            groups["mag"][r.complexity].append(row)

    def summarise(rows):
        a = np.array(rows)
        n = a.shape[0]
        out = {"region_days": n}
        for i, k in enumerate("CMX"):
            lam = float(a[:, i].mean())
            hit = int((a[:, i] > 0).sum())
            out[k] = {"rate": lam, "poisson_p": 1 - math.exp(-lam), "frac": hit / n,
                      "ci": list(_wilson(hit, n))}
        return out

    mag_order = ["alpha", "beta", "beta-gamma", "delta"]
    sort_key = {
        "zurich": lambda g: "AHBCDEF".find(g["group"][0]),
        "mcintosh": lambda g: -g["M"]["rate"],
        "mag": lambda g: mag_order.index(g["group"]) if g["group"] in mag_order else 9,
    }
    res = {}
    for g, d in groups.items():
        rows = [{"group": k, **summarise(v)} for k, v in d.items()
                if len(v) >= (10 if g == "mcintosh" else 1)]
        res[g] = sorted(rows, key=sort_key[g])
    res["note"] = ("Each row: region-days in that class (from the Solar Region Summary at the "
                   "start of the UT day) and the flares those regions produced that day. "
                   "rate = flares per region-day; poisson_p = 1−e^(−rate), the chance of ≥1. "
                   "McIntosh classes with fewer than 10 region-days are left out.")
    return res


# -- one run --------------------------------------------------------------------

PANELS = ("rates", "dist", "verify", "regions")


def run(start: date, end: date, floor_cls: str = "C1.0", panels: Iterable[str] = PANELS,
        rescale: bool = False, lead: int = 1, verify_cls: str = "M",
        progress: Progress | None = None) -> dict:
    panels = [p for p in PANELS if p in set(panels)]
    floor = class_flux(floor_cls.upper())
    if lead not in (1, 2, 3) or verify_cls not in ("M", "X"):
        raise ValueError("lead must be 1–3 and class M or X")
    cat = catalogue(start, end, progress)
    out = {"start": cat.days[0].isoformat(), "end": cat.days[-1].isoformat(),
           "n_events": len(cat.events), "coverage_note": cat.note(),
           "floor": floor_cls.upper(), "rescale": rescale, "panels": {}}
    sats = {e.sat for e in cat.events if e.sat is not None}
    if any(s <= 15 for s in sats) and any(s >= 16 for s in sats) and not rescale:
        out["era_note"] = ("This window mixes GOES-15-and-earlier and GOES-R classes; the older "
                           "ones carry NOAA's 0.7 scaling. Tick 'rescale' to put them on one scale.")

    def guard(fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — one panel's failure is that panel's
            return {"error": str(e) or type(e).__name__}

    if "rates" in panels:
        def _rates():
            try:
                ssn = httpclient.get_json_cached(SWPC + "/json/solar-cycle/observed-solar-cycle-indices.json", ttl=86400)
            except Exception:  # noqa: BLE001
                ssn = None
            return rates(cat, floor, rescale, ssn)
        out["panels"]["rates"] = guard(_rates)
    if "dist" in panels:
        out["panels"]["dist"] = guard(lambda: distribution(cat, floor, rescale))
    if "verify" in panels:
        def _verify():
            probs, note = forecasts(cat.days, progress)
            v = verification(cat, probs, lead, verify_cls)
            if note:
                v["note"] += " " + note + "."
            return v
        out["panels"]["verify"] = guard(_verify)
    if "regions" in panels:
        def _regions():
            texts, _, failed = load_days("srs", [d - timedelta(days=1) for d in cat.days], progress)
            r = regions(cat, texts)
            if failed:
                r["note_srs"] = f"{len(failed)} region summary day(s) the archive did not serve"
            return r
        out["panels"]["regions"] = guard(_regions)
    return out


def stream(**kw) -> Iterator[dict]:
    """run() as NDJSON-ready progress events."""
    return _stream(run, kw)


# -- S3: superposed epoch ---------------------------------------------------------

EPOCH_MAX = 200
CHANNELS = {"xrsb": ("goes_xrs", {}, 1, "GOES XRS-B 1–8 Å"),
            "xrsa": ("goes_xrs", {}, 0, "GOES XRS-A 0.5–4 Å"),
            "lya": ("goes_euvs", {"channel": "irr_1216"}, 0, "GOES EUVS Lyman-α 121.6 nm")}
MAX_GAP_S = 180


def select_flares(cat: Catalogue, class_min: str, class_max: str | None, region: int | None,
                  before_min: int, after_min: int, limit: int, exclude_overlaps: bool
                  ) -> tuple[list[Event], dict]:
    lo = class_flux(class_min)
    hi = class_flux(class_max) if class_max else math.inf
    pool = [e for e in cat.events if lo <= e.flux <= hi and (region is None or (e.region or 0) % 10000 == region % 10000)]
    dropped = 0
    if exclude_overlaps:
        keep = []
        for e in pool:
            w0, w1 = e.peak - timedelta(minutes=before_min), e.peak + timedelta(minutes=after_min)
            clash = any(o is not e and w0 <= o.peak <= w1 and o.flux >= 0.5 * e.flux for o in cat.events)
            dropped += clash
            if not clash:
                keep.append(e)
        pool = keep
    chosen = sorted(pool, key=lambda e: -e.flux)[:limit]
    return chosen, {"matched": len(pool) + dropped, "overlaps_dropped": dropped,
                    "used": len(chosen), "largest_first": len(pool) > limit}


def _curve(channel: str, t0: datetime, t1: datetime, fetch) -> tuple[np.ndarray, np.ndarray]:
    source, extras, idx, _ = CHANNELS[channel]
    spec = fetch(source, t0, t1, extras)
    s = spec["series"][idx] if channel != "lya" else spec["series"][0]
    t = np.array([datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() for x in s["times"]])
    v = np.array(s["values"], float)
    ok = np.isfinite(v) & (v > 0)
    return t[ok], v[ok]


def _on_grid(t: np.ndarray, v: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Interpolate onto grid seconds; NaN outside the data or across gaps."""
    out = np.full(grid.size, np.nan)
    if t.size < 2:
        return out
    order = np.argsort(t)
    t, v = t[order], v[order]
    y = np.interp(grid, t, v, left=np.nan, right=np.nan)
    j = np.clip(np.searchsorted(t, grid), 1, t.size - 1)
    gap = (t[j] - t[j - 1]) > MAX_GAP_S
    y[gap] = np.nan
    return y


def _registry_fetch(source: str, t0: datetime, t1: datetime, extras: dict) -> dict:
    from solarapp.core.registry import get_registry
    return get_registry().get(source).fetch(t0, t1, **extras)


def epoch(cat: Catalogue, class_min: str = "M1.0", class_max: str | None = None,
          region: int | None = None, before_min: int = 60, after_min: int = 120,
          channel: str = "xrsb", align: str = "curve", limit: int = 50,
          exclude_overlaps: bool = True, progress: Progress | None = None, fetch=None) -> dict:
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
    if not 0 < limit <= EPOCH_MAX:
        raise ValueError(f"limit must be 1–{EPOCH_MAX}")
    fetch = fetch or _registry_fetch
    flares, sel = select_flares(cat, class_min, class_max, region, before_min, after_min,
                                limit, exclude_overlaps)
    if not flares:
        raise ValueError("no flares match")
    rel = np.arange(-before_min, after_min + 1) * 60.0
    done = 0

    def one(e: Event):
        t0 = e.peak - timedelta(minutes=before_min + 15)
        t1 = e.peak + timedelta(minutes=after_min + 15)
        t, v = _curve(channel, t0, t1, fetch)
        # measured peak: the curve's maximum within ±5 min of the catalogue peak
        p0 = e.peak.timestamp()
        near = np.abs(t - p0) <= 300
        if not near.any():
            raise ValueError("no samples near the catalogued peak")
        k = np.argmax(np.where(near, v, -np.inf))
        t_peak = t[k] if align == "curve" else p0
        y = _on_grid(t, v, t_peak + rel)
        # pre-flare background: median of the 10 min before catalogued start
        b0 = e.begin.timestamp()
        pre = (t >= b0 - 600) & (t < b0)
        if pre.sum() < 3:
            pre = (t >= t_peak - before_min * 60) & (t < t_peak - before_min * 60 + 600)
        bg = float(np.median(v[pre])) if pre.any() else float(np.nanmin(y))
        fp = float(v[k])
        decay = None
        after = (t > t[k]) & (v - bg <= (fp - bg) / math.e)
        if after.any() and fp > bg:
            decay = round((t[after][0] - t[k]) / 60, 1)
        return {"peak": e.peak.isoformat(), "class": e.cls, "region": e.region, "sat": e.sat,
                "offset_s": round(t[k] - p0), "flux_peak": fp, "background": bg, "curve": y,
                "rise_min": round((e.peak - e.begin).total_seconds() / 60, 1),
                "duration_min": round((e.end - e.begin).total_seconds() / 60, 1) if e.end else None,
                "decay_efold_min": decay}

    rows, skipped = [], []
    with ThreadPoolExecutor(4) as pool:
        futures = [(e, pool.submit(one, e)) for e in flares]
        for e, fut in futures:
            try:
                rows.append(fut.result())
            except Exception as err:  # noqa: BLE001
                skipped.append({"peak": e.peak.isoformat(), "class": e.cls, "why": str(err)[:120]})
            done += 1
            if progress:
                progress("epoch", done, len(flares))
    if not rows:
        raise ValueError("no flare had data: " + "; ".join(s["why"] for s in skipped[:3]))

    raw = np.array([r["curve"] for r in rows])
    fp = np.array([r["flux_peak"] for r in rows])[:, None]
    bg = np.array([r["background"] for r in rows])[:, None]
    modes = {"raw": raw, "norm": raw / fp,
             "bgsub": (raw - bg) / np.where(fp - bg > 0, fp - bg, np.nan)}

    def band(a):
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                q = np.nanpercentile(a, [25, 50, 75], axis=0)
        clean = lambda z: [None if not np.isfinite(x) else float(x) for x in z]  # noqa: E731
        return {"q25": clean(q[0]), "median": clean(q[1]), "q75": clean(q[2]),
                "n": np.isfinite(a).sum(axis=0).tolist()}

    clean = lambda z: [None if not np.isfinite(x) else float(x) for x in z]  # noqa: E731
    return {
        "channel": channel, "label": CHANNELS[channel][3], "align": align,
        "minutes": (rel / 60).astype(int).tolist(), **sel, "skipped": skipped,
        "flares": [{k: v for k, v in r.items() if k != "curve"} | {"curve": clean(r["curve"])}
                   for r in rows],
        "stats": {m: band(a) for m, a in modes.items()},
        "derived": {k: [r[k] for r in rows if r[k] is not None]
                    for k in ("rise_min", "duration_min", "decay_efold_min")},
        "note": ("t = 0 at " + ("each curve's own maximum within ±5 min of the catalogued peak"
                 if align == "curve" else "the catalogued GOES peak") + ". norm = flux / peak; "
                 "bgsub = (flux − B)/(peak − B), B = median of the 10 min before the catalogued "
                 "start. Rise and duration are the catalogue's; decay is the e-folding time of "
                 "(flux − B) after the peak, from the curve."
                 + (" Flares with another catalogued flare of ≥ half their flux inside the "
                    "window were left out." if exclude_overlaps else "")),
    }


def run_epoch(start: date, end: date, progress: Progress | None = None, **kw) -> dict:
    cat = catalogue(start, end, progress)
    out = epoch(cat, progress=progress, **kw)
    out["coverage_note"] = cat.note()
    return out


def stream_epoch(**kw) -> Iterator[dict]:
    return _stream(run_epoch, kw)


def _stream(fn, kw) -> Iterator[dict]:
    import queue
    import threading
    q: queue.Queue = queue.Queue()

    def progress(what, done, total):
        q.put({"event": "progress", "what": what, "done": done, "total": total})

    def work():
        try:
            q.put({"event": "result", **fn(progress=progress, **kw)})
        except Exception as e:  # noqa: BLE001
            q.put({"event": "error", "error": str(e) or type(e).__name__})
        q.put(None)

    threading.Thread(target=work, daemon=True).start()
    while (item := q.get()) is not None:
        yield item
