
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

The goal is to keep the useful parts of LootNanny's workflow while avoiding dense legacy form layouts and making room for richer modern catalog sources.

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

```bash
ped-hunter monitor --chat-log "%APPDATA%/Entropia Universe/chat.log"
```

5. Ask for stats:

```bash
ped-hunter stats
```

6. Look up a weapon:

```bash
ped-hunter weapon "Frontier Rifle"
```

## Building the Windows executable

Release executables are built with PyInstaller:

```bash
python -m PyInstaller PED-Hunter.spec
```

The spec bundles `data/catalog/` so catalog lookups work from the standalone `.exe`.

## Data source

The seed catalog is imported from the legacy LootNanny database files at:
https://github.com/euloggeradmin/LootNanny

The legacy LootNanny seed set does not currently include the newer Frontier starter rifle, so PED Hunter adds a supplemental weapon entry backed by EntropiaWiki and Entropia Nexus:

- `Frontier Hunting Rifle`
- Sources: [EntropiaWiki weapon id 3070](http://www.entropiawiki.com/Info.aspx?chart=Weapon&id=3070), [Entropia Nexus](https://entropianexus.com/items/weapons/Frontier~Hunting~Rifle)

For convenience, PED Hunter also accepts the shorthand:

- `Frontier Rifle` → `Frontier Hunting Rifle`

## Notes

The legacy LootNanny seed set is useful, but PED Hunter is being rebuilt with a cleaner architecture and a more modern feel.
