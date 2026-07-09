# Watchdog Integration Plan for PED Hunter

## Goal

Replace the current file-size polling loop for `chat.log` ingestion with an event-driven watcher so PED Hunter reacts faster to combat bursts, keeps the streamer overlay responsive, and avoids unnecessary CPU churn.

## Recommended architecture

### Primary path

1. `watchdog` watches the directory containing `chat.log`.
2. File change events signal the ingestion worker.
3. The worker reads only newly appended bytes from the last known offset.
4. The parser turns raw lines into events.
5. The store persists events.
6. A live in-memory session snapshot updates immediately.
7. The UI thread drains queued updates and refreshes live widgets right away.
8. Expensive dashboard/session-list/chart refreshes stay debounced.

### Optional robustness path

`pygtail` can be added later for offset persistence and rotation handling.
It is useful when `chat.log` is truncated, replaced, or rotated during play.
It is not required for the main responsiveness win.

## Phase 1 — Add watchdog as the trigger

### What to change

- Add a small watcher helper/module, for example `src/ped_hunter/log_watcher.py`.
- Watch the parent directory of `chat.log`, not just the file itself.
- Filter events to the exact `chat.log` path.
- Use the observer only to signal work; do not parse inside the watchdog callback.

### Notes

- Directory watching is more resilient if the file is replaced or rotated.
- Keep a stop event so the watcher shuts down cleanly when tracking stops.
- Treat modification, creation, and move/replace events as wakeup signals.

## Phase 2 — Make ingestion event-driven

### Worker behavior

- Keep track of the current file offset.
- When signaled, open `chat.log` once.
- Seek to the last offset.
- Read only the appended lines.
- Update the offset.
- Parse and store the new events.

### Burst handling

- Coalesce rapid successive file events into a single drain cycle.
- Process all pending bytes in one pass.
- Emit one UI refresh per drain cycle rather than one refresh per line.

This is especially important when multiple enemies flood the log.

## Phase 3 — Preserve the fast snapshot/UI path

The existing live snapshot approach should stay in place.

### Keep these rules

- Do not query SQLite on every streamer update.
- Do not mutate Tk widgets off the main thread.
- Do not recompute the whole session summary for every combat line.

### Split refresh responsibility

- **Fast path:** loot, cost, return, net, events, damage, durability.
- **Slow path:** tables, charts, recent session lists, lifetime aggregates.

## Phase 4 — Add rotation and truncation safety

### Option A: custom offset handling only

Keep the current incremental reader and add checks for:

- file size smaller than the last offset
- file replacement or rotation
- temporary disappearance of the file

If the file shrinks, reset the offset to zero and continue.

### Option B: use pygtail

Use `pygtail` if you want stronger resume/rotation behavior.
It is better at tracking unread lines across restarts and rotation.

### Best combined use

- `watchdog` = wakeup trigger
- `pygtail` = offset/rotation-aware reader

That combination is best when you want both responsiveness and robustness.

## Phase 5 — Handle combat bursts cleanly

- Wake quickly on file changes.
- Coalesce rapid events.
- Keep the worker hot during active combat.
- Back off when no new data arrives.
- Keep the streamer overlay fed from the latest in-memory snapshot.

## Phase 6 — Testing plan

### Unit tests

Add coverage for:

1. Watchdog event filtering: only the target `chat.log` path triggers work.
2. Offset advancement: appended lines are consumed once.
3. Truncation/rotation: shrinking or replacement resets or reinitializes correctly.
4. Burst coalescing: multiple rapid events produce one drain cycle.
5. Live snapshot updates: overlay reads from the snapshot, not the database.
6. Resume/restart behavior: state restores cleanly.

### Integration tests

- Write to a temporary log file.
- Simulate append, truncate, and replace behavior.
- Verify parser/store events are created.
- Verify the live snapshot updates quickly.

### Manual verification

Run the GUI and confirm:

- attacks appear quickly after they hit the log
- multiple hits do not freeze the overlay
- the old comparison window can stay open
- long play sessions do not degrade responsiveness

## Phase 7 — Rollout plan

1. Introduce watchdog behind a feature flag or config toggle.
2. Keep polling as a fallback.
3. Test against the real live `chat.log`.
4. Make watchdog the default once stable.
5. Keep a fallback path for environments where watchdog is unavailable.

## Optional pygtail instructions

### Install

Add `pygtail` as an optional dependency so startup still works without it.

### Use it for

- offset persistence
- log rotation handling
- restart recovery

### Do not use it for

- event notification
- UI updates
- expensive recomputation

### Recommended pattern

1. `watchdog` says the file changed.
2. `pygtail` yields unread lines.
3. The parser/store process those lines.
4. The live snapshot updates.
5. The UI refreshes on the main thread.

## Implementation order

1. Add the watchdog watcher.
2. Refactor ingestion to be event-driven.
3. Keep the current live snapshot path.
4. Add truncation and rotation handling.
5. Add tests.
6. Optionally switch the reader to pygtail.
7. Verify in the live GUI.
8. Package and release only after the behavior is stable.

## Recommendation

Start with **watchdog only**.
That gives the biggest responsiveness win.
Add **pygtail** later only if rotation or restart persistence becomes a real issue.
