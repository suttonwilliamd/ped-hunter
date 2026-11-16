# Hunt cockpit (main window)

This run's readouts stay on top; run, setup and healing controls sit beside the
chart; the tabs below switch only the tables. Default size 960×640, minimum
520×360 (logical pixels; multiply by the display scale).

- **Readout strip** ("This run"): hunt net (largest), return, cost per hour of
  active play (pauses of 10 minutes or more are not counted), loot, cost, events.
- **Rail**: New run / Resume while idle, End run while tracking; setup
  quick-switch for the first four setups (buttons or Alt+1–4; the same order as
  the streamer HUD tiles); heal and HoT selectors with the per-use cost of a
  priced tool and the run's healing total; durability.
- **Tabs**: Runs (history, Resume), Lifetime, Events (filter, "Don't count this
  loot…", Loot corrections), Skills, Setups (builder, "Weapon data…"), Crafting,
  WTB, Help & settings (chat log, currency, motion, sounds).
- **Status line**: what the app is doing, the chat log in use, Streamer UI,
  Hide totals.

## Size classes

`responsive.py` picks a class from the window's logical size; the layout is
re-laid only when a threshold is crossed (16 px of slack stops flicker), so a
drag costs arithmetic, not font changes.

| Class | When | What changes |
|---|---|---|
| wide | ≥ 1000 × 600 | 20 pt net, 224 px rail, loot strip on the Events tab |
| regular | ≥ 720 × 600 | 16 pt net, 196 px rail |
| compact | narrower than 720 or shorter than 600 | one-line readouts; a run bar (run buttons, setup picker) replaces the rail, whose healing and equipment open as a drawer; tables drop secondary columns |
| compact, short | shorter than 520 | chart and tables take turns ("Show chart" / "Show tables") |
| compact, narrow | narrower than 600 | cost per hour leaves the strip |

No chart is shown until a run is started or resumed. A chart shorter than 212 px
drops its summary band and shows encounter details there only while one is focused.

Tables sort by clicking a heading (`table_sort.py`; the order survives refreshes).
Weapon, attachment, blueprint and healing selectors are searchable lists
(`search_select.py`): click to open, type to filter, arrows and Enter to pick,
Esc to cancel; text that is not a listed item is put back on leaving the field.

`tools/perf/shoot.py` captures the real window on the hidden desktop (see
docs/testing.md) for layout review without touching the screen.

- **Hunt chart** ("Hunt net by encounter"): encounter contributions as a
  waterfall that climbs and falls along the hunt net, with a bold break-even
  line. Standout wins (the largest positive encounters, at least a quarter of
  the best one) stand alone with a gold outline and a labelled landmark; the
  latest encounter always stands alone; other encounters are merged into runs
  of a round size so the bars stay readable. A short pulse marks a new standout
  win while tracking (off with Reduced motion). Details dock in the band above
  the plot and never cover bars: *Encounter gain* is that encounter alone,
  *Hunt net after* is the running result. Hover, click, or focus the chart and
  use ← → (step), Home/End, `[` `]` (previous/next landmark), Esc.

## Totals always reconcile

The chart ends exactly at the session's hunt net, and loot/cost per bar add up
to the session totals. Encounter groups do not carry every booked input
(pending healing, groups whose weapon cost is not attributed yet, other session
inputs), so `hunt_trajectory.build_trajectory` allocates the difference with
one rule (`own-input-then-shots/1`): a group without an attributed cost uses its
own booked input; the remaining difference is spread by shots fired (by cost
when negative, so no group goes below zero). Hunt data is never changed.

The allocation behind each displayed chart is written to
`%LOCALAPPDATA%/ped-hunter/logs/accounting-estimates.jsonl` when it changes.
Approximation marks, unknown-cost or not-booked warnings and reconciliation
explanations stay out of the main window, chart and HUD; the healing `?` help
still explains how healing is priced.

The chart only reads stored attribution (`live=False`); the streamer HUD stays
the single writer of attribution state.

While the main window is minimized the chart and cost-per-hour are not rebuilt
on live updates (the HUD keeps its own projection); they are rebuilt once, from
the current run, when the window is restored.
