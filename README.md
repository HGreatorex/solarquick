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
- [ ] More sources (SDO/EVE, STIX, ionosonde/TEC...)
- [ ] Multi-source overlay, HEK event annotations
