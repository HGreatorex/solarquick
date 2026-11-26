# SolarQuick — Live, Stats & Extensions: build spec (2026-11-25)

Agreed with Harry. Build in phase order. Each phase = own commit(s); tests stay offline
(record JSON/text fixtures for every new feed under `tests/fixtures/`).

Feeds checked live from Harry's machine on 2026-11-25 (all 200 OK, no key):
`services.swpc.noaa.gov/json/{solar_regions,solar_probabilities}.json` (30 d history each),
`json/goes/primary/xray-flares-7-day.json`, `products/alerts.json`, `products/noaa-scales.json`,
`json/solar-cycle/observed-solar-cycle-indices.json`. JSOC `jsoc_info` reachable.
NCEI RSGA archive: `ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/reports_solar_geophysical_activity/YYYY/MM/YYYYMMDDRSGA.txt`.
DONKI event chain: dropped by Harry 2026-11-25.
Helioviewer has STEREO_A EUVI (sourceId 21 = 195 Å; ~3 d latency), GOES (SUVI) and PUNCH.

---

## Phase 1 — LIVE mode

### UI
- Header button `LIVE` (teletext; red block flashes while on). Click → right pane swaps to the
  Live view; left pane swaps to Live controls. `REGULAR` button returns. Regular-mode DOM,
  form state and last results are hidden, not destroyed (no re-fetch on return).
  `#live` in the URL so a reload stays in Live.
- Live view = Ceefax-style sub-pages as tabs: P100 NOW · P101 REGIONS · P102 FLARES · P103 GEO.
- Live controls (left): refresh interval (30 s / 60 s default / 5 min), `REFRESH NOW`,
  "updated HH:MM:SS UT", STALE flag if last success > 3× interval, alert toggle + threshold.

### Refresh (local app → client polling)
- Client polls `GET /api/live?pages=...` on the interval; pauses when `document.hidden`,
  refreshes immediately on return to visible.
- Backend `core/live.py`: one endpoint, per-panel partial failure (a dead feed greys one panel).
  Per-feed TTL via existing `httpclient.get_json_cached`:
  X-ray 1-min (`xrays-6-hour.json`) 60 s · `xray-flares-latest` 60 s · `xray-flares-7-day` 5 min ·
  `alerts` / `noaa-scales` 2 min · `solar_regions` 15 min · `solar_probabilities` 1 h ·
  Kp 5 min · protons 5 min · solar wind 2 min.
- Live is a view, not a DataSource; reuse plugin code (e.g. `goes_xrs._fetch_swpc`, regions
  disk projection, Helioviewer image pipeline) rather than duplicating it.

### Pages
- **P100 NOW**: R/S/G banner (current + day 1–3 from noaa-scales); current long-channel flux and
  class; 6 h XRS both channels with A–X bands (existing timeseries PlotSpec); FLARE IN
  PROGRESS flag from `xray-flares-latest` (no end_time); alerts ticker (last 24 h, product id +
  first message line, click to expand).
- **P101 REGIONS**: latest `observed_date` from `solar_regions.json` → table: AR, location, area,
  McIntosh (`spot_class`), `mag_class`, spots, C/M/X today, **per-region C/M/X probabilities**
  (`c/m/x_flare_probability`). Disk map (existing `regions` kind) over latest AIA 171; click
  row → regular-mode AR track. Whole-disk C/M/X day-1..3 from `solar_probabilities.json`.
- **P102 FLARES**: 7-day list (begin/max/end, class, sat). Feed has no positions → attach AR/site
  with existing `flare_sites_from_regions`. Row click → regular-mode event view (existing path).
- **P103 GEO**: Kp 3 d bars coloured by G level; solar wind speed/density/Bz; GOES ≥10 MeV
  protons 3 d with S1 (10 pfu) line.

### Flare alerts
- Client-side on each X-ray poll: onset = long-channel flux ≥ threshold (C5 / **M1 default** /
  M5 / X1) and rising over last 3 min. One alert per event; re-arm when flux < 0.5× threshold
  or after 30 min.
- Also notify on any new `alerts.json` product id since last poll (X-ray ≥M5, proton, geomag).
- Delivery: browser Notification API (permission asked when alerts first enabled) + flashing
  in-page banner + optional beep. Works only while the page is open (state this in the UI).
