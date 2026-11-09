
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

The main window starts at 900×600 logical pixels, with a 760×500 minimum; Windows monitor DPI scales those dimensions. Resizing does not reset the window to its default. Main-window text uses the same readable point-equivalent sizes rendered in native-DPI pixels rather than inheriting another monitor's process-wide Tk point scaling. The profit headline is slightly quieter so it does not dominate the editor. There is no separate Compact mode. Lifetime totals live in **Statistics**, leaving **Dashboard** focused on run history. Saved runs do not populate the live summary until you explicitly start or resume a run.

The goal is to keep the useful parts of LootNanny's workflow while avoiding clutter and making room for richer modern catalog sources.

### Setups and Help in smaller windows

**New setup** is always the first saved-list entry. Select it for an unnamed draft
with no weapon selected, optional attachments set to None and zero enhancers.
Saving a new draft creates a separate saved setup; an existing name is rejected,
not silently overwritten. Select an existing setup to edit it; existing-row
selection retains the application's activation behavior.

**Save** preserves the active-setup choice; saving an already-active setup also
keeps its current run's setup snapshot consistent. **Save and activate** changes
the active choice to the saved setup and updates that snapshot. Neither action
reprices recorded events. Starting a new draft leaves the active setup and
current run unchanged. Changing selection with unsaved edits offers Save
(without activation), Discard or Cancel. Delete is disabled for an unsaved draft.

The setup fields and explanatory text scroll, while draft state, current cost
feedback and all three actions stay outside that scroll viewport. The saved
comparison table has horizontal scrolling instead of forcing the form wider.
The Help page scrolls as a whole, including Reduced motion, Refresh saved data,
Quiet sounds and the separate synthetic-data preview. Mouse wheel and Page Up /
Page Down operate within each scroll pane; tabbing to a field/control reveals
it. Read-only explanatory text remains selectable. No system display-scaling
change or new user-geometry persistence is required.
The summary wraps to its actual column width and stacks its two headline values
when narrow. The main event stream yields space to its chart rather than
flattening it at the smaller minimum; all events remain available by scrolling.
Chart text uses native-DPI font measurements for axis padding and a separate
caption lane so values, event numbers and descriptions do not cover each other.

## Basic Mining Laser, Type M (L) — 0.3.42

The exact **Basic Mining Laser, Type M (L)** is searchable and selectable in
**Setups**, distinct from Type A. Save it as a new setup and activate it for
future uses; selecting it does not reprice an existing run's recorded history.

| Unmodified per-use component | Community source units | PED |
|---|---|---:|
| Weapon Cells | 212 units | 0.02120 |
| Decay | 0.11 PEC | 0.00110 |
| Total | 2.23 PEC | 0.02230 |

