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
- [Managing disk usage](#managing-disk-usage)
- [Reporting problems](#reporting-problems)
- [Adding a data source](#adding-a-data-source)

---

## Requirements

- **Python 3.10 or newer.** Check with `python3 --version`. If you don't have
  it, install from [python.org](https://www.python.org/downloads/) or via your
  package manager (`brew install python`, `sudo apt install python3 python3-venv`).
- **Git**, to clone the repository (or download a ZIP from GitHub instead).
- An internet connection — all data is fetched live from the archives.
- About **400 MB of disk** for the Python packages, plus space for cached data
  files. Be aware that the data cache **grows without limit** as you use the
  app — heavy exploration across many dates and instruments can reach several
  GB. See [Managing disk usage](#managing-disk-usage).

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

**Historical GOES** reaches back well before the GOES-R satellites, from the
NOAA NCEI science archive:
```
euvs 2014-06-10                       # GOES 13-15 EUVS, from 2010
goes-14 euvs 2015-06-01               # the standby spacecraft, by name
goes xrs high resolution 2014-06-10   # 2 s XRS instead of 1-min averages
goes-12 xrs 2003-10-28                # pick the spacecraft yourself
```
Two things are worth knowing about this era. **Pre-2017 EUVS is Lyman-α
only** — the archive's 30.4 nm channel exists but every value in it is empty,
so for 30.4 nm before 2017 use SDO EVE. And the NCEI **science-quality XRS
fluxes are true irradiances**, roughly 1.4–1.5× the SWPC-scaled values the
GOES flare classes were defined on, so the A/B/C/M/X bands on the plot
overstate a pre-2017 flare's class by about half a letter step.

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
`today` / `yesterday`, `now`, `latest`, `3 days ago`, `this week`,
`this month`, `last 3 days`, `past 2 weeks`, or `since 2014`.

**A time of day narrows any single day**, in whichever way you write it:
```
aia 171 yesterday at 14:30
aia 193 on 2026-08-01 between 10:00 and 12:00
aia 304 today 10am-2pm
goes xrs 2026-07-10 14:00 to 15:30
lasco c3 2026-08-01 around 09:00
goes 2003-10-28 22:00 to 02:00      # crossing midnight is understood
```

**Everyday wording works**, not just instrument names — `xrays yesterday`
finds GOES XRS, `radio today` finds Wind/WAVES. Hyphens, spaces and plurals
don't matter: `x-ray`, `x ray`, `xray` and `xrays` are all the same word.

**Ambiguous terms are questioned, not guessed.** `euv today` could be five
different instruments, so SolarQuick lists them and asks which you meant
rather than picking one.

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

**"could mean several instruments"**
Not an error either — the term you used belongs to more than one instrument
(`euv` is on AIA, EIT, EUI, SWAP, EVE and EUVS). Name the one you want.
SolarQuick asks rather than picking, so it can't quietly plot the wrong thing.

**`No module named 'mpl_animators'`**
An install from before this dependency was declared. Update and reinstall:
```bash
git pull && pip install --upgrade .
```
It affects every sunpy-backed source (archival GOES XRS, RHESSI, LYRA, EVE,
Fermi GBM, Hinode SOT); image sources are unaffected.

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

### Managing disk usage

Downloaded archive files are cached so repeat queries are fast, and **nothing
prunes them automatically**. Two locations:

| Path | What's in it |
|---|---|
| `~/sunpy/data` | Files fetched via sunpy/Fido — GOES, RHESSI, Fermi GBM, LYRA, Hinode. This is the one that grows; it is shared with any other sunpy work on your machine. |
| `~/.cache/solarapp` | Files SolarQuick downloads directly — Wind/WAVES, SDO EVE, GOES EUVS. |

Check the size and clear them when you like — the app will simply re-download
what it needs:

```bash
du -sh ~/sunpy/data ~/.cache/solarapp
rm -rf ~/.cache/solarapp          # SolarQuick's own cache, safe to delete
rm -rf ~/sunpy/data               # only if you don't use sunpy for other work
```

**Removing SolarQuick**
Delete the `solarquick` folder. With a venv, that removes every installed
package too, then clear the cache directories above.

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
