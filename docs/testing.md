# Focused tests and complete-suite evidence

Use the existing project `.venv`; the runner requires pytest in that interpreter.
It adds no application dependency, marker requirement or CI filter. Ordinary
`python -m pytest` discovery is unchanged (`testpaths=tests`, `pythonpath=src`,
`addopts=-q` in `pyproject.toml`).

## Commands

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe tools\pytest_groups.py list
.\.venv\Scripts\python.exe tools\pytest_groups.py run catalog-loadouts
.\.venv\Scripts\python.exe tools\pytest_groups.py run chart-accounting ingestion --dry-run
.\.venv\Scripts\python.exe tools\pytest_groups.py run packaging-diagnostics -- -k pytest_groups -ra
.\.venv\Scripts\python.exe tools\pytest_groups.py full --dry-run
.\.venv\Scripts\python.exe tools\pytest_groups.py full -- -ra
```

`run` requires one or more explicit group names. Selection is their **union**, in
pytest's collection order: overlaps and repeated names do not repeat tests.
Unknown group names fail before collection. Options after `--` go to pytest;
reporting options from the existing configuration remain available.

`list` and `--dry-run` use pytest's real collectors, not a second filesystem glob
that could miss custom test names. They import test modules but do not execute
test bodies or fixtures. They show selected file/case counts, group membership,
collection skips and unmapped files. **Collection is not passing-test evidence.**
Imports can have side effects, just as with ordinary `pytest --collect-only`;
review/isolate collection when a live application must remain protected.

Focused runs, including dry runs, refuse any discovered **unmapped test file**,
even one skipped during collection. Add its correct membership in `GROUPS` in
`tools/pytest_groups.py`, based on its imports and behavior. Component-family
patterns include matching new files automatically and the inventory shows them;
unrecognized names remain visible. Do not rename, delete or skip tests to satisfy
the mapping. There is no automatic fallback that unexpectedly runs the full suite.

`full` does not filter by the registry: unmapped tests are reported **and included**.
It rejects `-k`, `-m` and `--deselect` rather than calling a subset full. File/node
paths, `--ignore`, `--ignore-glob`, `--lf` and `--stepwise` are rejected by the group
tool because they can hide discovery. Use ordinary pytest for an explicit
file/node or other deliberate subset and record that scope as focused, for example:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pytest_groups.py -ra
```

Other configuration overrides/plugins still require review; a command named
`full` is not proof that every environment-dependent case was exercised.

## Choose by changed behavior and affected consumers

The registry is deliberately file-level and overlapping. It was mapped from
actual imports and assertions, not from which source filename happened to change.
`list` is the current inventory; counts are not pinned in this guide.

| Changed source/contract | Start with | Dependency guidance grounded in existing tests |
|---|---|---|
| `activity_trend`, `chart_scales`, `chart_trails`, `hunt_attribution`, chart projection in `app.py` | `chart-accounting` | Includes cost/repair consumers in `test_loadouts`, incremental input, persistent attribution, real fan canvas, craft grouping and live refresh budget. Add `desktop-ui` for geometry/range/animation changes; `persistence-lifecycle` for stored samples/decisions. |
| `parser`, `log_tail`, `log_watcher`, app worker/drain/path handling | `ingestion` | `test_log_ingestion` exercises real appends, UTF-8/partial writes, rotation/truncation, EOF resume, ended sessions and GUI consumers. `test_loadouts` checks parsed attacks through Store and displayed costs. Add chart/persistence groups when payload/accounting meaning changes; trading for chat/listing semantics. |
| `catalog.py`, catalog data, CLI seed/import mapping, `tools/sync_legacy_data.py`, setup editor/cost helpers, currency | `catalog-loadouts` | Ammo/decay/markup, repairs, setup snapshots and resume are shared consumers. `test_setup_editor` runs real SQLite controller transitions without a native window: new versus existing identity, duplicate protection, unsaved choices, activation, original-event conservation and plain-save behavior. Price/setup activation changes also need chart/accounting and persistence consumers. Catalog tests overlap diagnostics through CLI import/version metadata. |
| `storage.py`, session start/end/resume/context, stored caches and projections | `persistence-lifecycle` | Includes original-event preservation, estimate/fan round trips, loadout snapshots, live ingestion, startup summaries and single-instance ownership. Broad Store/schema/migration changes justify an early full run, not just this group. |
| Native widgets/layout/DPI, `scrolling.py`, `feedback.py`, `sounds.py`, streamer behavior | `desktop-ui` | `test_default_layout` exercises real setup/Help widgets, persistent actions, focused-control reveal, smaller geometry, native font bounds and simulated application DPI; `test_setup_editor` is controller/data evidence, not rendered acceptance. Includes streamer rendering/resize/toggle/placement/stability/crafting and GUI ingestion/fan cases. Add catalog/loadouts and persistence for setup controls/activation. A large `app.py` edit alone is not a full-suite gate. |
| CLI/doctor/startup lock, `__init__`, spec/installer, frozen diagnostic helper, test runner | `packaging-diagnostics` | Covers doctor isolation/redaction, helper failure behavior, packaging metadata, single-instance lock, catalog release checks, sound CLI flags and selection tooling. Tooling-only changes can use the direct tooling test module plus collection/CLI smoke instead of unrelated diagnostics or native GUI execution. |
| WTB extraction and `app._wtb_listings` | `trading` | `test_wtb` crosses parser, stored chat rows and displayed channel/quantity/price filtering; parser tests overlap ingestion. Add persistence coverage if queries/schema change. |

