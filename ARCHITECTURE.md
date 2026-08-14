# SolarQuick — Architecture & Blueprint

A stripped-down, extensible quicklook tool for solar/ionospheric physics data.
Type a request like *"show me the GOES SXR data for the 10th of July 2026"* → get a plot.
Spiritual successor to the IDL Solar Flare Finder; philosophically aligned with SunPy's `Fido`.

## Design principles

1. **One command to run**: `python -m solarapp` → browser UI at `localhost:8000`.
2. **Plugins are just files**: adding a data source = dropping one `.py` file into
   `solarapp/sources/`. No registration code elsewhere. A `git push` adds a mission.
3. **Everything is derived from what a plugin declares.** A source states its
   `keywords`, its `variables` (the options *inside* it), its `coverage` and its
   `summary_rank`; routing, option matching, the spell-checker's vocabulary,
   pre-flight refusals, the overview view, the progress display and
   `/api/sources` all read those four and need no edit of their own.
4. **Rule-based query parsing** (no API costs, deterministic). The optional LLM
   layer only *rewrites* a query the rules failed on, behind the same `parse()`.
5. **Backend returns a generic PlotSpec (JSON)**, frontend renders it with Plotly.
   Plugins never touch frontend code — a new source works in the UI for free.
6. **Never a confidently wrong answer.** Every interpretation is surfaced
   (typo correction, LLM rewrite, an auto-composed overview, a partial
   failure), ties between instruments are asked rather than resolved, and a
   query naming something unsupported fails instead of being approximated.

## Layout

```
solarapp/
├── ARCHITECTURE.md          ← this file
├── README.md                ← install & run instructions
├── pyproject.toml           ← deps: sunpy, fastapi, uvicorn, python-dateutil
├── solarapp/
│   ├── __main__.py          ← `python -m solarapp` entry point
│   ├── core/
│   │   ├── registry.py      ← DataSource + Variable + auto-discovery of sources/
│   │   ├── parser.py        ← which source(s), which options (rule-based)
│   │   └── timegrammar.py   ← which time span (dates, weekdays, ranges, DOY, CR)
│   ├── core/  (also)
│   │   ├── pipeline.py      ← one query as a stream of progress events
│   │   ├── summary.py       ← the overview a bare date composes
│   │   ├── timings.py       ← learned per-source durations (~/.cache/solarapp)
│   │   ├── helioviewer.py   ← shared Helioviewer image pipeline (channel tables)
│   │   ├── tsutil.py        ← df→series, clip, downsample, energy-band select
│   │   └── fetchcache.py    ← ~/.cache/solarapp download cache + dir listings
│   ├── sources/
│   │   ├── goes_xrs.py      ← GOES XRS (NCEI archive via Fido, SWPC JSON for recent;
│   │   │                       avg1m or flx1s, any spacecraft back to GOES-8)
│   │   ├── goes_euvs.py     ← GOES EUVS lines + MgII (NCEI netCDF; primary-sat default;
│   │   │                       GOES 13-15 Lyman-α backend for pre-2017)
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
│       ├── app.py           ← FastAPI: /api/query(+/stream), /api/sources, static/
│       └── static/index.html← single-page UI: query box + progress + Plotly plot
└── tests/                   ← 354, all offline
```

## Request flow

```
"show me GOES SXR for 10 July 2026"
   → POST /api/query/stream                    (or /api/query for one JSON blob)
   → pipeline.run(): a generator of progress events, drained or forwarded
   → parser.parse(): segments → goes_xrs; variables → extras; consumed tokens
                     stripped; timegrammar.extract() → 2026-07-10 00:00–24:00 UT
   → {"event": "parsed", sources:[...with learned estimate_ms...]}   ← the UI draws
   → GoesXRS.fetch(start, end): recent (<7 d) → SWPC realtime JSON; else → Fido/NCEI
   → {"event": "source_done", ms, ok}                               ← ticked off
   → {"event": "result", spec: {title, series[], yaxis{log}, bands[A/B/C/M/X]}}
   → Plotly renders interactive log-scale timeseries
```