Source: [Entropia Nexus weapon 3058](https://api.entropianexus.com/weapons/3058)
([item page](https://entropianexus.com/items/weapons/Basic~Mining~Laser%2C~Type~M~%28L%29)),
retrieved 2026-11-09. [Weapon Cells](https://api.entropianexus.com/materials/2501)
lists 0.0001 PED/unit. `data/catalog/weapon_supplements.json` retains the full
original weapon/ammo records, URLs, retrieval timestamp and unit semantics.
Both legacy catalog refresh paths merge this verified supplement exactly once,
even if the upstream feed later adds a differently cased copy.

These are **community catalog values, not inspection of your item**. Mining
laser attachment/enhancer compatibility is unverified: use None and zero
enhancers rather than assuming Ranged means an Energy amplifier fits. Markup,
buffs, fuel, ship wear and other unlogged expenses are not added to this base
cost. The catalog's maximum TT does not establish your item's current TT.

Observed space-mining logs use ordinary outgoing damage (including critical
hits), `You missed`, Mining Laser skills/tier notices, and personal
`You received … x (…) Value: … PED` receipts. One outgoing hit/miss is one
selected-setup use **regardless of damage magnitude**; incoming damage/misses,
skill and tier messages are not tool uses. Receipt Value is the total reported
value, not a unit price to multiply by quantity again. An unknown resource name
with an explicit Value is still priced from that receipt.

Equipment is not identified on each attack: this remains the **selected-setup
TT-cost assumption**, not detected equipment or exact asteroid identification.
Choose the correct setup before tracking. Silent/unlogged activations and
receipts without a usable explicit value cannot be reconstructed. Exact
adjacent/poll-boundary repeats are suppressed; identical legitimate same-second
messages are inherently ambiguous. Recent loot groups/fans remain inferred,
not predictions.

### Deliberate upgrade continuity

Ordinary Start/Resume still begins at the current EOF. A deliberate upgrade can
instead stage a **one-use verified source cursor** after the old writer exits.
It binds the still-current session/cost setup, a unique recent committed raw
sequence in source order, complete byte/newline boundary, file identity, short
anchor and the hash of the entire committed source prefix. Benign appends are
allowed; missing/ambiguous anchors, changed setup, replacement or truncation
fail closed rather than guessing zero/EOF.

The new app's existing Resume and normal LogTail/parser account for real
downtime and subsequent lines. No synthetic event or historical cost rewrite
is needed. A claimed `.resume.json.claimed` receipt is **not completion proof**:
it remains discoverable and blocks another Resume after an uncertain or
partially committed handoff. Preserve it for reconciliation; the upgrade
operator archives it only after real ingestion/history conservation is verified.



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
**PED**, even when the monetary cards use USD; **Groups*** are inferred event
clusters, not independently confirmed kills. Query failures are distinguished
from an empty chart. Unknown durability is a neutral empty track, not an alarm.

The streamer retains a **development fan for each observed group**, not a single
trail across the whole hunt. As that group's costs and receipt change, faint
cost/average/return positions accumulate behind the ordinary series. Completed
groups keep their fans; new groups start their own. Return traces are green for
profit, pink for loss, and gray when input cost is unknown. Hover the chart or
focus the footer for the interpretation. Groups and TT costs are inferred or
estimated from recorded inputs; the fans describe observations, **not predictions**.

Fans are sampled only when the open HUD refreshes during tracking, never from
animation frames or by replaying old events. Pre-feature groups remain ordinary
lines. A single observation is not a fan; missing intermediate positions while
the HUD is closed, paused, or between refreshes cannot be reconstructed. Each
group stores at most **24** raw-PED observations, preserving first/latest positions
and per-series/profit extrema; at most four faint traces per series keep it
readable. Only the latest 16 groups are drawn/cached. The first visible group's
endpoint range is shown without inventing an off-chart anchor.

Samples persist in the same session SQLite database, separately from accounting
events, and reload with their original group IDs. They remain stored while their
underlying event/session remains stored, including after leaving the visible
window. Resize, monitor-scale changes, and automatic chart range changes reproject
the raw data together with the current lines, including retained extrema.
Session totals, recent arrivals, and the cost/average/return series remain
available; revised per-group costs are explicitly marked as estimates.

A clearly separated event envelope at least **3×** the lower visible cohort gets
a narrow **vertical local-scale section at its chronological event position**.
The amber boundary and rotated `low–high PED` label distinguish that event's
linear scale from the ordinary left axis. The full spike remains visible,
including modest returns such as 1.11 PED among 0.05 PED costs, not just jackpots.
Multiple or adjacent exceptional events each receive their own section. Selection
requires three ordinary peers where available (fewer during startup); without a
separated cohort the chart keeps one shared linear scale rather than inventing
an ordinary population.

Lines, grid lines, fan polygons and animation never bridge a local-scale boundary.
An event at a boundary shows its retained sample range and up to four endpoint
whiskers per series instead of inventing an incoming segment on a different axis.
Ordinary fans remain shaded shapes. Equal screen heights across separate sections
do **not** mean equal PED. No values, event order, time range, accounting totals or
stored traces are changed; resize and later scale changes recalculate every
coordinate from raw PED. These are views of observed/inferred events, not forecasts.


Recent hunting input can be shared across several receipts when creatures are
killed before looting. An unusually expensive interval followed by little or no
new input triggers a **bounded reconsideration**, not proof of a particular kill
allocation. A robust reference needs at least five clean, completed, same-setup
intervals from the latest 32. Variable/mixed costs or insufficient evidence stay
ambiguous rather than falling back to invented equal shares.

The mutable window is limited to **12 receipt groups, 5 minutes, 1024 charged
shots, and a 4096 event-ID span**. Intervening shots enter its input pool once;
ongoing shots without a receipt remain separate. Supported groups get provisional
typical-cost estimates; excess input stays **shared/unassigned** until observed
evidence supports its allocation. It is already included in session expenditure,
not another charge or a prediction of more loot. Estimates plus unassigned input
conserve the original recorded hunting cost. The blue **booked average** still
includes all raw input, including unassigned cost.

Amber-ring chart points and `~` costs identify estimates; unknown costs/profits
are gray, not free wins. Recent revisions append new endpoints only to fans with
previously observed history, retaining their earlier provisional positions.
Pricing, equipped-weapon, repair and crafting boundaries, or window limits, freeze
the earlier estimates and any unresolved remainder. Future evidence does not
rewrite those frozen decisions. The log contains neither creature IDs nor
confirmed kill markers, so these are receipt-group estimates, never exact
per-creature costs or predictions.

Allocation state and per-group overrides persist in separate SQLite tables keyed
to the original session/events; raw events, totals and loadouts are not rewritten.
Saved estimates reload when the HUD reopens or the session resumes. Existing
groups before the feature's first live activation retain their original interval
costs; there is no historical backfill. Estimation runs on HUD refreshes, while
fan samples still require live observation: closed/paused gaps cannot fabricate
missing visual traces. Optional projection/storage failures leave recorded totals
available and identify the unavailable allocation.


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

## Tests and focused selection

Use the existing `.venv` and `tools/pytest_groups.py` for explicit component
groups, collection-only list/dry runs, deduplicated unions and a complete-suite
mode. Unmapped tests are surfaced, never silently excluded; ordinary pytest
discovery and existing CI remain unchanged. See [testing guidance](docs/testing.md)
for source/dependency selection, GUI safety, exact-revision CI reuse and the
external coordinator's durable focused/full cadence.

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

`SET Combat Blade (L)` is a supplemental **Melee** entry in the bundled catalog,
the CLI seed importer, and `tools/sync_legacy_data.py`. Both importers keep one
canonical entry even if the legacy feed later includes the same name.
[Nexus weapon data](https://api.entropianexus.com/weapons/SET%20Combat%20Blade%20(L))
confirms this sword consumes **65 Weapon Cells** per use, despite being melee.
[Weapon Cells](https://api.entropianexus.com/materials/2501) are 0.0001 PED each.
The weapon's **0.001 PEC** decay is stored as **0.00001 PED**, not 0.001 PED.
Without attachments/enhancers the existing formula gives
`65 / 10000 + 0.001 / 100 = 0.00651 PED` (**0.651 PEC**) per use; TT limits are
0–0.1 PED. Damage, missed/dodged attacks, and incoming damage retain the existing
hunt-accounting rules. The cost-only catalog does not model damage ranges,
professions/SIB, reload, efficiency, DPS or DPP; Nexus's derived figures are not
used as base pricing data or substituted for the app's formulas.

For convenience, PED Hunter also accepts the shorthand:

- `Frontier Rifle` → `Frontier Hunting Rifle`

## Changelog

### Unreleased

- Keep a permanent New setup entry, reject accidental same-name replacement, preserve plain-save activation and make unsaved Save/Discard/Cancel transitions explicit.
- Put setup actions outside a scrollable form; scroll all Help controls, reduce default/minimum logical geometry and keep main fonts aligned with native monitor DPI without changing system scaling.
- Keep smaller-window summaries readable and reserve chart space; measure native-DPI chart labels, edge annotations and captions without changing underlying events, values, chart range or session totals.

- Add opt-in focused pytest groups, real-collection inventory/dry runs, unmapped-test protection, complete discovery and explicit isolated-process execution; document dependency selection, honest evidence/CI reuse and durable cadence without changing runtime or CI.

- Keep ordinary streamer-chart variation readable while exceptional events remain visible, using separately labeled vertical event-local PED scales and honest line/fan breaks; preserve all raw values, chronological positions and totals.

- Improve bounded recent hunting receipt-cost estimates for delayed/batched looting and intervening shots, with conserved unassigned input, explicit ambiguity, persistent frozen decisions, and consistent per-event fan revisions.

- Add `SET Combat Blade (L)` to search and hunting setups with ammo-consuming melee costs; preserve its verified cost/TT data and uniqueness through both seed import paths.

- Add read-only `doctor` setup checks, redacted text/JSON diagnostics, and optional explicit log-file readability checks without reading chat contents.
- Correct Windows source setup instructions for Python >=3.11 and add safe diagnostic-sharing guidance.
- Add bounded Windows Python 3.11/3.13 diagnostics/CLI CI alongside unchanged full Linux jobs; this is not a full Windows GUI acceptance claim.

## Notes

The legacy LootNanny seed set is useful, but PED Hunter is being rebuilt with a cleaner architecture and a more modern feel.
