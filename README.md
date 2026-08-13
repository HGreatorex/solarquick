# SolarQuick

**A quicklook browser for solar physics data. Type what you want in plain
English, get a plot.**

> "show me the GOES SXR data for the 10th of July 2026"
> "sdo aia 304 and goes xrs yesterday"
> "biggest flare since 2014"

SolarQuick runs as a small web app on your own machine. It fetches from the
public science archives (NOAA, Helioviewer, LASP, NASA SPDF, LMSAL/HEK, JPL) on
demand — there is no database to load and nothing to configure. 16 data sources
are built in, and a plain-English query picks the instrument, the time range and
the options.

> **Beta.** This is a working tool under active development, shared for testing.
> Expect rough edges, and please report them (see [Reporting problems](#reporting-problems)).
> It produces **quicklook** views, not calibrated science products — see
> [What this is and isn't](#what-this-is-and-isnt).

---

## Contents

- [Requirements](#requirements)
- [Install](#install) — **start here**
- [Running it](#running-it)
- [What you can ask for](#what-you-can-ask-for)
- [Optional: smarter query understanding](#optional-smarter-query-understanding)
- [Exporting data](#exporting-data)
- [What this is and isn't](#what-this-is-and-isnt)
- [Troubleshooting](#troubleshooting)
- [Reporting problems](#reporting-problems)
- [Adding a data source](#adding-a-data-source)

---

## Requirements

- **Python 3.10 or newer.** Check with `python3 --version`. If you don't have
  it, install from [python.org](https://www.python.org/downloads/) or via your
  package manager (`brew install python`, `sudo apt install python3 python3-venv`).
- **Git**, to clone the repository (or download a ZIP from GitHub instead).
- An internet connection — all data is fetched live from the archives.
- About **1.5 GB of disk** for the Python packages, plus space for cached data
  files as you use it (typically tens of MB).

### About the Python packages

SolarQuick depends on the scientific Python stack — **sunpy, astropy, numpy,
pandas, matplotlib, astroquery** and about fifty of their own dependencies.
**You almost certainly do not have all of these already, and the installer will
need to download and install them.** That's expected and normal.

**Install into a virtual environment**, as shown below. This is not just good
practice — it matters here, because SolarQuick pulls in specific versions of
large scientific packages, and installing those into your system Python (or into
a shared conda environment you use for other work) can upgrade or downgrade
packages that your other projects depend on. A virtual environment is a private
folder that keeps all of this isolated; deleting the folder removes everything
cleanly.

The first install downloads a few hundred MB and typically takes **2–5 minutes**.

---

## Install

Pick whichever matches your setup. **The venv route (A) is recommended** unless
you already live in conda.

### A. With a virtual environment (recommended, works everywhere)

```bash
git clone https://github.com/HGreatorex/solarquick.git
cd solarquick
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

On **Windows** use PowerShell, and activate with `.venv\Scripts\Activate.ps1`
instead of the `source` line.

You'll know the environment is active because your prompt gains a `(.venv)`
prefix. **You need to activate it in each new terminal** before running
SolarQuick:

```bash
cd solarquick && source .venv/bin/activate
```

### B. With conda / mamba

```bash
git clone https://github.com/HGreatorex/solarquick.git
cd solarquick
conda create -n solarquick python=3.12 -y
conda activate solarquick
pip install .
```

Using a **dedicated** environment (rather than `base` or an existing one) keeps
SolarQuick's dependency versions away from your other work.

### C. Without Git

Download the ZIP from the
[repository page](https://github.com/HGreatorex/solarquick) (green **Code**
button → **Download ZIP**), unzip it, then follow route A from the `cd` step
onwards.

### Verify the install (optional)

```bash
pip install '.[dev]'
python -m pytest tests -q
```

All tests run offline — they never touch the archives — so this should pass in a
few seconds and confirms the install is sound. The `[dev]` extra adds `pytest`
and `httpx`, which are only needed for running the tests, not for using the app.

If you skip this, just go straight to [Running it](#running-it).

---

## Running it

```bash
solarquick
```

That's it. It starts a local server and opens your browser at
<http://localhost:8000>. Press **Ctrl-C** in the terminal to stop it.

Equivalent, if you prefer: `python -m solarapp`.

Useful flags:

| Flag | What it does |
|---|---|
| `--port 8123` | Serve on a different port. If the default 8000 is busy, SolarQuick moves to the next free port automatically and tells you. |
| `--no-browser` | Don't open a browser window. |
| `--host 0.0.0.0` | Serve to your network. **Only on a network you trust** — there is no authentication. |

SolarQuick binds to localhost by default, so it is reachable only from your own
machine.

---

## What you can ask for

Type a source, optionally some options, and a time. Examples that work:

**Timeseries**
```
goes sxr yesterday
goes xrs 2026-07-10 14:00 to 15:30
rhessi 2003-10-28
solar orbiter stix yesterday
gbm n3 2022-04-20
lyra last 3 days
eve he ii 304 yesterday
euvs yesterday
goes-16 euvs lyman alpha 2023-01-01
```

**Images**
```
sdo aia 304 yesterday
aia 171 on 10 July 2026
hmi magnetogram 2026-08-01
lasco c3 2026-08-01
soho eit 195 yesterday
proba2 swap yesterday
solar orbiter eui yesterday
hinode sot 2013-11-15
```

**Radio spectra**
```
wind waves 2026-07-11
type iii burst yesterday
```

**Flares** (from the HEK/SWPC catalogue)
```
flares yesterday
x-class flares in july 2026
biggest flare since 2014
flare at 2017-09-06 12:02
```
A single flare opens an **event view**: the light curve plus AIA 131/304 cropped
to the flare site, and STIX if Solar Orbiter was flying. Clicking a row in a
flare table opens that flare's event view.

**Several sources at once**
```
sdo aia 304 and goes xrs yesterday
goes, stix and lasco c2 for 2026-07-10
aia 171 and aia 304 yesterday
```

**Context**
```
positions today
where is psp from 2026-06-01 to 2026-08-01
terminator today
day night map from 2026-06-01 to 2026-07-01
```

**Time can be written** as a date (`2026-07-10`, `10 July 2026`), a range
(`from 2026-07-01 to 2026-07-08`), a month (`july 2026`), a year (`2024`),
`today` / `yesterday`, `last 3 days`, `past 2 weeks`, `since 2014`, or a day
plus a time (`yesterday at 14:30`).

**Typos are tolerated.** `goess xsr yesterdy` works, and SolarQuick tells you
what it read your query as rather than silently guessing.

---

## Optional: smarter query understanding

For phrasings the rules can't read — *"the big x-ray blast during the halloween
storms"* — SolarQuick can ask Claude to restate your request in its own query
syntax. This is **entirely optional and off by default.**

```bash
pip install '.[llm]'
export ANTHROPIC_API_KEY=sk-ant-...        # your own key
solarquick
```

Notes:
- Without a key nothing changes; the app behaves exactly as before.
- It only runs when the ordinary rules *fail*, so normal queries never involve
  it and never cost anything.
- The rewrite is always shown to you, so you can see how your request was
  interpreted.
- Uses your own API key and your own account. Set `SOLARQUICK_LLM=0` to force it
  off even with a key present.

> This part is **not yet verified against the live API** — it is covered by
> tests with the API stubbed, but had not been exercised with a real key at the
> time of writing. Please report what happens if you try it.

---

## Exporting data

Every result block has its own download buttons:

- **PNG** — the figure as displayed.
- **CSV** — the plotted values, with a header recording your query, the data
  provenance, and (where the source bins high-cadence data) a note saying so, so
  a file always states what it actually contains.
- **Data (JSON)** — the full result including metadata.
- Image results offer **Open frame** instead of CSV, since there's no table
  behind a picture.

---

## What this is and isn't

**It is** a fast way to look at solar data across many instruments without
writing code or knowing each archive's layout.

**It isn't** a source of calibrated science products:

- **Images are browse products.** Helioviewer-backed frames are rendered from
  JPEG2000 browse images — right for "what did the Sun look like", not for
  photometry.
- **High-cadence timeseries may be binned.** RHESSI, Fermi GBM and PROBA2 LYRA
  are mean-resampled to keep plots responsive. CSV exports state when this has
  happened.
- **Archives lag and wobble.** The NOAA NCEI science archive is often days
  behind real time and returns intermittent errors; SolarQuick uses NOAA SWPC's
  realtime feed for recent GOES data instead. A query that works may fail an
  hour later through no fault of yours.
- **Flare positions are sometimes missing.** The SWPC catalogue frequently omits
  a location; event views fall back to full-disk images when it does.

For publication work, go to the archives and instrument teams directly.

---

## Troubleshooting

**`command not found: solarquick`**
Your virtual environment isn't active. Run `source .venv/bin/activate` (from
inside the `solarquick` folder) and try again. Or use `python -m solarapp`.

**`pip: command not found` or pip installs to the wrong place**
Use `python3 -m pip` instead of bare `pip`.

**Install fails building a package from source**
Usually an old pip. Run `pip install --upgrade pip` and retry.

**"Port 8000 is in use; using 8001 instead."**
Not an error — something else is on 8000, so SolarQuick moved. Use the URL it
prints.

**"No data source recognised"**
The query didn't match a known instrument. The message lists what's available.
Check the [examples above](#what-you-can-ask-for).

**"No GOES XRS files found on NCEI for this interval"**
Usually the archive, not you — NCEI lags real time and is intermittently
unavailable. Try a different date, or the same one later.

**The first `positions` query takes several seconds**
Expected once: astropy downloads Earth-orientation data and astroquery contacts
JPL Horizons. Later ones are fast.

**A query is slow the first time and instant afterwards**
Expected. Results are cached (briefly for recent windows, longer for historical
ones), and downloaded archive files are kept. Set `SOLARQUICK_CACHE=0` to
disable result caching if you want to be certain you're seeing a fresh fetch.

**It says "cached" and I think the data is stale**
`DELETE http://localhost:8000/api/cache` clears it, or restart with
`SOLARQUICK_CACHE=0`.

**Removing SolarQuick**
Delete the `solarquick` folder. With a venv, that removes every installed
package too. Cached data lives in `~/.cache/solarapp` and `~/sunpy` — delete
those to reclaim the space.

---

## Reporting problems

Please open an issue: <https://github.com/HGreatorex/solarquick/issues>

The single most useful thing you can include is **the exact query you typed**,
plus:

- What you expected versus what happened (a screenshot helps).
- The timing/provenance line under the plot — it says which source was used and
  how long each part took.
- Anything printed in the terminal where SolarQuick is running.
- Your OS and `python3 --version`.

Queries that produce a *wrong-looking but plausible* result are the most
valuable of all — a confidently wrong answer is worse than an error, and that
class of bug is the hardest to find without fresh eyes.

---

## Adding a data source

One file, no registration:

```python
# solarapp/sources/my_instrument.py
from solarapp.core.registry import DataSource, PlotSpec

class MyInstrument(DataSource):
    name = "my_instrument"
    display_name = "My Instrument"
    keywords = ["myinst", "my instrument"]

    def fetch(self, start, end, **extras) -> PlotSpec:
        ...
```

Drop it in `solarapp/sources/` and it's auto-discovered; the query parser learns
its keywords automatically. Copy `solarapp/sources/goes_xrs.py` as a template
and read [`ARCHITECTURE.md`](ARCHITECTURE.md) — that plus `core/registry.py` is
the whole mental model.

Please verify an archive's API by hand before writing a plugin against it;
several have moved or lag, and `ARCHITECTURE.md` records the traps.

---

## Licence

MIT — see [LICENSE](LICENSE). Built on
[sunpy](https://sunpy.org), [astropy](https://astropy.org) and the public data
archives of NOAA, NASA, LMSAL, LASP, ESA and JPL; please cite those projects and
instrument teams in any work that uses their data.
