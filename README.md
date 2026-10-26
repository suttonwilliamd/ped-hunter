
# PED Hunter

Free, local-first Entropia Universe profit intelligence.

## Philosophy

- free for everyone forever
- donations welcome, no paid API or account wall
- local-first by default
- modern, simple, and extensible

## What it does

- tracks Entropia chat logs for loot, combat, crafting, and skill events
- stores sessions in SQLite
- seeds item/resource/crafting catalogs from the legacy LootNanny data files
- resolves community shorthand like `Frontier Rifle` to the actual starter weapon `Frontier Hunting Rifle`
- computes derived values like cost per shot from the item catalog

## Interface direction

PED Hunter borrows LootNanny's best basic idea — a run-centric Entropia tracker that follows chat logs and summarizes loot, combat, skills, and crafting — but updates it into a cleaner local-first desktop cockpit:

- dashboard summary cards for live loot, combat damage, event count, stored sessions, and tracking state
- clear start/stop controls with visible chat-log status
- recent session history backed by SQLite
- live event stream for parsed chat lines
- LootNanny-style session skill-gain summary with total XP, procs, and proc share by skill
- loadout builder inspired by LootNanny's Config flow so hunt costs are based on weapon, amp, scope/sights, and enhancers
- catalog search with weapon cost/shot details and aliases
- setup guidance for first-time users

The main window uses one space-efficient layout by default (1040×640), with tighter controls and a side-by-side profit/return summary; there is no separate Compact mode. Lifetime totals live in **Statistics**, leaving **Dashboard** focused on run history. Saved runs do not populate the live summary until you explicitly start or resume a run.

The goal is to keep the useful parts of LootNanny's workflow while avoiding clutter and making room for richer modern catalog sources.

## Quick start

Download the Windows `.exe` from the latest GitHub release, or run from source:

Running `PED-Hunter.exe` with no arguments opens the dashboard. Command-line subcommands are still available from a terminal.