Select the changed component **plus affected dependencies**. Groups are useful
starting points, not proof that every acceptance case is covered. Preserve named
bug/feature sequences and required visual checks. A carefully justified direct
file/node selection is valid focused evidence; do not use `-k` to discard failing
or required cases. Update the mapping/guide when behavior moves between files.

`test_space_mining` covers the Type M supplement/import paths, PEC/PED units,
selected-setup attacks/misses, explicit loot values and recorded-history
conservation. `test_resume_handoff` crosses ingestion and persistence with real
files, SQLite and the normal Resume worker: unique ordered byte anchors,
CRLF/UTF-8/partial writes, replacement/truncation, claimed/partial-failure holds,
large gaps and changing future equipment. `test_space_mining_ui` adds real native
search/save/activation and Resume/HUD consumers. Keep those dependencies together
when changing source-cursor or selected-equipment accounting behavior.

`test_resume_responsiveness` adds native/SQLite checks for a responsive event
loop while history preparation is held, arrivals and duplicate lines during
preparation, ended-session confirmation without a success modal, draft/settings
preservation, cancellation followed by another run, stale setup changes and
failed preparation with a retained claimed source cursor. It belongs to
ingestion, persistence/lifecycle and desktop UI. Large-session wall-clock and
heartbeat measurements require isolated real-data reproduction as well; a
small controlled regression is not a performance claim for a user's database.

`test_setup_slots` crosses the saved-order migration, exact ID permutations and
restart persistence, pinned New setup/drag cancellation, real native slot
states and font bounds, unsaved choices, and normal-file uses on both sides of
a HUD activation. It overlaps catalog/loadouts, persistence, ingestion,
chart/accounting and desktop UI. Reordering must conserve every original field
and all historical rows; activation may change only subsequent cost context.
Native captures must distinguish actual Windows DPI from simulated HUD scale.

`test_weapon_images` is headless: original synthetic PNGs exercise exact names
and explicit aliases (not fuzzy weapon search), variant separation, all four
requested identities, missing/broken/unapproved assets, stale asynchronous
completions under a blocked reader, finite worker/cache storage, and all-entry
catalog/provenance/reuse auditing. It belongs to catalog/loadouts and desktop
UI groups, but does **not** open Tk or prove compact rendered readability.
During a background-only pass, run this module directly plus affected safe
dependencies; do not run a native fixture simply because its group overlaps.
Actual HUD image/font/active-state contrast at ordinary and minimum sizes and
representative DPI requires separately authorized native captures. Synthetic
test art and an all-entry manifest are not recognizable weapon-image coverage.


For setup or Help layout changes, run controller/data checks **and** authorized
native layout checks. A headless controller/SQLite pass cannot prove that a
button is visible, keyboard traversal works on Windows, or a preview child
opens safely. Record actual client dimensions, actual monitor DPI versus any
simulated application scale, before/after screenshots, lower-control use and
untested scaling limitations. Simulating `tk scaling` is not changing Windows
display scaling and is not native-monitor parity.

If a live-hunt protection or GUI safety guard blocks rendering, retain the
failure and frozen before-source data; report GUI acceptance as **blocked**,
not passed or silently skipped. Do not weaken the guard, retry a launch without
clearance, or treat a focused/model pass as permission to deploy/restart.


## GUI, operating-system and skip limits

Tests that create Tk windows need a suitable display. Existing CI runs the full
Linux suite under `xvfb-run --auto-servernum` on Python 3.11 and 3.13. Its Windows
source doctor job is bounded diagnostics, not a full suite or native GUI parity;
the frozen-doctor job is a separate built-executable qualification. Existing CI
jobs and commands remain intact.