A query naming no instrument at all takes one extra step: if it is *only* a
date, `summary.compose()` builds the overview from the sources that opted in
and were observing then, and the reading is reported in the note.

## Key interfaces

**DataSource** (subclass in `sources/`, auto-discovered):
```python
class MySource(DataSource):
    name = "my_source"
    display_name = "My Instrument"
    keywords = ["mymission", "instrument nickname", ...]   # parser vocabulary
    coverage = ("2015-03-01", None)      # (first light, last light); checked
                                         # before any network call
    summary_rank = 50                    # opt in to the bare-date overview
    summary_extras = {"channel": "blue"} # ...with these options
    variable_key = "channel"             # extras key a bare Variable fills
    variables = [                        # the options *inside* this source
        Variable("blue", "Blue 430 nm", ["blue", "430", "43 nm"]),
        Variable("fast", "high cadence", ["high resolution"],
                 extras={"cadence": "1s"}, group="cadence"),
    ]
    def fetch(self, start: datetime, end: datetime, **extras) -> PlotSpec: ...
    # optional: options a keyword table can't express (a satellite number, a
    # flare-class threshold). Call variable_extras() from the override to keep
    # the declared ones. Returns (extras, consumed_tokens); consumed tokens are
    # stripped before date parsing (so "aia 171" doesn't feed 171 to the date
    # parser).
    def parse_extras(self, query: str) -> tuple[dict, list[str]]: ...
    # optional: override how strongly a query asks for this source. The default
    # sums the lengths of the keywords that matched; override only for context
    # a flat list can't express (Wind/WAVES withdraws its bare "wind" when the
    # query says "solar wind"; Positions outranks EUI on "where is solar
    # orbiter"). Do NOT use it to break a genuine tie — see below.
    def score(self, text: str) -> int: ...
```

**Variables are declared, not hidden in a regex.** Before this, every option
lived inside a private `parse_extras`, so the rest of the app could not know
that "he ii" meant anything: `euvs he ii`, `lyra zirconium` and `rhessi 25-50
keV` all silently returned the source default, which is a different quantity
with nothing to signal the misread. Declaring them buys six things at once —
the option is matchable, its words join the spell-checker's vocabulary, its
matched text is consumed before date parsing, `/api/sources` can list it, a
"…and 193" segment can attach to the instrument named before it, and a
quantity named with no instrument ("sunspots") can find its own source.

`group` separates *dimensions* from *alternatives*: options in different
groups combine ("short channel high resolution" is a channel and a cadence),
options in the same group are exclusive and the best-scoring one wins. Within
one source a tie goes to the earlier declaration — between sources a tie is a
question, but between two channels of one instrument, refusing to answer is
the worse trade.

**Keyword matching** (`registry.keyword_regex`) treats internal hyphens,
slashes and spaces as interchangeable and optional, and allows a trailing
plural `s`. One keyword `"x-ray"` therefore covers `x-ray`, `x ray`, `xray`,
`x-rays` and `xrays`; keyword lists stay short and spelling variants are the
matcher's problem, not the plugin author's. Write the keyword in its most
separated form (`"proba 2"`, not `"proba2"`) so every spelling folds into it.

**Ties are questions, not defaults.** When several sources share the top score
the parser returns an error naming all of them instead of picking one — `euv`
really does belong to AIA, EIT, EUI, SWAP, EVE and EUVS, and load order is not
an answer. Adding a broad keyword to several sources is therefore safe: naming
an instrument breaks the tie by scoring higher.

**Time grammar** (`core/timegrammar.py`) is the whole of "when". It is tried
from the most explicit phrasing to the least — ISO range, single ISO, natural
range, slash date, relative words, weekday, day-of-year/Carrington, fuzzy
precision — so a query that states a date outright is never reinterpreted by a
looser rule. Two conventions worth knowing: precision sets the *width* of the
span ("2026" is a year, "10 July 2026" a day), and a relative word resolves
*backwards*, because data only exists in the past — a bare weekday or a
month-and-day with no year means the most recent one, and "next" is the only
qualifier that looks forward.

