# SolarQuick — Architecture & Blueprint

A stripped-down, extensible quicklook tool for solar/ionospheric physics data.
Type a request like *"show me the GOES SXR data for the 10th of July 2026"* → get a plot.
Spiritual successor to the IDL Solar Flare Finder; philosophically aligned with SunPy's `Fido`.

## Design principles

1. **One command to run**: `python -m solarapp` → browser UI at `localhost:8000`.
2. **Plugins are just files**: adding a data source = dropping one `.py` file into
   `solarapp/sources/`. No registration code elsewhere. A `git push` adds a mission.
3. **Rule-based query parsing** (no API costs, deterministic). Each plugin declares its
   own keywords; the parser's vocabulary grows automatically with each new source.
   An optional LLM layer can be bolted on later behind the same `parse()` interface.
4. **Backend returns a generic PlotSpec (JSON)**, frontend renders it with Plotly.
   Plugins never touch frontend code — a new source works in the UI for free.

## Layout

```
solarapp/
├── ARCHITECTURE.md          ← this file
├── README.md                ← install & run instructions
├── pyproject.toml           ← deps: sunpy, fastapi, uvicorn, python-dateutil
├── solarapp/
│   ├── __main__.py          ← `python -m solarapp` entry point
│   ├── core/
│   │   ├── registry.py      ← DataSource base class + auto-discovery of sources/
│   │   └── parser.py        ← rule-based NL parser (source keywords + dates)
│   ├── core/  (also)
│   │   ├── helioviewer.py   ← shared Helioviewer image pipeline (channel tables)
│   │   ├── tsutil.py        ← mission-span guard, df→series, clip, downsample
│   │   └── fetchcache.py    ← ~/.cache/solarapp download cache + dir listings
│   ├── sources/
│   │   ├── goes_xrs.py      ← GOES XRS (NCEI archive via Fido, SWPC JSON for recent)
│   │   ├── goes_euvs.py     ← GOES EUVS lines + MgII (NCEI netCDF; primary-sat default)
│   │   ├── sdo_images.py    ← SDO AIA/HMI images via Helioviewer
│   │   ├── sdo_eve.py       ← SDO EVE L2B lines & spectra (LASP)
│   │   ├── soho_images.py   ← SOHO EIT + LASCO C2/C3 via Helioviewer
│   │   ├── solo_eui.py      ← Solar Orbiter EUI FSI/HRI via Helioviewer
│   │   ├── solo_stix.py     ← Solar Orbiter STIX quicklook (STIX Data Center API)
│   │   ├── proba2_swap.py   ← PROBA2 SWAP 174 Å via Helioviewer
│   │   ├── proba2_lyra.py   ← PROBA2 LYRA lev3 via Fido
│   │   ├── rhessi.py        ← RHESSI observing summary via Fido (2002–2018)
│   │   ├── fermi_gbm.py     ← Fermi GBM CTIME via Fido (detector option)
│   │   ├── wind_waves.py    ← Wind/WAVES RAD1+RAD2 spectrograms (SPDF L2 CDF)
│   │   └── hinode_sot.py    ← Hinode SOT filtergrams via VSO (FITS → PNG data URIs)
│   └── server/
│       ├── app.py           ← FastAPI: /api/query, /api/sources, serves static/
│       └── static/index.html← single-page UI: query box + Plotly plot
└── tests/test_parser.py
```

## Request flow

```
"show me GOES SXR for 10 July 2026"
   → POST /api/query
   → parser.parse(): match plugin keywords → goes_xrs; extract date(s) → 2026-07-10 00:00–24:00 UT
   → GoesXRS.fetch(start, end): recent (<7 d) → SWPC realtime JSON; else → Fido/NCEI netCDF
   → PlotSpec JSON {title, series[], yaxis{log}, bands[A/B/C/M/X]}
   → Plotly renders interactive log-scale timeseries
```

## Key interfaces

**DataSource** (subclass in `sources/`, auto-discovered):
```python
class MySource(DataSource):
    name = "my_source"
    display_name = "My Instrument"
    keywords = ["mymission", "instrument nickname", ...]   # parser vocabulary
    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec: ...
    # optional: pull source-specific options out of the raw query.
    # Returns (extras, consumed_tokens); consumed tokens are stripped before
    # date parsing (so "aia 171" doesn't feed 171 to the date parser).
    def parse_extras(self, query: str) -> tuple[dict, list[str]]: ...
```

