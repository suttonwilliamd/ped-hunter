
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
- resolves community shorthand like `Frontier Rifle` to the canonical legacy entry `EWE LC-100 Frontier`
- computes derived values like cost per shot from the item catalog

## Quick start

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

## Data source

The seed catalog is imported from the legacy LootNanny database files at:
https://github.com/euloggeradmin/LootNanny

The canonical Frontier-series entry currently used in the seed catalog is:

- `EWE LC-100 Frontier`

For convenience, PED Hunter also adds the alias:

- `Frontier Rifle` → `EWE LC-100 Frontier`

## Notes

The legacy LootNanny seed set is useful, but PED Hunter is being rebuilt with a cleaner architecture and a more modern feel.