**The overview** (`core/summary.py`) answers a query that is only a date.
Membership is the plugins' `summary_rank`, filtered by `coverage`, capped at
four — so 2003 quietly gets XRS + flares + LASCO rather than those plus two
errors about SDO not existing yet. The guard that keeps this honest is
`parser.names_only_a_date`: "solar wind speed yesterday" also names no
instrument, but answering it with an overview would be a wrong answer to a
question about the plasma stream, so it errors.

**Progress** (`core/pipeline.py`, `POST /api/query/stream`) makes one query a
generator of events; `/api/query` drains it and returns the last one, so the
two endpoints cannot disagree. The browser reads NDJSON and draws a row per
source with the option chosen, elapsed time, and how long that source usually
takes — from `core/timings.py`, an EWMA per source in `~/.cache/solarapp`.
Those durations are *measured, never declared*: what a fetch costs depends on
the archive, the window, the network and the local file cache, so a "typical"
number in a plugin would be wrong on every machine but the author's.

**Export** (`core/export.py`, `POST /api/export`) turns one PlotSpec block into
a CSV or JSON download. The browser posts back the spec it is already
displaying rather than the server re-running the query — the file is then
guaranteed to be the same numbers as the figure, and slow archives aren't
fetched twice. Each kind has its own tabular shape (timeseries align every
series onto one time column; spectrograms are a frequency × time matrix; events,
positions and earthmap have their own columns); images have no tabular form and
are refused with a pointer to the frame button. PNG is produced client-side by
Plotly so it captures the rendered figure. Every CSV carries a commented header
with the title, the query, the provenance, and the sampling disclosure below.

**Caching** happens at three levels, deliberately separate:
- `core/resultcache.py` — fetched PlotSpecs, keyed on source + options +
  concrete start/end. TTL is recency-aware: a window that ended over
  `ARCHIVE_LAG_DAYS` ago is settled and caches for hours, while anything
  reaching into the present expires in minutes because the archives are still
  filling it. Failures are never cached. Hits are reported in the response
  (`cached: [...]`) and `SOLARQUICK_CACHE=0` disables it.
- `core/httpclient.py` — one pooled `requests.Session` for every source, plus a
  short TTL cache for rolling feed URLs. That feed cache covers what the result
  cache cannot: SWPC publishes one ~4.6 MB 7-day feed, so *different* windows
  within that week previously re-downloaded all of it (1230 ms → 4 ms).
- `core/fetchcache.py` and sunpy's own `~/sunpy/data` — downloaded archive files.

**Event expansion** (`core/events.py`) runs between parse and fetch. If the
query names a flare superlative ("biggest flare since 2014") or a pinpoint
("flare at 2017-09-06 12:02"), it resolves the flare against HEK, narrows
the time range to its rise–decay window, and — unless the user named their
own companions — appends the instruments worth seeing: GOES XRS, AIA 131+304
cropped to the flare site, STIX if Solar Orbiter was flying, SOHO EIT
instead of AIA for pre-2010 events. Everything downstream is just an
ordinary multi-source request.

**PlotSpec** (dict), eight kinds:
- timeseries (default): `title`, `series: [{name, times[iso], values[]}]`,
  `yaxis: {label, type: "log"|"linear"}`, optional `bands: [{label, y0, y1}]`.
  A series may use numeric `x` instead of `times` (spectra: irradiance vs nm).
- `kind: "images"`: `title`, `images: [{time, url, thumb_url, caption}]`,
  optional `note`. Frontend shows a main image + clickable thumbnail filmstrip;
  the strip loads `thumb_url` (~20 KiB) and only the displayed frame is fetched
  from `url` (~1 MiB). `url` may be a data URI (Hinode SOT renders FITS
  server-side, and has no separate thumbnail).
- `kind: "spectrogram"`: `times[]`, `freqs[]`, `z[freq][time]`, `zrange`,
  `zlabel` — rendered as a Plotly heatmap (Wind/WAVES).
- `kind: "panels"`: `panels: [spec...]` — stacked subplots (GOES EUVS
  channel groups, WAVES RAD2/RAD1).
