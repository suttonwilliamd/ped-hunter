# Catalog source research: missing Entropia equipment sets

## Summary

PED Hunter currently imports only the legacy LootNanny raw files:

- `weapons.json`
- `attachments.json`
- `scopes.json`
- `sights.json`
- `resources.json`
- `crafting.json`

That source is useful for weapon cost/decay and some crafting/resource lookups, but it is **not a complete Entropia Universe equipment database**. Entire equipment families are absent, including armor sets/armor parts, armor plates, mining tools, medical tools, vehicles, misc tools, clothing, consumables, refiners, and modern web-shop starter-pack items.

The practical consequence: fixing a single alias such as `Frontier Rifle` is not enough. The Frontier starter gear is a whole missing package family, not one missing weapon.

## Evidence gathered

### Current PED Hunter normalized catalog

Local counts from `data/catalog/`:

| Local file | Count | Notes |
| --- | ---: | --- |
| `weapons.json` | 2,881 | includes old `EWE LC-100 Frontier*` and manually-added `Frontier Hunting Rifle` |
| `attachments.json` | 341 | amps/scopes/sights only |
| `resources.json` | 1,335 | stackables/material values |
| `crafting.json` | 3,454 | recipes/material lists |
| `aliases.json` | 1 | only `Frontier Rifle -> Frontier Hunting Rifle` |

Search for `Frontier` locally currently returns only:

- `EWE LC-100 Frontier`
- `EWE LC-100 Frontier (L)`
- `EWE LC-100 Frontier, CDF Edition (L)`
- `Frontier Hunting Rifle`

### LootNanny raw source shape

GitHub source: `https://github.com/euloggeradmin/LootNanny/tree/main/data/raw`

Raw files present there:

- `attachments.json`
- `crafting.json`
- `resources.json`
- `scopes.json`
- `sights.json`
- `weapons.json`

There are no raw `armor`, `armor_sets`, `medical_tools`, `vehicles`, `finders`, `excavators`, `refiners`, `misc_tools`, `clothing`, or `consumables` files in that source directory.

### EntropiaWiki chart coverage

EntropiaWiki exposes separate charts that the current import does not touch. Counts observed from chart pages:

| Chart | EntropiaWiki count | Current PED Hunter coverage |
| --- | ---: | --- |
| Weapons | 2,991 | partial via LootNanny weapons, plus one supplement |
| Weapon Attachments | 383 | partial via LootNanny attachments/scopes/sights |
| Armor Sets | 319 | missing |
| Armor Parts | 1,820 | missing |
| Armor Platings | 238 | missing |
| Finders | 104 | missing |
| Finder Amplifiers | 54 | missing |
| Excavators | 54 | missing |
| Medical Tools | 300 | missing |
| Clothes | 639 | missing |
| Vehicles | 111 | missing |
| Mindforce Implants | 30 | missing |
| Personal Effect Chips | 17 | missing |
| Teleportation Chips | 30 | missing |
| Materials | 2,874 | partial via LootNanny resources |
| Blueprints | 4,030 | partial via LootNanny crafting |

Useful chart URLs:

- `http://www.entropiawiki.com/Chart.aspx?chart=Weapon`
- `http://www.entropiawiki.com/Chart.aspx?chart=Attachment`
- `http://www.entropiawiki.com/Chart.aspx?chart=Armor`
- `http://www.entropiawiki.com/Chart.aspx?chart=ArmorItem`
- `http://www.entropiawiki.com/Chart.aspx?chart=Plating`
- `http://www.entropiawiki.com/Chart.aspx?chart=Finder`
- `http://www.entropiawiki.com/Chart.aspx?chart=FinderAmplifier`
- `http://www.entropiawiki.com/Chart.aspx?chart=Excavator`
- `http://www.entropiawiki.com/Chart.aspx?chart=FAP`
- `http://www.entropiawiki.com/Chart.aspx?chart=Vehicle`

### Entropia Nexus API coverage

Entropia Nexus has a public API with typed endpoints and a search endpoint:

- API docs: `https://api.entropianexus.com/docs/`
- Search: `https://api.entropianexus.com/search/detailed?query=Frontier`

Observed endpoint counts:

