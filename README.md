
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

1. Install Python 3.11+
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
scaling, with a **260×240** minimum. Fonts and spacing adapt inside the chosen
rectangle without changing the main window's scaling. Large monetary totals
reflow rather than losing precision. No-session and zero-cost
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
wrapping instead of imposing a minimum based on previously enlarged content.
Arrival messages settle back to quiet copy after four seconds, including with
reduced motion. Preview keeps a persistent **DEMO** state and synthetic-money
warning and uses the same layout/placement as the production overlay.

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

## Notes

The legacy LootNanny seed set is useful, but PED Hunter is being rebuilt with a cleaner architecture and a more modern feel.