- `kind: "multi"`: `items: [spec...]` — one block per requested source
  (server-assembled for multi-source queries), any of the kinds above;
  optional `note` lists sources that failed.
- `kind: "events"`: `events: [{class, start, peak, end, x, y, ar, query,
  selected}]` + `markers: [{t, label}]` — flare table (rows click through to
  their event view via `query`); `markers` are lifted by the frontend and
  drawn as dashed peak lines on *every* timeseries in the same result.
- `kind: "positions"`: `bodies: [{name, type, color, x, y, r, lon_deg,
  trail?}]` — synoptic ecliptic-plane map (Sun at origin, Earth-Sun line
  fixed at 0°), equal-aspect scatter with orbit guides and motion trails.
- `kind: "earthmap"`: `shading: [{lon, lat, opacity, label}]` (null-separated
  parallel segments), `lines` (terminator), `points` (subsolar), optional
  `track` — day/night world map on Plotly's built-in geo (scattergeo,
  natural earth projection; topojson comes from the Plotly CDN).

## Caveats (read before extending)

- **HEK flare positions are unreliable**: the SWPC list often reports
  `hpc_x = 0` when the location is unknown (with a plausible-looking non-zero
  `hpc_y`), so `Flare.has_position` requires |x| > 1″ before cropping to a
  site. Event views silently fall back to full-disk images.
- **HEK is filtered to one provider** (`OBS.Observatory == "GOES"`, i.e. the
  SWPC list). Dropping that filter returns the same flare several times from
  different pipelines (SWPC, SSW Latest Events, ...) with slightly different
  coordinates — dedupe before you widen it.
- **"Biggest flare since <year>" searches class bands descending** (X → M →
  C → B) rather than fetching everything and sorting: an unfiltered decade
  query is tens of thousands of rows, while X-class since 2014 is ~130 rows
  in ~20 s.
- **Helioviewer crop y is south-positive**: `center_y` arrives as
  helioprojective (north-positive) arcsec and is negated for the
  `takeScreenshot` `y0` parameter. Verified against the 2017-09-06 X9.3
  (AR 12673 at 515″, −232″) — the flip puts the saturated core centre-frame.
- **NCEI archive latency**: GOES XRS science files appear on NOAA NCEI days after
  observation. The plugin uses SWPC's realtime 7-day JSON for recent times; there can
  be a gap (~7 days to ~2 weeks old) where neither has data yet.
- **NCEI's science-quality XRS fluxes are not SWPC's flux.** The reprocessed
  files are true irradiances; SWPC's operational values (and therefore the
  A–X flare classes) carry the historical 0.7/0.85 scaling. Measured on the
  2014-06-10 X2.2: science-quality XRS-B peaks at 3.3e-4, i.e. an "X3.3",
  ~1.5× its catalogue class. `FLARE_BANDS` is drawn in operational units, so
  for pre-GOES-R dates the bands locate a flare correctly but overstate its
  class. This predates the flx1s work — the avg1m backend has always served
  the same science files — but do not quote a class off the figure.
- **The pre-2017 EUVS L2 archive is Lyman-α only.** `geuv-l2-ir10s` declares
  `irr_304_1nm` and `irr_chanB`, and *every value in them is fill* — verified
  across GOES-14 and GOES-15 files from 2009, 2010, 2012, 2014, 2016 and 2019.
  Only channel E (121–122 nm) was reduced. Don't add a 30.4 nm series there on
  the strength of the variable existing; the request is refused with a pointer
  to SDO EVE instead.
- **Legacy EUVS is a 1-nm band, GOES-R EUVS is a line.** The legacy variable
  is `irr_1216_1nm` (121–122 nm); GOES-R's `irr_1216` is the Lyman-α line
  itself. They happen to sit within a few percent of each other, and both
  archives store *observed* irradiance with a separate `au_factor` for 1-AU
  normalisation, so a curve crossing 2017 is continuous in convention — but
  the two are not the same quantity and the legacy series is labelled as the
  band it is.