- Use SWPC operational flux (consistent with SWPC classes); label provenance.

---

## Phase 2 — STATS page (`STATS` header button, same toggle pattern as LIVE)

Left: date range (default last 365 d), class floor, optional AR, `RUN`. Right: S1–S5.
Event list = existing `flares` source (HEK/SWPC); cache per month.

- **S1 Rates**: monthly C/M/X counts (stacked) + monthly sunspot number
  (`observed-solar-cycle-indices.json`) on a second axis.
- **S2 Frequency distribution**: peak-flux histogram (log bins) + complementary CDF; power-law
  index by MLE with x_min chosen by KS minimisation (Clauset, Shalizi & Newman 2009);
  bootstrap 1σ. Show fitted line and α. Note C-class incompleteness at high background
  (detection threshold rises with background) — x_min handles it; say so.
- **S3 Superposed epoch** (idea 5): choose flares (class range / AR / explicit list, cap 200) →
  GOES XRS (NCEI 1-min, cached) ±N min around peak, t=0 at peak. Modes: raw · peak-normalised ·
  pre-flare-background-subtracted. Median + IQR band + faint individual curves. Optional second
  channel: GOES EUVS Lyman-α for the same epochs. Derived distributions: rise time, e-folding
  decay time, duration.
- **S4 Forecast verification** (idea 6): NOAA day-1 M and X probabilities vs observed
  (≥1 flare of that class in the target UT day, from the GOES event list).
  Sources: `solar_probabilities.json` (30 d) + RSGA archive for history (Part III event
  probabilities). **Align validity carefully**: RSGA is issued ~2200 UT; its day-1 is the next
  UT day. Metrics: reliability diagram (bins of 10 %, counts shown), Brier score and Brier skill
  score vs climatology (base rate over the window), ROC + AUC. Baselines: climatology and
  persistence (flare of class yesterday → yes). Optional per-region version using
  `solar_regions.json` probabilities (30 d only unless logged).
- **S5 Region productivity**: top regions by flare index (C=1, M=10, X=100 × magnitude);
  flare rate by McIntosh class and delta vs non-delta. Rows click → AR tracker.

Opus to review S2/S4 maths before merge.

### Phase 2 status (2026-11-25)
Backend done (`core/stats.py`, tests/test_stats.py). Data source changed from HEK to SWPC
daily archives (Edited Events / RSGA / SRS via NCEI) — faster, has satellites and regions.
**UI done 2026-11-25** (index.html, `STATS` module; frac-of-region-days with Wilson CI is plotted in S5 because the backend CI belongs to `frac`, not `rate`). API contract:

`POST /api/stats/stream` `{start, end: "YYYY-MM-DD", floor: "C1.0", panels: [rates, dist,
verify, regions], rescale: false, lead: 1, verify_class: "M"}` → NDJSON
`{event:"progress", what: events|rsga|srs, done, total}` … then
`{event:"result", start, end, n_events, coverage_note, era_note?, floor, rescale, panels}`;
`{event:"error", error}` on failure; 422 JSON for bad input. Every panel may be `{error}`.
- `rates`: months[], counts{B?,C,M,X: []}, coverage[] (0–1), ssn[], f107[], note
- `dist`: n, alpha, sigma, alpha_boot_sd, xmin, xmin_class, ks, n_tail, gof_p,
  hist{edges, counts, density}, ccdf{x, p}, model{x, density, ccdf}, note
- `verify`: n, events, base_rate, brier, brier_clim, bss, reliability, resolution,
  uncertainty, bins[{lo, hi, n, mean_p, freq, ci[2]}], roc[{pofd, pod, threshold}], auc,
  tss_max, tss_threshold, persistence{n, brier, pod, pofd}, series{days, p, o}, lead, class, note
- `regions`: table[{region, fi, n{C,M,X}, largest, largest_peak, first, last, max_area,
  mcintosh_at_max, delta, mag_max, days_on_disk, query}], n_regions, unassigned,
  rates{zurich[], mcintosh[], mag[]: {group, region_days, C|M|X: {rate, poisson_p, frac, ci}}, note}