On Windows, repeated Tk teardown in one process can be unreliable. When GUI
execution is authorized, opt in to a fresh pytest process per selected case:

```powershell
.\.venv\Scripts\python.exe tools\pytest_groups.py run desktop-ui --isolated -- --capture=sys -ra
```

`--isolated` preserves the selected cases and reports failed child processes;
read **each pytest summary** for passes/skips. It is **not source/data/window
isolation** and grants no permission to interrupt a live hunt. Repeated-interpreter
`tcl_findLibrary`/`tk.tcl` initialization failures must be retained and reported,
not converted into passes or silently skipped. Fresh-process case evidence must
cover the complete selected inventory; do not discard the failing cases.
Windows native checks use `--capture=sys` to avoid file-descriptor capture's
interference with lazily initialized Tcl/Tk. This does not bypass collection,
assertions, skips or child exit status, and does not alter CI's full discovery.


While a live session is protected, do not activate/move/close/restart its app,
HUD or game, change tracking/settings/data, or alter its live modules/install.
Test windows need isolated sources/data/processes and bounded owned cleanup.
Follow the user's current explicit monitor/foreground/input permissions: they
may allow either monitor and test-window foreground/overlap, but do not authorize
input to the game or unrelated process termination. A new process alone is not
proof of isolation. Any required provider control approval remains separate.

For no-GUI tooling work, exercise disposable test projects and collection/CLI
behavior without launching the application. A display/import skip is an explicit
environment limitation, not a pass. Use `-ra` and retain reasons, collection
errors, deselections and unsupported-platform cases. An all-skipped/no-selected
run must not be presented as verified behavior. Neither a collection-only run
nor a successful isolated-process count is an unqualified full-suite pass.

## Evidence, CI reuse and durable cadence

Record the actual command, interpreter/platform, exact revision/tree, selected
groups/files, result, pass/fail/skip counts and reasons, and retained output.
Distinguish **focused**, **full**, **failed**, **not run/environment blocked** and
**collection only**. Preserve failed attempts and identify only the selections
superseded by corrected reruns. Do not infer a full pass from a green exit code
when filters, skips or unsupported environments reduced coverage.

Adequate successful full CI evidence on the **exact delivered revision/tree** can
avoid repeating hundreds of tests locally or in a commit snapshot. Check the
actual CI run, tested SHA, commands, complete collection, exclusions/skips and
relevant platform coverage; do not treat a pending run or another SHA as proof.
A clean CI checkout does not cover unrelated dirty/untracked local edits merely
because `HEAD` matches. Record those differences and any needed targeted checks.
Reuse unchanged exact-source evidence across a restart; a restart is not itself
a reason for another broad suite.

The external coordinator owns the durable cadence ledger, not this repository's
runner. Its `queue.json.testPolicy` and `test-cadence.mjs` derive completed-job
counts across context loss. One delivered job is one change, not each commit or
test invocation. Normally use focused tests; plan a full run about every fifth
completed change since the last **verified** full pass, or earlier for shared
schema/Store, event-ingestion/accounting, lifecycle, dependency/runtime risk or
unexplained cross-component failures. Unknown history is not a fabricated reset.

After the coordinator finishes a job, it saves a record like this in its own
workspace and records it with `node test-cadence.mjs record results/ID-tests.json`;
`node test-cadence.mjs status` shows the durable assessment before next dispatch.
The worker reports evidence but does not finish/release the queue itself.

```json
{
  "id": "ID",
  "mode": "focused",
  "result": "passed",
  "command": "actual command and selections",
  "revision": "exact commit plus relevant dirty-tree evidence",
  "evidence": "retained output path; pass/fail/skips and reasons",
  "reason": "changed behavior, dependencies, and why focused/full was chosen"
}
```

A full passing reset additionally requires supported evidence for
`completeCollection=true`, `verifiedFinalTree=true`, `exclusionsReviewed=true`.
Focused passes, failures, skipped/blocked runs and not-run records never reset
cadence. An adequate exact-revision full CI run may supply that evidence; no
redundant local full rerun is required. The runner neither reads nor rewrites the
external ledger and never automatically escalates a focused request to full.

Tests/docs/tooling-only changes that leave runtime files unchanged need scoped
commit/push and tooling verification, **not** an editable reinstall or app
restart. Record installation/restart/restoration as not applicable with the
reason and live-process/session/HUD preservation evidence. Actual runtime
changes still require the normal safe deployment and user-permission workflow.