- **GOES-14 is a standby spacecraft**: its EUVS coverage is scattered
  campaigns (2009, 2010, 2012, 2015–2020), not a span, so it is offered by
  name and never chosen by default. GOES-15 (2010-03-25 – 2020-03-04) is.
- **A number after "goes" is usually a date, not a satellite.**
  `tsutil.satellite_in_query` refuses to read one when a month name or an
  ordinal suffix follows, because consuming "goes 10" out of "goes 10 july
  2014" strips the day before the date parser sees it and silently widens the
  request to a whole month.
- **Times are UTC everywhere.** Parser assumes day-first for ambiguous dates (10/07/2026 = 10 July).
- **sunpy caches downloads** in `~/sunpy/data` — repeat queries are fast, disk grows.
- **Parser is keyword-based**: unknown phrasings fail gracefully with a "did you mean"
  message listing the nearest known phrases and the available sources. It is not an AI.
- **The vocabulary's run-together spelling must not strip digits.** A keyword's
  separators are optional when matching, so "x-ray" also teaches the
  spell-checker "xray" — but building that form with `[^a-z]` removal turned
  "lasco c2" into the non-word "lascoc", and `lasoc` then "corrected" to it.
  Only separators are removed now, and the result is kept only if it is
  alphabetic.
- **Function words have to be in the vocabulary.** With "band" among the
  declared option words, an unprotected "and" was one edit away from it, and
  every multi-source query silently lost its second instrument. `_STOPWORDS`
  exists to be *known*, not to be matched. A correction is also refused when it
  changes a word's length by more than one: a typo is a slip ("lasoc" →
  "lasco"), whereas "slit" → "sunlit" invents two characters and answers a
  question about spectrograph slits with a daylight map.
- **Physics nicknames make bad option keywords.** "chromosphere" on AIA 304 and
  a bare "spectra" on EVE both routed queries meant for instruments SolarQuick
  does not have (IRIS, in both cases). Ion labels and wavelengths are precise;
  vague descriptions of what a channel *shows* are not, and they compete with
  every unsupported instrument that shows the same thing.
- **A weekday abbreviation list must exclude "sat" and "sun."** In this app
  they are a satellite and the star far more often than Saturday and Sunday.
- **An unspaced hyphen is part of a date, not a range separator.** `wind
  8-may-2024` split into "8" and "may-2024" and was read as May 2024 through
  the end of that year — a single-day query that became eight months. Reported
  from the field, and the class of bug is worth naming: *a range rule is a
  widening rule*, so anything it accepts as a separator must be something no
  date contains. Word separators (`to`, `until`, `and`) and dashes with spaces
  around them qualify; a bare `-` does not. The one place an unspaced hyphen is
  still allowed is `10-12 july`, where the right-hand side must lead with a day
  number — which is exactly what `8-may-2024` does not.
- **An over-long request is usually a misread date.** `tsutil.check_max_days`
  quotes the window it understood, because "limited to 2 days" told the user
  nothing about the fact that their one day had become eight months. When a
  limit fires, the interpretation is the diagnosis.
- **A plugin's `fetch` is never run by the suite**, so a missing import
  survives every test and appears as a NameError in front of a user, usually on
  an error branch. `tests/test_sources_static.py` reads each plugin's bytecode
  and asserts every global it loads exists — cheap, and it catches the one
  mistake this architecture makes easy.
- **Typos are fixed by retry, not looser matching.** `_autocorrect` rewrites
  only tokens absent from the vocabulary (keyword words + date/grammar words),
  then the query is parsed again; corrections are reported to the user via
  `ParsedQuery.corrections`. Correction runs *before* parsing because a typo
  usually degrades a parse rather than breaking it ("bigest flare since 2014"
  would otherwise return a 12-year list instead of one flare). Tokens of 3
  characters are only matched against grammar words — the instrument acronyms
  are dense (sot/sdo, eve/eui/eit are 1–2 edits apart) and would resolve
  confidently to the wrong instrument.
- **A date must contain a year or month name** to reach the fuzzy span logic:
  dateutil reads a bare "3" as a day-of-month, which the precision rules would
  widen into a whole-year request.