`POST /api/stats/epoch/stream` `{start, end, class_min: "M1.0", class_max, region,
before_min: 60, after_min: 120, channel: xrsb|xrsa|lya, align: curve|catalogue, limit: 50
(≤200), exclude_overlaps: true}` → progress (`events`, then `epoch` per flare) → result
`{minutes[], flares[{peak, class, region, sat, offset_s, flux_peak, background, curve[],
rise_min, duration_min, decay_efold_min}], stats{raw|norm|bgsub: {q25, median, q75, n}},
derived{rise_min[], duration_min[], decay_efold_min[]}, matched, overlaps_dropped, used,
largest_first, skipped[], label, note, coverage_note}`. Needs sunpy (Mac); untested live.

UI to build (same pattern as LIVE: header `STATS` button, `body[data-mode]`, `#stats`;
LIVE alert polling must keep running):
- Left: start/end dates (default last 365 d to yesterday), floor (B1/C1/M1), rescale,
  panel ticks, RUN with progress "events 120/366"; epoch box with the fields above, RUN EPOCH.
- S1: stacked monthly bars C/M/X + sunspot number on y2; dim months with coverage < 1.
- S2: log-log density histogram + model from x_min; CCDF + model; line "α = a ± boot_sd
  above xmin_class (n_tail of n), GOF p". Show note.
- S3: median + IQR band + faint individual curves; mode toggle raw (log y) / norm / bgsub;
  histograms of rise, decay, duration; list skipped.
- S4: reliability diagram (mean_p vs freq, CI bars, diagonal, base-rate line, bin counts),
  ROC with persistence point and AUC; score table (BS, BS_clim, BSS, REL, RES, UNC, TSS);
  lead/class selectors re-run only `verify`.
- S5: region table (row click → regular view with `query`); bar charts of M and X rate by
  Zurich and magnetic class with CI; McIntosh table.
- Show era_note / coverage_note as a banner; every panel's note under it.

---

## Phase 3 — AR tracker upgrade (idea 3)

Extends existing `solar_regions._track` ("track AR 14503").
- Flare history for the AR (flares source filtered by AR) marked on the track and as a class
  timeline.
- Magnetic-class timeline; highlight delta onset.
- HMI SHARP via JSOC `jsoc_info` (`hmi.sharp_cea_720s`; NOAA→HARP via JSOC
  `all_harps_with_noaa_ars.txt`): USFLUX, TOTUSJH, R_VALUE, MEANSHR, TOTPOT, AREA_ACR; 12-min
  cadence downsampled to 1 h; flares as vertical lines. Shade intervals with |CM distance|
  > 60° (projection makes SHARPs unreliable).
- Daily cut-out strip (AIA 171 + HMI magnetogram) following the region with existing
  `synodic_rate`.

### Phase 3 status (2026-11-25)
Backend done: `core/artrack.py`, called from `solar_regions._track` (extras `enrich: false`
skips it). Live-checked on AR 12673 (Sep 2017: 85 flares incl. X9.3/X8.2, δ onset 09-04,
HARP 7115 definitive) and AR 14579 (Nov 2026: near-real-time HARP 14206). Each block may be
`{error}` on its own. x, y are solar radii in the same frame as `regions` rows and `track`.
- `flares`: {list[{peak, begin, end, class, flux, sat, x, y}] (x/y null past the limb),
  counts{B,C,M,X}, fi, largest, largest_peak, start, end, note}. Source: SWPC Edited Events
  region numbers, window = summary days read ±1 day.
- `mag_timeline`: [{valid, mag, mcintosh, delta, area, spots}]; `delta_onset`: date | null.
- `sharp` (absent before 2010-05): {harp, noaa, series (definitive | near-real-time | both),
  nrt_from, shared_with[], other_harps[], times[] (UTC, hourly), lon[] (LON_FWT), quality[],
  params[{key, label, unit, log, values[]}] for USFLUX, TOTUSJH, TOTPOT, R_VALUE, MEANSHR,
  AREA_ACR, far[[t0, t1]] (|lon| > far_lon = 60°), note}. Selected by
  `[? NOAA_ARS like '%1NNNN%' ?]` (no HARP map file); records with USFLUX = 0 → null.
  QUALITY ≠ 0 records are kept and counted (all NRT records carry 0x400) — Harry to confirm.
