
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

The legacy LootNanny seed set does not currently include the newer Frontier starter rifle, so PED Hunter adds a supplemental weapon entry backed by EntropiaWiki and Entropia Nexus:

- `Frontier Hunting Rifle`
- Sources: [EntropiaWiki weapon id 3070](http://www.entropiawiki.com/Info.aspx?chart=Weapon&id=3070), [Entropia Nexus](https://entropianexus.com/items/weapons/Frontier~Hunting~Rifle)

For convenience, PED Hunter also accepts the shorthand:

- `Frontier Rifle` → `Frontier Hunting Rifle`

## Notes

The legacy LootNanny seed set is useful, but PED Hunter is being rebuilt with a cleaner architecture and a more modern feel.
