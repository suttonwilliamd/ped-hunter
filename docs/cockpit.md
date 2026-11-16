# Hunt cockpit (main window)

The main window is a cockpit: primary readouts across the top, a command rail on
the left, and the hunt chart above the existing tabs.

- **Readout strip**: hunt net (largest), return, cost per hour of active play
  (pauses of 10 minutes or more are not counted), loot, cost, events.
- **Command rail**: New run / Resume / End run and tracking state; setup
  quick-switch for the first four setups (buttons or Alt+1–4; the same order as
  the streamer HUD tiles); heal and HoT selectors with the per-use cost of a
  priced tool and the run's healing total; durability.
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
