# PED Hunter

[![CI](https://github.com/suttonwilliamd/ped-hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/suttonwilliamd/ped-hunter/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/suttonwilliamd/ped-hunter)](https://github.com/suttonwilliamd/ped-hunter/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**A free, local-first Entropia Universe session, loot, cost, and return tracker for Windows.**

PED Hunter follows the Entropia Universe chat log, organizes activity into sessions, and helps compare loot with configured weapon and loadout costs. It requires no account and stores its working data on your computer.

> [!IMPORTANT]
> PED Hunter is alpha software and an unofficial community project. It is not affiliated with or endorsed by MindArk or Entropia Universe. Parsed events, catalog values, costs, and calculations may be incomplete or inaccurate. PED Hunter does not predict or guarantee profit.

## Download

[**Download the latest Windows executable**](https://github.com/suttonwilliamd/ped-hunter/releases/latest)

The release asset is currently an unsigned Windows executable. Review the source and release details before running it. Running `PED-Hunter.exe` without arguments opens the desktop dashboard.

## Features

- Live monitoring of loot, combat, crafting, skill, and global chat events
- Session summaries for loot, configured costs, return, and history
- Loadouts for weapons, amplifiers, scopes, sights, and enhancers
- Repair and durability tracking based on configured equipment
- Crafting runs, WTB listings, catalog search, and shorthand aliases
- Compact streamer overlay
- Local SQLite storage with no account or telemetry
- Command-line monitoring, statistics, and catalog lookup

## Quick start

1. In Entropia Universe, enable chat logging.
2. Download and run the [latest Windows release](https://github.com/suttonwilliamd/ped-hunter/releases/latest).
3. Use the **Chat log** control in the top bar to select the Entropia Universe `chat.log` file.
4. Configure a loadout before starting a session if you want cost estimates.
5. Start tracking, play, then stop the session and review its summary and history.

PED Hunter records only events it can recognize from the selected log. Validate important totals against the game before relying on them.

## Run from source

Python 3.11 or newer is required.

```bash
git clone https://github.com/suttonwilliamd/ped-hunter.git
cd ped-hunter
python -m pip install -e ".[dev]"
python -m ped_hunter gui
```

Useful commands:

```bash
ped-hunter monitor --chat-log "path/to/chat.log"
ped-hunter stats
ped-hunter weapon "Frontier Hunting Rifle"
ped-hunter seed-data
```

The explicit `seed-data` command downloads catalog JSON from the legacy LootNanny repository. Normal session monitoring does not upload chat or database data. See [Privacy](PRIVACY.md) for exact behavior and local file locations.

## Catalog and calculation limitations

The bundled catalog is seeded primarily from legacy LootNanny data and includes selected supplemental entries. It may be stale or incomplete. Cost and return estimates depend on recognized log messages, current item data, and the loadout values you enter. TT values and player-market markup are not interchangeable.

Found incorrect data? Submit a [catalog correction](https://github.com/suttonwilliamd/ped-hunter/issues/new?template=catalog_correction.yml) with a verifiable source.

## Help and feedback

- [Report a bug](https://github.com/suttonwilliamd/ped-hunter/issues/new?template=bug_report.yml)
- [Request a feature](https://github.com/suttonwilliamd/ped-hunter/issues/new?template=feature_request.yml)
- [Ask the community](https://github.com/suttonwilliamd/ped-hunter/discussions)
- [Review open issues](https://github.com/suttonwilliamd/ped-hunter/issues)

Do not post real chat logs, avatar names, private messages, or other personal data. Redact examples first. Report security issues privately as described in [Security](SECURITY.md).

## Contributing

Contributions and carefully sourced catalog corrections are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

PED Hunter is available under the [MIT License](LICENSE).