1. Install Python **3.11 or newer** (including Tkinter; the standard python.org Windows installer includes it). From the repository root, create an isolated environment and install the project. PowerShell activation is not required:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m ped_hunter doctor
.\.venv\Scripts\python.exe -m ped_hunter gui
```

Use the installed environment's Python for the commands below (on Windows,
replace `python` with `.\.venv\Scripts\python.exe`). Installing the project supplies
the declared `watchdog>=6.0.0` runtime dependency and the `ped-hunter` entry point.

2. Create the normalized catalog:

```bash
python tools/sync_legacy_data.py
```

3. Launch the dashboard:

```bash
python -m ped_hunter gui
```

4. Watch a log file:

On Windows, the dashboard uses Windows' configured Documents folder, including OneDrive or domain redirection. For command-line monitoring, use PowerShell to resolve that same location:

```powershell
$chatLog = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Entropia Universe\chat.log'
ped-hunter monitor --chat-log "$chatLog"
```

PED Hunter buffers a line until its newline arrives, including split UTF-8
writes, and follows log truncation/replacement without treating byte offsets as
text-mode seek cookies. Start/Resume still skips existing history. The source
log is never modified. After 30 seconds with no new bytes, the status line warns
that the file is not changing; if you are actively playing, check in-game chat
logging and the selected path. No file changes alone do **not** prove a parser
failure or a disconnected game. New data clears the warning automatically.

5. Ask for stats:

```bash
ped-hunter stats
```

6. Look up a weapon:

```bash
ped-hunter weapon "Frontier Rifle"
```

## Read-only setup diagnostics

```powershell
.\.venv\Scripts\python.exe -m ped_hunter doctor --json
.\.venv\Scripts\python.exe -m ped_hunter doctor --chat-log 'C:\path\you\explicitly\selected\chat.log'
```

`ped-hunter doctor` checks Python >=3.11, Tkinter **import only** (no window),
and the declared watchdog dependency's module presence and installed version
(>=6.0.0). It uses only the standard library and does not import watchdog itself.
The conservative version allowlist accepts numeric releases and `a`/`b`/`rc`
suffixes, not complete PEP 440 syntax. A compatible post/local build such as
`6.0.0.post1` or `6.0.0+vendor.1` currently produces a failed check with a null
version; that result alone does not establish that the installed package is too old.
An optional explicit `--chat-log PATH` checks a regular file can be opened for
reading, then closes it **without reading any bytes**. No log is selected by
default; there is no home scan or game-data discovery. It does not initialize
SQLite, touch catalogs, acquire the GUI lock, start monitoring/GUI, download seeds,
or make explicit network requests. Select a trusted local file: the operating
system can perform network filesystem access for UNC or network-mounted paths.

Exit 0 means **only selected checks passed**; 1 means at least one failed;
2 means invalid command usage. Missing, non-file and unreadable paths fail.
Import/file checks cannot prove display health, game connectivity, or log-format
health, nor constitute native GUI acceptance. An ACL/file change after the check
can still prevent subsequent monitoring.

JSON schema version 1 has `schema_version`, overall `status` (`pass`/`fail`),
`checks`, and `limitation`. Each check has `name`, `status`, fixed `detail`, and
`version` (sanitized version or null). No executable/home/log paths, usernames,
environment values, raw exceptions, or chat contents are emitted. Share **only
reviewed doctor output** for support; do not paste the command line (which may
contain your private path), game logs, environment dumps, or crash logs. Diagnostics
intentionally omit private error detail rather than masking failure status.

## Frozen Windows diagnostics qualification (not a release)

The `Windows frozen diagnostics / Python 3.13` public Actions job builds
`PED-Hunter.spec` in an ephemeral Windows runner. Build tooling is
pinned to PyInstaller **6.16.0** (verified PyPI Python 3.13 support and Windows
amd64 wheel); watchdog **6.0.0** avoids the doctor's documented version-syntax
limit. The instrumentation does not itself establish runtime repair.
For an equivalent build in a disposable Windows Python 3.13 environment:

```powershell
python -m pip install . "watchdog==6.0.0" "pyinstaller==6.16.0"
python -m PyInstaller --clean --noconfirm PED-Hunter.spec
python tools/frozen_doctor_smoke.py dist/PED-Hunter.exe --report frozen-evidence/report.json
```

The portable stdlib helper accepts the actual executable, never falls back to
source Python, and runs only `doctor --json` and `doctor --json --chat-log` with
a synthetic local fixture. Each invocation has a 60-second timeout. It requires
exit 0, exact schema 1, passing Python 3.13/Tkinter/watchdog checks, strictly
allowlisted redacted values, empty stderr, unchanged fixture bytes and no new
application files/directories. HOME, USERPROFILE, APPDATA, LOCALAPPDATA, cwd and
temporary paths are isolated. The existing spec's cwd `_MEI*` one-file extraction
is classified separately from application state and must be cleaned on exit.
A surviving extraction directory also fails the probe; it is not labeled a DB
or catalog defect. Temporary child output is polled with a 16 KiB limit per
stream and never copied into reports or artifact uploads.

CI blocks this executable's outbound traffic using an ephemeral runner firewall
rule before invoking it (rule failure fails the job). The helper itself is not
a network/GUI API tracer or an OS sandbox; this gate neither exercises GUI/game
commands nor proves absence of attempted network calls. Use the manual invocation
only in an equally disposable, network-restricted environment, never against a
real game profile. It does not prove native display/game/log-format health.

CI uploads only a bounded sanitized boolean/check-status JSON report, including
explicit failures, and uploads the tested EXE **only on success**. Both expire
after **one day**. Build/setup failures retain fixed CI-stage outcomes instead
of raw errors. No tag, release, signing, deployment or publication job is added.
Existing source test job names and tests are retained; helper fake-executable
unit tests are **not** frozen Windows acceptance.

The first actual Windows Python 3.13 run, **40266034306** at commit `32c0963`,
failed both frozen doctor calls: Python/Tkinter passed, watchdog failed, and the
explicit synthetic-log check passed. Install/build/firewall setup succeeded;
both calls returned valid redacted JSON, and the EXE was correctly not uploaded.
The sanitized report does not distinguish missing module from missing metadata.
Subsequent read-only PyInstaller **6.16.0 Linux static graph** inspection of
`32c0963` found doctor/app/LogTail but neither the unused `log_watcher` nor
`watchdog`. It also found automatic collection of `watchdog-6.0.0.dist-info`
from the literal metadata version query. Recognizing `find_spec('watchdog')` in
bytecode does not automatically collect that package. Thus the earlier
**missing-metadata-only hypothesis is insufficient**; `2cce4bf` is not a proven
runtime repair. This graph is not Windows archive-membership or runtime proof.

The bounded corrective candidate explicitly requests the **real `watchdog`
package** in Analysis `hiddenimports`, matching doctor's dynamic module-presence
check without importing it in doctor or changing version policy. It retains
`copy_metadata('watchdog')` for deterministic direct-dependency metadata packaging,
not as evidence that automatic metadata collection failed. No collect-all or
watcher-backend expansion is needed for this presence check. The spec regression
executes the spec, verifies that the exact real-package request reaches Analysis,
and preserves sentinel metadata propagation and all other packaging settings.
It fails on `2cce4bf`; it does not freeze or run Windows code. Root owns independent
scoped review and **one controlled actual Windows CI rerun at the reviewed head**
with the existing smoke/firewall/state/redaction/artifact gates unchanged. Only
that run can confirm or refute runtime repair. Another red result calls for
evidence review, not skipped checks, fake green, source substitution or repeated
speculative fixes.

## Live visual feedback

The dashboard and Streamer UI show bundled loot arrivals, actual skill-gain activity,
rolling monetary totals, and short chart transitions. New loot/skill rows briefly
highlight. Historical sessions and resumed tracking establish a quiet baseline;
opening a saved run never replays its loot as new arrivals.

Use **Reduced motion** in **Help** to settle totals immediately and disable motion
and flashes. Quiet loot/skill sounds can be enabled in **Help** on Windows; they are
off by default, bundled, and never overlap. Net profit and costs remain visible;
visual effects never change records.

To see the effects without waiting for a drop, choose **Help → Preview live feedback**
or run:

```bash
python -m ped_hunter gui --preview
```

The clearly labeled preview uses a temporary database and synthetic events. It does
not read chat logs, write to your real session history, or interrupt a running tracker.

The **Streamer UI** opens in the bottom-right corner of the dashboard's monitor,
with a 16-pixel inset from the usable desktop edges (above the Windows taskbar).
It fits its contents before appearing. You can still drag or resize it; live
refreshes will not snap a manually positioned window back to the corner.
Closing and reopening the Streamer UI restores the bottom-right starting position.

The broadcast HUD uses a side-by-side net/return hero, a persistent tracking state,
and compact overlay-local typography. Its default size is **360×330** at 100%
scaling, with a **260×240** minimum. Fonts and spacing adapt when you resize,
not on live-data or animation ticks. The hero, metrics, activity, chart summary,
and footer keep stable slots inside the chosen rectangle without changing the
main window's scaling. Large monetary totals fit their own field's font without
moving other sections or losing precision. No-session and zero-cost
returns show `—`, not a misleading return percentage. The chart is always labelled
**PED**, even when the monetary cards use USD; **Kills*** are inferred event
clusters, not independently confirmed kills. Query failures are distinguished
from an empty chart. Unknown durability is a neutral empty track, not an alarm.

The header and background can be dragged. Resize from **any edge or corner**,
or use the bottom-right grip; mouse capture keeps the drag working outside the
small handle. Live refreshes preserve the chosen rectangle, and enlargement
never raises the minimum. Focus the brand or resize handle with
Tab, then use arrow keys to move by 10 pixels or Shift+arrows to resize. The close
button supports Space/Enter, and Escape dismisses only the overlay. Enlargement gives the chart more room; shrinking adapts fonts, spacing, and
field allocations instead of imposing a minimum based on previously enlarged content.
Activity uses two fixed single-line slots; long messages ellipsize visually
without changing the strip's height. Arrival messages settle back to quiet copy
after four seconds without moving the HUD, including with
reduced motion. Preview keeps a persistent **DEMO** state and synthetic-money
warning and uses the same layout/placement as the production overlay.

### Crafting inputs in the streamer

Use **Manufacturing → Add material cost** before the batch. This books the full
configured batch TT cost once in the session; it is not charged again per receipt.
The streamer immediately shows the saved blueprint, TT input per attempt and
`observed/planned` groups, even before the first result arrives. While crafting,
it also shows batch returns, booked input and net against that booked input.
The top cards are explicitly **booked whole-session** totals, including hunting;
chart values and input/batch figures remain **PED** when the cards use USD.

Real crafting logs often contain only `You received ...` lines, not an explicit
success/failure notice. Product, returned ingredients, residue and shrapnel in
one receipt group share one configured input cost. Craft receipts crossing a
clock-second boundary stay together. **C** labels mark craft groups; **K** labels
mark hunting clusters. The Groups* count is not a confirmed click/kill count.
Craft chart averages/deltas are within a batch; hunting averages stay separate.
Hover/focus the footer for full blueprint, cost-source and inference details.

A newly added setup starts a new batch at its saved event position; outgoing
attacks switch attribution back to hunting. Streamer attribution never prices
older receipts retroactively or treats stale unused setup entries as a future
FIFO queue. Completely
silent failures are unobservable, so observed groups can be fewer than planned
attempts while the entire entered budget remains booked. Extra craft groups
beyond the configured plan have **unknown** input/profit, not free profit; add a
new setup for a new batch. TT estimates do not include material markup.

Saved runs are reprojected read-only using their existing setup records: no
history rewrite, rebilling, or manual re-entry is needed. Craft progress updates
retain the same fixed-layout and resize behavior.

Design direction: BobRoss.bot's [streamer HUD specification](docs/streamer-redesign.md).

## Building the Windows executable

Release executables are built with PyInstaller:

```bash
python -m PyInstaller PED-Hunter.spec
```

The spec bundles `data/catalog/` so catalog lookups work from the standalone `.exe`.

## Data source

The seed catalog is imported from the legacy LootNanny database files at:
https://github.com/euloggeradmin/LootNanny

The legacy LootNanny seed set does not currently include the newer Frontier starter weapons, so PED Hunter adds supplemental entries backed by EntropiaWiki and Entropia Nexus:

- `Frontier Hunting Rifle`
- `Frontier Hunting Rifle, Adjusted`
- `Frontier Combat Knife`
- `Frontier Combat Knife, Adjusted`
- Sources: [EntropiaWiki weapon id 3070](http://www.entropiawiki.com/Info.aspx?chart=Weapon&id=3070), [Entropia Nexus](https://entropianexus.com/items/weapons/Frontier~Hunting~Rifle), [Entropia Nexus Frontier Combat Knife](https://entropianexus.com/items/weapons/Frontier~Combat~Knife), [Entropia Nexus Frontier Combat Knife, Adjusted](https://entropianexus.com/items/weapons/Frontier~Combat~Knife~Adjusted)

For `Frontier Hunting Rifle, Adjusted`, the catalog uses 180 ammo per shot, 0.020 PEC decay (stored as 0.0002 PED), and TT limits of 0–2 PED. Total cost is 1.820 PEC (0.0182 PED) per shot. The base rifle and its `Frontier Rifle` alias remain unchanged. Verified against [Entropia Nexus](https://entropianexus.com/items/weapons/Frontier~Hunting~Rifle%2C~Adjusted) and [EntropiaWiki weapon id 3065](http://www.entropiawiki.com/Info.aspx?chart=Weapon&id=3065).

For convenience, PED Hunter also accepts the shorthand:

- `Frontier Rifle` → `Frontier Hunting Rifle`

## Changelog

### Unreleased

- Add read-only `doctor` setup checks, redacted text/JSON diagnostics, and optional explicit log-file readability checks without reading chat contents.
- Correct Windows source setup instructions for Python >=3.11 and add safe diagnostic-sharing guidance.
- Add bounded Windows Python 3.11/3.13 diagnostics/CLI CI alongside unchanged full Linux jobs; this is not a full Windows GUI acceptance claim.

## Notes

The legacy LootNanny seed set is useful, but PED Hunter is being rebuilt with a cleaner architecture and a more modern feel.
