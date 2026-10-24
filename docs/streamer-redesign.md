# Streamer HUD redesign — v0.3.33

Design by **BobRoss.bot**, Paperclip task **WIL-15**, 24 October 2026.
Implementation by Aaron / Pi Coding Assistant.

## v0.3.37 crafting-cost attribution

The owner's live log records crafting as received product, returned materials,
residue and shrapnel, without the explicit craft-success/failure notices expected
by the old allocator. The batch budget was already in the session ledger, but
the hunting-only chart ignored it and the manufacturing row stayed hidden.

- Project relevant events in insertion order so timestamp-less manual setup
  records keep their actual boundary. A saved setup is available before results.
- Attribute one configured TT input to each observed crafting receipt group,
  including partial/failed returns with no crafted product. Allow adjacent clock
  seconds inside a craft receipt; keep ordinary hunting's narrower grouping.
- Honor explicit craft notices without counting their output again. Do not infer
  completely silent failures or extrapolate input cost beyond the saved budget.
- Use C/K labels and Groups*, with per-batch crafting averages/deltas and separate
  hunting averages. The chart remains PED; unknown costs create gaps/? markers,
  never zero-cost profit. Current over-budget/unpriced crafting suppresses a
  misleading net/return; historical attribution gaps remain disclosed in the
  footer while booked session totals remain available for subsequent activity.
- Label the hero as booked whole-session net/return and the cost card as booked
  cost. The crafting footer shows blueprint and observed/planned groups; the
  former durability-text slot shows batch returns, booked cost and booked net.
  Incoming damage/healing does not disarm crafting; outgoing attacks restore
  weapon context. Last saved TT input remains visible afterward.
- One pure, read-only projection supplies both chart and setup context. Existing
  session billing remains unchanged; no historical allocations are invented or
  persisted. Relevant kinds are streamed with snapshot-key caching.
- All v0.3.36 fixed allocations remain in place, including during crafting
  progress, quiet reset, currency changes and return to hunting.

## v0.3.36 layout-stability correction

Live updates must not re-run the overlay's global fitting loop. The v0.3.35
text-length cache and wrapped activity height could repeatedly change fonts,
stacking and section positions without a user resize, especially during numeric
animation or long skill arrivals.

- Keep the two-column hero and three-column metrics in fixed allocations.
- Reserve stable heights for activity, latest-point summary, durability and
  footer. Activity is exactly two single-line slots; presentation may ellipsize
  long text, while the complete original headline/detail remain in memory.
- Global sizing uses only the window rectangle and whether the manufacturing
  section is present, not current strings. Recompute on actual resize or an
  explicit section visibility change, not on animation/data ticks or movement.
- Fit static template heights without pumping idle events between candidates;
  intermediate font/layout candidates must never be painted.
- Exceptionally long monetary values may shrink only their own independent
  field font. Do not expand it again between ordinary values/animation frames;
  restore the font budget on the next actual layout change. Never abbreviate
  monetary precision or rearrange other sections to fit it.
- Nine new regression cases check fixed positions/fonts across digit thresholds,
  animation frames, long activity and quiet resets, zero-cost transitions,
  manufacturing attempts and large money. Stability runs at default, minimum
  and enlarged sizes; the existing resize/DPI tests remain in place.

This correction supersedes earlier content-driven reflow and activity wrapping
requirements below. The 360×330 default and 260×240 minimum are unchanged.

## v0.3.35 sizing correction

The owner found the original redesign too large and its resize minimum sticky.
The implementation now defaults to **360×330** at 100% scaling, with a fixed
**260×240** minimum. These dimensions and the compact responsive typography
supersede the original geometry/point-size targets below. Overlay-local pixel
fonts apply display scaling once and adapt within the user's rectangle; the main
window and global Tk scaling are untouched. Default body text is 10px, hero 26px,
return 18px and metric values 14px before DPI/content scaling.

Wrapped labels no longer determine the window minimum. Refreshes reflow content
without rewriting geometry. Every edge/corner and the footer grip support mouse
resizing, with a captured drag and release cleanup. Regression tests drive the
actual Tk bindings, cover 100/125/150/200% Tk scaling, reverse enlargement, and
verify live updates preserve smaller dimensions and visible footer controls.

## Original design direction

