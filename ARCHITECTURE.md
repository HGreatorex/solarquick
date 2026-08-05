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
│   ├── sources/
│   │   ├── goes_xrs.py      ← v1: GOES XRS (NCEI archive via Fido, SWPC JSON for recent)
│   │   └── sdo_images.py    ← v2: SDO AIA/HMI full-disk quicklook images via Helioviewer
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

**PlotSpec** (dict), two kinds:
- timeseries (default): `title`, `series: [{name, times[iso], values[]}]`,
  `yaxis: {label, type: "log"|"linear"}`, optional `bands: [{label, y0, y1}]`.
- `kind: "images"`: `title`, `images: [{time, url, caption}]`, optional `note`.
  Frontend shows a main image + clickable thumbnail filmstrip.
Extend later with `kind: "spectrogram"` for radio.

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
- **SDO images are Helioviewer browse products** (JPEG2000 → server-rendered PNG):
  perfect for quicklook, not calibrated science data. The backend samples ≤8 times
  across the interval, resolves each to the nearest real image (getClosestImage),
  dedupes, and the browser loads takeScreenshot URLs directly from helioviewer.org.
  Helioviewer's ingestion lags real time by hours — "yesterday" may return fewer
  frames than requested; captions always show the true image time.

## Roadmap

- v1: GOES XRS quicklook, free-text query, single-day/range plots.
- v2 (this build): SDO image quicklook (AIA all channels + HMI magnetogram/continuum
  via Helioviewer), per-source query options (wavelength), image filmstrip UI.
- next: more timeseries sources (SDO/EVE, ionosonde/GNSS TEC, RHESSI/STIX counts),
  multi-source overlay ("show GOES and STIX for..."), HEK flare-event annotations,
  spectrograms (radio), export (PNG/CSV), optional LLM query layer, hosted deploy.

## Handoff notes (Fable → Opus continuation)

State at handoff is tracked in README "Status" section. To continue: read this file +
`core/registry.py` + one existing source plugin; that is the whole mental model.
Add sources by copying `goes_xrs.py` as a template. Tests: `pytest tests/`.
