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

---

## Model split
Phase 1, 4 and S1/S3/S5 UI: Sonnet. S2/S4 maths, alignment rules and Phase 3 science:
spec'd here; Opus reviews before merge.