A quiet broadcast instrument, not a miniature dashboard: one dominant result,
one honest tracking state, one compact trend. BobRoss reviewed the repository
at f32c52c; the design handoff was code-grounded, not a claimed Windows visual review.

### Layout and typography

- Target 440×400 at 100% scaling, 420×400 minimum before measured content needs.
  Respect display scaling, readable fonts, and essential text over fixed height.
- Opaque borderless always-on-top window, 1px border, 12px interior padding,
  related 4px gaps and section 8px gaps.
- Persistent state/close in header; two-column net profit and return hero.
- Net 24pt bold, return 16pt bold, metric values 12pt bold, supporting text 9pt.
- Shared Loot/Cost/Kills surface instead of three heavy cards. Large amounts
  reflow into rows; no monetary abbreviation or precision loss.
- Compact activity area, conditional manufacturing row, PED chart with inline
  legend and inferred-kill disclosure, latest exact summary, durability, footer.
- Loadout alone may ellipsize, with full name available on focus or hover.

### Palette

| Role | Hex |
| --- | --- |
| Background | `#0B1220` |
| Surface | `#131E2E` |
| Border | `#334155` |
| Primary text | `#F1F5F9` |
| Supporting text | `#B8C5D6` |
| Accent/tracking | `#5EEAD4` |
| Positive | `#86EFAC` |
| Cost/warning | `#FDBA74` |
| Negative | `#FDA4AF` |
| Average cost | `#7DD3FC` |

Signs, labels, markers and dashed average lines communicate without relying on
color. Return remains neutral rather than being profit-colored.

### Truthful state and feedback

`TRACKING` requires a displayed unended session and running ingestion state.
Retained unended sessions are `PAUSED`; ended displayed sessions are `STOPPED`.
No session is `IDLE`, or `WAITING FOR RUN` when ingestion is running without a
valid displayed session. Existing Stop clears the live session and becomes Idle;
this redesign does not invent a pause workflow or change tracking lifecycle.

No session or no cost means no meaningful return: show an em dash. Preserve
net/loot/cost currency formatting, conditional manufacturing input in PED and
attempt counts. Preview retains DEMO branding and a synthetic-money warning.

Bundled loot and real skill counts remain in the activity area. Arrival copy
returns to quiet text after four seconds. Session changes/resume establish a
quiet baseline. Reduced motion keeps immediate values and readable arrival copy
without motion. Main-dashboard activity presentation and sound policy remain
unchanged.

### Chart and equipment

- Cost, cumulative average cost and profit remain visible; plot the latest 16
  points but count the complete history. Chart values remain PED in USD mode.
- Label `Kills*` and `* Inferred from event clusters`: the existing algorithm is
  an inference, not an authoritative kill count. No financial formula changes.
- Measured axis gutter, readable signed ticks, zero baseline, first/latest kill
  indices, markers for a single point. No decorative fill or tiny per-point arrows.
- Latest profit, direction/delta, exact cost and cumulative average remain in a
  wrapped summary. Distinguish query failure from a real empty chart.
- Preserve durability role/percentage/approximate shots, use a neutral unfilled
  track for unknown durability, orange at <=30%, rose at <=10%, readable EMPTY
  label at zero. Durability remains an estimate based on existing inputs.

### Interaction

Preserve fit-before-show and initial bottom-right placement 16px inside the
monitor's usable work area. Drag and resize never snap back on refresh. Content
minimum must not ratchet upward after user enlargement. Resize grows chart, not
fonts. Header/background drag; release clears drag state. Escape and close only
dismiss the overlay. Close and move/resize targets support keyboard focus;
arrows move and Shift+arrows resize by 10px, clamped to work area/minimum.

## Verification scope

Automated regression coverage exercises monetary reflow, inferred counts beyond
16, empty/error chart distinction and recovery, manufacturing row visibility,
known/unknown/empty durability, keyboard geometry, persistent demo/retained states,
arrival expiry/reduced motion, placement and reversible minimum sizing. Temporary
synthetic databases are used, never the player's logs or history.

Windows screenshots are captured separately from isolated synthetic sessions.
Tk scaling checks are not a substitute for testing every real monitor's Windows
DPI configuration or broadcast pipeline. Native multi-monitor and broadcast
capture acceptance should be verified on the user's actual setup.