| Endpoint | Count |
| --- | ---: |
| `/weapons` | 3,141 |
| `/weaponamplifiers` | 297 |
| `/weaponvisionattachments` | 104 |
| `/armorsets` | 340 |
| `/armors` | 2,094 |
| `/armorplatings` | 250 |
| `/finders` | 111 |
| `/finderamplifiers` | 57 |
| `/excavators` | 54 |
| `/medicaltools` | 259 |
| `/misctools` | 336 |
| `/vehicles` | 124 |
| `/clothings` | 853 |
| `/stimulants` | 114 |
| `/materials` | 3,392 |
| `/blueprints` | 3,989 |
| `/refiners` | 26 |
| `/mindforceimplants` | 34 |
| `/effectchips` | 23 |
| `/teleportationchips` | 31 |

This looks like the best current primary machine-readable import source for PED Hunter.

## Concrete missing Frontier family

`https://api.entropianexus.com/search/detailed?query=Frontier` returns a full family of modern starter-pack items that are mostly absent locally:

### Clothing

- `Frontier Coat`
- `Frontier Boots`
- `Frontier Pants`
- `Frontier Shirt`

### Armor sets

- `Frontier`
  - 7-piece armor set
  - 11.0 total defense
  - 20,000 durability
  - +8% run speed set effect at 7 pieces
  - set pieces:
    - `Frontier Helmet`
    - `Frontier Harness`
    - `Frontier Arm Guards`
    - `Frontier Gloves`
    - `Frontier Thigh Guards`
    - `Frontier Shin Guards`
    - `Frontier Foot Guards`
- `Frontier, Adjusted`
  - 7-piece armor set
  - 22.0 total defense
  - 20,000 durability
  - +16% run speed set effect at 7 pieces
  - set pieces:
    - `Frontier Helmet, Adjusted`
    - `Frontier Harness, Adjusted`
    - `Frontier Arm Guards, Adjusted`
    - `Frontier Gloves, Adjusted`
    - `Frontier Thigh Guards, Adjusted`
    - `Frontier Shin Guards, Adjusted`
    - `Frontier Foot Guards, Adjusted`

### Weapons

- `Frontier Hunting Rifle`
- `Frontier Hunting Rifle, Adjusted`
- `Frontier Combat Knife`
- `Frontier Combat Knife, Adjusted`

Important: these are distinct from `EWE LC-100 Frontier`, `EWE LC-100 Frontier (L)`, and `EWE LC-100 Frontier, CDF Edition (L)`.

### Medical/tools/vehicle/consumable

- `Frontier First Aid Pack`
- `Frontier First Aid Pack, Adjusted`
- `Frontier Vehicle Repair Tool (L)`
- `Frontier 4x4 (L)`
- `Frontier Stimulant Pill`

Steam discussion and video metadata around the 2025 starter packs also describes the package scope as rifles, knives, armor sets, vehicle, and tools. Entropia Nexus provides the machine-readable item data for these names.

## Recommendation

Do not keep patching individual missing items by hand. Replace the catalog sync model with a multi-source importer:

1. Keep LootNanny as a legacy compatibility source for known weapon/resource/crafting data.
2. Add Entropia Nexus as the primary typed catalog source for:
   - weapons
   - weapon amps / sights / scopes
   - armor sets and armor parts
   - armor plates
   - mining tools
   - medical tools
   - misc tools
   - vehicles
   - clothing
   - consumables/stimulants
   - refiners
   - mindforce tools/chips
3. Keep EntropiaWiki as a cross-check/source-of-truth fallback when Nexus is incomplete or a value needs confirmation.
4. Model item families explicitly:
   - `items`: common identity fields
   - `weapons`: weapon-specific economy/damage/skill fields
   - `armor_sets`: set-level defense/effects
   - `armor_parts`: per-slot pieces
   - `tools`: medical/mining/misc/refiner variants
   - `vehicles`, `clothing`, `consumables`
   - `aliases`: user shorthand and legacy name compatibility
5. Add an audit command that compares local counts against Nexus/EntropiaWiki by category and flags missing names.

## Immediate import target

Start by importing the full `Frontier` search result from Entropia Nexus as a regression fixture. It is small, modern, and proves that PED Hunter can handle one real cross-category equipment family instead of only weapons.