- `cutouts`: {fov (400″), frames[{day, time (SRS 2400 UT instant), x, y (arcsec), lon,
  images[{label, url (256 px), full_url (1024 px)}]}], note}. AIA 171 + HMI magnetogram;
  EIT 195 + MDI magnetogram before 2010-05-13.

**UI done 2026-11-25** (`renderTrackExtras`, `plotFlareTimeline`, `plotSharp` in index.html; M/X lines also on the area/spots panels, coloured by class). Built to:
- Disk map: flares on the track at (x, y), marker size/colour by class (C cyan, M yellow,
  X red), hover class + peak UT.
- Flare timeline: peak flux vs time, log y with A–X bands (as GOES plots), coloured by class;
  line "C n · M n · X n · FI · largest"; note.
- Magnetic class strip: one cell per day (α, β, βγ, βγδ …), δ days red, "δ onset" label,
  McIntosh class as text.
- SHARP: six small panels sharing the time axis (log y where `log`), `far` intervals shaded
  grey, M/X flares as dotted vertical lines, `nrt_from` marked; note; error line if `{error}`.
- Cut-out strip: horizontal row of daily pairs (EUV above, magnetogram below), lazy-loaded,
  click opens `full_url`; day + longitude label.
- Existing area/spots trend panels: same M/X flare lines.
- Any block's `{error}` shows as one line in its place.

---

## Phase 4 — Multi-viewpoint (idea 4) — feasible; much already exists

Existing: `solo_eui`, `solo_stix`, `positions`. Add:
- `stereo_euvi` source via Helioviewer (STEREO_A SECCHI EUVI 195/284/304/171).
- Event view "VIEWPOINTS" block: from the flare's Stonyhurst lat/lon and each observer's
  position, flag on-disk / limb / occulted for Earth, SolO, STEREO-A; auto-panel AIA,
  EUI FSI and EUVI-A at peak.
- STIX light curve **shifted by light-travel-time difference to 1 AU** before overlaying on
  GOES. For far-side flares show the STIX Data Center's estimated GOES class.
- Optional: PUNCH (Helioviewer) for CME context.

### Phase 4 status (2026-11-26) — done (backend + UI)
- `sources/stereo_euvi.py`: Helioviewer EUVI-A 171/195/284/304 (ids 20–23), from 2006-12.
- `core/viewpoints.py` + `sources/viewpoints.py` (kind `viewpoints`): flare site from HEK
  (hpc → Stonyhurst, orthographic) or, with no HEK position, the region's latest SRS position
  rotated to the peak (past the limb allowed). Observers: Earth (astropy), Solar Orbiter and
  STEREO-A (Horizons, cached per 10 min). Per observer: θ from disk centre, status on disk
  (<70°) / near limb / behind limb (<20° past θ_L = acos(R/D); height that clears the limb
  h = R(1/cos(θ−θ_L) − 1)) / far side; light lead vs Earth. Unplaced flares still get
  positions + STIX.
- STIX: the quicklook light curve was already light-time corrected by the Data Center
  (`ltc=true`); provenance now states the shift (DELTA_LIGHT_TIME). Flare list
  (`/api/request/flare-list`) times are Solar Orbiter's (checked: list peak 17:04:08 vs
  uncorrected LC max 17:03:44, corrected 17:06:01) → shifted by the light lead. Shows GOES
  seen + STIX-estimated GOES class (mean, min–max).
- Event view (`events.expand`, flares since 2006-10): adds `viewpoints`, plus EUI FSI 174 /
  EUVI-A 195 to the image grid when that spacecraft has the site on disk/limb/behind limb and
  Helioviewer has an image within 1 h of the peak.
- Fixed: "flare at T" looked up the flare nearest T + 30 min (window midpoint); now T.
- PUNCH: not done (optional).

---

## Model split
Phase 1, 4 and S1/S3/S5 UI: Sonnet. S2/S4 maths, alignment rules and Phase 3 science:
spec'd here; Opus reviews before merge.