- **The LLM fallback rewrites, it does not parse.** `core/llm_parser.py` asks
  Claude to restate an unparseable query in canonical SolarQuick syntax, and
  that string goes back through `_parse_once`. So every source's
  `parse_extras()` is reused, and the model's output is only ever
  keyword-matched — a hallucinated instrument or an injected instruction cannot
  reach `fetch()` or widen what the app will retrieve, it just fails to match.
  The rewrite is always shown to the user. Dormant unless `ANTHROPIC_API_KEY`
  is set (`SOLARQUICK_LLM=0` forces off, `SOLARQUICK_LLM_MODEL` overrides the
  model); it runs only after the deterministic and typo-correcting passes both
  fail, so ordinary queries never pay for it.
- **Bare instrument words can collide with physical quantities.** Wind/WAVES
  used to claim the keyword `wind`, so "solar wind speed" silently returned
  radio spectrograms — a different quantity entirely, with nothing to signal
  the misread. Prefer `wind waves` / `wind spacecraft` over a bare mission word
  whenever the word also names a phenomenon.
- **Satellite choice**: defaults to primary GOES; `a.goes.SatelliteNumber` can be
  exposed as a query option later ("goes-16 sxr ...").
- **Helioviewer-backed images are browse products** (JPEG2000 → server-rendered
  PNG): perfect for quicklook, not calibrated science data. The backend samples
  ≤8 times across the interval, resolves each to the nearest real image
  (getClosestImage, all in parallel — serially this was ~470 ms × 8), dedupes,
  and the browser loads takeScreenshot URLs directly.
- **A frame's `imageScale` must scale inversely with its pixel width** to hold
  the field of view constant — otherwise a small render crops into the middle of
  the disk instead of framing all of it. `_screenshot_url(..., px=)` derives it;
  don't hardcode a scale for a new size.
  Ingestion lags real time by hours (EUI: weeks–months, SOAR releases);
  captions always show the true image time.
- **Archive latencies**: LASP EVE ~2 days; NCEI EUVS ~1–2 weeks; SPDF WAVES L2
  a few weeks; STIX downlink gaps of days–weeks are normal.
- **Instrument lifetimes** are enforced with friendly errors: RHESSI
  2002-02-12–2018-04-11; Hinode SOT 2006-10-25–2016-02-25 (campaign-based,
  many empty days); EVE MEGS-A (λ ≲ 33 nm) lost 2014-05-26, MEGS-B at reduced
  duty cycle; GOES-R EUVS from 2017-02-07 (GOES-16), GOES-19 primary from
  2025-04-07; legacy GOES EUVS 2009-09-01 (GOES-14) / 2010-03-25 (GOES-15)
  through 2020-03-04.
- **Fermi GBM is an all-sky monitor**: detector choice matters (default n5,
  override "gbm n3 ..."), solar pointing not guaranteed.
- **High-cadence series are mean-downsampled** (~3000 points/series) before
  hitting the browser (GBM 0.256 s, RHESSI 4 s, STIX 4 s). **A CSV export is
  therefore not always archive resolution** — `downsample_df` records what it
  did in `df.attrs["sampling"]`, the source copies that into the spec, and the
  export writes it into the header. A file that looked like raw instrument data
  but silently contained binned means would be worse than no export, so any new
  downsampling source must thread that field through too.
- **Wind/WAVES intensity** is 10·log10(PSD/daily-background) — the standard
  quicklook normalisation, not absolute flux.

## Roadmap

- v1: GOES XRS quicklook, free-text query, single-day/range plots.
- v2: SDO image quicklook (AIA + HMI via Helioviewer), per-source query
  options, image filmstrip UI.
- v3/v4: SOHO EIT/LASCO, PROBA2 SWAP+LYRA, SolO EUI+STIX,
  RHESSI, Fermi GBM, Wind/WAVES spectrograms, SDO EVE lines/spectra,
  GOES EUVS multi-panel, Hinode SOT; spectrogram/panels/spectrum plot kinds.
