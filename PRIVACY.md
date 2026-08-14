# Privacy

PED Hunter is local-first. It does not require an account, and the application
code contains no telemetry or analytics reporting.

## Data read and stored locally

- PED Hunter reads the Entropia Universe `chat.log` file selected by the user.
- Parsed chat, global, loot, combat, crafting, and skill events are stored in a
  local SQLite database together with their source message, parsed payload,
  timestamps, sessions, and loadout configuration.
- By default, the database is
  `~/AppData/Local/ped-hunter/ped-hunter.sqlite3`. A different database path can
  be supplied to command-line operations.
- If the database is malformed, PED Hunter may preserve the old database and
  its SQLite sidecar files as timestamped backup files in the same directory.
- If desktop startup fails, PED Hunter writes a traceback to
  `~/AppData/Local/ped-hunter/crash.log`.

These files remain on the computer until the user deletes them. PED Hunter
does not currently provide an in-app retention or deletion policy.

## Network operations

Normal monitoring, parsing, catalog lookup, and statistics use local files and
do not send chat or SQLite data over the network.

The explicit `seed-data` command and `tools/sync_legacy_data.py` fetch catalog
JSON files over HTTPS from the LootNanny repository on
`raw.githubusercontent.com`, then write normalized catalog files locally. That
request necessarily exposes ordinary connection metadata, such as the user's
IP address, to GitHub. The code does not upload chat logs, database contents,
or loadouts.

Review the source and your network policy before running catalog downloads.