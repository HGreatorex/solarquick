# SolarQuick

Quicklook webapp for solar & ionospheric physics data. Type a request, get a plot.

> "show me the GOES SXR data for the 10th of July 2026"

## Install & run

```bash
pip install -e .
solarapp            # or: python -m solarapp
```

Open http://localhost:8000 and type a query.

## Adding a data source

Copy `solarapp/sources/goes_xrs.py`, rename class/`name`/`keywords`, implement
`fetch(start, end) -> PlotSpec`. Done — it is auto-discovered and the parser
learns its keywords. See `ARCHITECTURE.md`.

## Status

- [x] Core: plugin registry, rule-based query parser, FastAPI server, Plotly UI
- [x] GOES XRS source (NCEI archive via sunpy/Fido + SWPC realtime JSON for last 7 days)
- [x] SDO images (AIA 94–4500 Å + HMI magnetogram/continuum via Helioviewer;
      e.g. "sdo aia 304 yesterday", "hmi magnetogram 2026-08-01")
- [x] SOHO EIT + LASCO C2/C3, PROBA2 SWAP, Solar Orbiter EUI (Helioviewer images)
- [x] RHESSI, Fermi GBM, PROBA2 LYRA, SolO STIX lightcurves
      ("rhessi 2003-10-28", "gbm n3 2022-04-20", "solar orbiter stix yesterday")
- [x] Wind/WAVES radio dynamic spectra ("wind waves 2026-07-11")
- [x] SDO EVE L2 lines & spectra ("eve he ii 304 yesterday", "eve spectrum 2026-08-05")
- [x] GOES EUVS lines + MgII, primary-satellite default, multi-panel
      ("euvs yesterday", "goes-16 euvs lyman alpha 2023-01-01")
- [x] Hinode SOT filtergrams via VSO ("hinode sot 2013-11-15")
- [x] Multi-source queries ("sdo aia 304 and goes xrs yesterday", "goes, stix
      and lasco c2 for 2026-07-10") — concurrent fetch, stacked results

- [x] Synoptic positions: Mercury–Mars + PSP/SolO/STEREO-A/BepiColombo
      ("positions today", "where is psp from 2026-06-01 to 2026-08-01")
- [x] Earth illumination: terminator + twilight bands + subsolar point/track
      ("terminator today", "day night map from 2026-06-01 to 2026-07-01")
- [x] Flare events from HEK ("flares yesterday", "x-class flares in july 2026",
      "biggest flare since 2014") — event tables, flare markers on timeseries,
      and event views that auto-compose XRS + AIA 131/304 cropped to the
      flare site + STIX
- [x] Crops: time-of-day ("goes xrs 2026-07-10 14:00 to 15:30", "yesterday at
      14:30") and spatial image crops around a target
- [x] Typo tolerance ("goess xsr yesterdy", "bigest flare sicne 2014") with the
      interpretation always shown back to you
- [x] Free-form query understanding — optional Claude fallback for phrasings the
      rules can't read ("the big x-ray blast during the halloween storms").
      Install with `pip install -e '.[llm]'` and set `ANTHROPIC_API_KEY`; without
      a key the app is unchanged. The rewrite it produces is always shown to you.
- [x] Export: every result block gets **PNG**, **CSV** and **Data (JSON)**
      buttons (image results get "Open frame" instead of CSV). CSVs carry the
      query, provenance and — where the source bins high-cadence data — a note
      saying so, so a file always states what it actually contains.
- [ ] Hosted deploy