- v5: multi-source queries — "goes xrs and aia 304 yesterday"
  splits on conjunctions (date ranges masked first), one request per segment
  (same source may repeat with different options), fetched concurrently,
  rendered as stacked blocks; per-source failures reported without sinking
  the rest.
- v6: synoptic positions — inner planets (astropy ephemeris)
  + PSP/SolO/STEREO-A/BepiColombo (JPL Horizons) on a Stonyhurst
  ecliptic-plane map; single epoch for ≤1-day queries, motion trails for
  ranges ("where is psp from 2026-06-01 to 2026-08-01").
- v7: Earth illumination — terminator + civil/nautical/
  astronomical twilight bands and the subsolar point from astropy (sunrise
  equation on parallels); multi-day ranges add a fixed-UT daily subsolar
  track (free-running sampling would alias — the point circles the globe
  daily).
- v8: flare events — HEK/SWPC catalog source (`flares`), event
  lists with click-through, peak markers auto-overlaid on any timeseries in
  the same result, superlative/pinpoint queries auto-composing an event view
  (see "Event expansion"); richer time grammar (last N days, "since 2014",
  bare-year/month spans) and crops in both dimensions — time-of-day
  ("14:00 to 15:30", "at 14:30") and spatial (Helioviewer `center_x/y` +
  `fov`, used to frame the flare site).
- v9 (this build): smart query understanding, in three layers behind the same
  `parse()` interface — exact keyword/date rules, then typo correction, then an
  optional Claude rewrite. Each layer reports what it did; none of them can
  widen what the app fetches.
- v10 (this build): export — per-block PNG / CSV / JSON download buttons, with
  provenance, the originating query, and sampling disclosure in every CSV
  header (see "Export").
- v1.1 (this build): performance. Per-source `timings` in every response;
  filmstrip metadata resolved in parallel with thumbnail-sized frames; result
  cache with recency-aware TTL; one pooled HTTP session plus a rolling-feed
  cache; bounded parfive timeouts.
- v1.2 (this build): the pre-GOES-R archives. GOES EUVS gains a GOES 13-15
  backend (NCEI `geuv-l2-ir10s`, 10.24 s, Lyman-α only) so "euvs 2014-06-10"
  answers instead of erroring, and GOES XRS gains the high-resolution science
  irradiance product (`gxrs-l2-irrad_science`, 1–3 s depending on spacecraft,
  via Fido's `flx1s`) plus explicit satellite choice back to GOES-8. Both
  mean-bin for the browser and disclose the binning in exports.
- v1.3 (this build): query intelligence, without requiring the LLM layer.
  Sources declare their `variables`, `coverage` and `summary_rank`, and the
  parser, spell-checker, help listing and overview all derive from those; the
  time grammar moves to `core/timegrammar.py` and gains weekdays, spelled-out
  counts, parts of a day, weekends, bare day-of-month, slash dates, natural
  ranges, day-of-year and Carrington rotations; a segment naming only an option
  continues the instrument before it; a bare date composes an overview; and
  every query streams progress events with learned per-source durations.
- next: hosted deploy — but see the note in the handoff section: local install
  is the recommended distribution route, and the LLM layer does not require
  hosting.

## Handoff notes

To continue: read this file + `core/registry.py` + one existing source plugin;
that is the whole mental model. Add sources by copying `goes_xrs.py` as a
template. Tests: `pytest tests/` (389, all offline — external APIs are never
called from the suite, and a test that quietly starts doing so shows up as a
suite that takes twenty seconds instead of one).

**Verify external APIs live before writing a plugin.** Several archives have
moved or lag: the Wind/WAVES `.R1` files are gone (use SPDF L2 CDFs), LASP's EVE
`level2` tree stopped in 2018 (use `level2b`), and NCEI 503s intermittently
under load. `curl` the endpoint and inspect a real file before coding against it.

**Unverified:** the Claude fallback's live path. Its wiring, guardrails, and
failure handling are covered by stubbed tests, but no request has been made
against the real API from this machine — there was no `ANTHROPIC_API_KEY`, SDK
install, or `ant` login available. Before relying on it: `pip install -e '.[llm]'`,
export a key, and try "the big x-ray blast during the halloween storms".
