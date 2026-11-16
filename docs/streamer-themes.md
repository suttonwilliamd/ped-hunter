# Streamer HUD themes

The Streamer UI has seven selectable looks. All of them draw the same model
(`streamer_model.project`), so every theme shows identical numbers, chart samples
and setups. Themes only change geometry and colours (`streamer_scene.py`).

| Theme | Character |
|---|---|
| Current (default) | The existing dark HUD: Net · setups · Return on top, stats strip, chart |
| Results First | Full-width Net/Return hero, metric strip, tall chart, fixed activity/healing box, setup dock at the bottom |
| Field Ledger | Warm instrument sheet, angled dark Net beam, ruled stats |
| Night Signal | Horizontal: rounded result chambers, capsule metrics, full-width chart, setup pills |
| Inkwing | Ink-dark left spine with Net/Return/stats, clipped setup tickets on warm paper |
| Copper Ledger | Paper and plum: clipped debit ticket, circular Return seal, beveled setups |
| Vector Beacon | Near-black/violet rounded capsules with a KPI spine and spectral chart |

Click the theme pill (or focus it and press ←/→/Enter/Space) to cycle themes;
right-click cycles backwards. The choice is stored in the `app_settings` row
`streamer.theme.v1`; a missing or unknown value falls back to **Current**.
Switching is presentation only: no session, reader, counter, setup or chart
history is touched.

## Shared layout rules

* Design space is 720×660 (the legacy 360×330 HUD at 2×). The window opens at
  360×330 logical pixels × monitor scale at the bottom-right of the work area and
  scales uniformly when resized (corner grip, or Shift+arrow keys). Nothing
  reflows when values change.
* The activity/healing area always reserves four rows. Hunting: latest group,
  cost/average detail, durability (with bar), healing. Crafting: latest group,
  TT input, batch totals, healing. Rows that have nothing to say stay blank (an
  empty healing slot falls to the bottom row), so the HUD never grows or shifts
  when healing starts. The durability bar follows its text with a visible track;
  in narrow layouts (Inkwing) it is a rule directly under the durability row.
* Setup tiles: neutral outline for every saved setup, accent outline + tint and a
  check mark for the active one, dashed outline for empty slots, per-slot name
  colours. Click or focus + Enter/Space activates a setup.
* The footer (setup name or `Crafting: <blueprint>`) is underlined and ends in a
  **Details ›** button, clear of the resize grip; the name and the button
  both open it.
  Click, Enter or Space opens the read-only details view.
* Keyboard focus on a setup tile or the footer draws a ring in the theme's ink
  colour, distinct from the accent outline that marks the active setup.
* The theme pill shows `››` because it cycles; it is not a dropdown.

## Estimation policy `best-estimate/3`

The HUD shows numbers, not caveats: no `≈`, `~`, `*`, "unknown" or allocation
prose. The arithmetic is the legacy HUD's, so totals are unchanged:

* **Cost** = booked session cost + supported crafting excess estimate (added once).
* Craft groups with no input evidence are estimated, in order of preference, at
  this session's mean evidenced excess, else this session's mean priced
  craft-group cost; with neither, they add nothing beyond booked inputs. Other
  sessions' prices are never borrowed.
* **Net** = Loot − Cost. **Return** = Loot ÷ Cost; with zero cost it shows `—`.
* **Healing** = the session's selected-tool healing estimate. It is already part
  of Cost and is never added twice. Unpriced heal observations are not charged.
* With no session, values show `—` (nothing yet), never an invented zero.

Nothing is written back: no bookings, repricing or raw-event edits. Every
assumption, operand and formula goes to the details view and to a coalesced,
rotating JSONL log next to the database:
`%LOCALAPPDATA%\ped-hunter\logs\streamer-estimates.jsonl` (a record is written
only when the money or assumptions change).

## Rollback

`LegacyStreamerWindow` (the pre-theme widget HUD) is retained in `app.py`.
Pointing `app.StreamerWindow` back at it restores the old HUD; the theme
preference row is ignored by it. Its widget-level tests (listed in
`tests/legacy_streamer_tests.txt`) are skipped by default; run them with
`python -m pytest --legacy-hud` before and after such a rollback.