**PlotSpec** (dict), four kinds:
- timeseries (default): `title`, `series: [{name, times[iso], values[]}]`,
  `yaxis: {label, type: "log"|"linear"}`, optional `bands: [{label, y0, y1}]`.
  A series may use numeric `x` instead of `times` (spectra: irradiance vs nm).
- `kind: "images"`: `title`, `images: [{time, url, caption}]`, optional `note`.
  Frontend shows a main image + clickable thumbnail filmstrip. `url` may be a
  data URI (Hinode SOT renders FITS server-side).
- `kind: "spectrogram"`: `times[]`, `freqs[]`, `z[freq][time]`, `zrange`,
  `zlabel` — rendered as a Plotly heatmap (Wind/WAVES).
- `kind: "panels"`: `panels: [spec...]` — stacked subplots (GOES EUVS
  channel groups, WAVES RAD2/RAD1).

## Caveats (read before extending)

- **NCEI archive latency**: GOES XRS science files appear on NOAA NCEI days after
  observation. The plugin uses SWPC's realtime 7-day JSON for recent times; there can
  be a gap (~7 days to ~2 weeks old) where neither has data yet.
- **Times are UTC everywhere.** Parser assumes day-first for ambiguous dates (10/07/2026 = 10 July).
- **sunpy caches downloads** in `~/sunpy/data` — repeat queries are fast, disk grows.
- **Parser is keyword-based**: unknown phrasings fail gracefully with a "did you mean"
  message listing available sources. It is not (yet) an AI.
- **Satellite choice**: defaults to primary GOES; `a.goes.SatelliteNumber` can be
  exposed as a query option later ("goes-16 sxr ...").
- **Helioviewer-backed images are browse products** (JPEG2000 → server-rendered
  PNG): perfect for quicklook, not calibrated science data. The backend samples
  ≤8 times across the interval, resolves each to the nearest real image
  (getClosestImage), dedupes, and the browser loads takeScreenshot URLs directly.
  Ingestion lags real time by hours (EUI: weeks–months, SOAR releases);
  captions always show the true image time.
- **Archive latencies**: LASP EVE ~2 days; NCEI EUVS ~1–2 weeks; SPDF WAVES L2
  a few weeks; STIX downlink gaps of days–weeks are normal.
- **Instrument lifetimes** are enforced with friendly errors: RHESSI
  2002-02-12–2018-04-11; Hinode SOT 2006-10-25–2016-02-25 (campaign-based,
  many empty days); EVE MEGS-A (λ ≲ 33 nm) lost 2014-05-26, MEGS-B at reduced
  duty cycle; GOES-R EUVS from 2017-02-07 (GOES-16), GOES-19 primary from
  2025-04-07.
- **Fermi GBM is an all-sky monitor**: detector choice matters (default n5,
  override "gbm n3 ..."), solar pointing not guaranteed.
- **High-cadence series are mean-downsampled** (~3000 points/series) before
  hitting the browser (GBM 0.256 s, RHESSI 4 s, STIX 4 s).
- **Wind/WAVES intensity** is 10·log10(PSD/daily-background) — the standard
  quicklook normalisation, not absolute flux.

## Roadmap

- v1: GOES XRS quicklook, free-text query, single-day/range plots.
- v2: SDO image quicklook (AIA + HMI via Helioviewer), per-source query
  options, image filmstrip UI.
- v3/v4 (this build): SOHO EIT/LASCO, PROBA2 SWAP+LYRA, SolO EUI+STIX,
  RHESSI, Fermi GBM, Wind/WAVES spectrograms, SDO EVE lines/spectra,
  GOES EUVS multi-panel, Hinode SOT; spectrogram/panels/spectrum plot kinds.
- next: (1) multi-source overlay ("show GOES and STIX for..."), (2) synoptic
  data (spacecraft/planet positions relative to the Sun), (3) Earth
  illumination plots. Later: HEK event annotations, export (PNG/CSV),
  optional LLM query layer, hosted deploy.

## Handoff notes (Fable → Opus continuation)

State at handoff is tracked in README "Status" section. To continue: read this file +
`core/registry.py` + one existing source plugin; that is the whole mental model.
Add sources by copying `goes_xrs.py` as a template. Tests: `pytest tests/`.
