"""Bounded histories of chart endpoints actually observed by the live HUD.

These are raw PED values, not an event replay or animation frames. Completed
and pre-feature groups acquire no history merely by appearing in a chart.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
import math
from typing import Protocol

TRAIL_MAX_SAMPLES = 24
_VISIBLE_GROUPS = 16
Sample = tuple[float | None, float | None, float]


class _TrailStore(Protocol):
    def load_chart_trails(self, session_id: str, event_ids: Sequence[int]) -> dict[int, tuple[Sample, ...]]: ...

    def save_chart_trails(self, session_id: str, trails: Mapping[int, Sequence[Sample]]) -> None: ...


def _sample(values: Sequence[object]) -> Sample:
    """Validate an endpoint without conflating unknown cost and zero cost."""
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError("A chart trail sample must contain cost, average cost and loot")
    result: list[float | None] = []
    for index, value in enumerate(values):
        if value is None and index < 2:
            result.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Chart trail values must be finite nonnegative numbers")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError("Chart trail values must be finite nonnegative numbers") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError("Chart trail values must be finite nonnegative numbers")
        result.append(number)
    return (result[0], result[1], result[2])  # type: ignore[return-value]


def _coordinates(sample: Sample) -> tuple[float | None, ...]:
    cost, average, loot = sample
    return cost, average, loot, loot - cost if cost is not None else None


def bounded_samples(samples: Sequence[Sample]) -> tuple[Sample, ...]:
    """Keep endpoints/extrema, thinning the least informative interior bends.

    Profit is included as a fourth coordinate, so a change in return can remain
    useful even when none of the individual series sets a new extreme. The
    working set never exceeds the cap plus one, even for a large input batch.
    """
    kept: list[Sample] = []
    for values in samples:
        sample = _sample(values)
        if not kept or sample != kept[-1]:
            kept.append(sample)
        if len(kept) <= TRAIL_MAX_SAMPLES:
            continue
        coordinates = [_coordinates(sample) for sample in kept]
        protected = {0, len(kept) - 1}
        scales: list[float] = []
        for column in range(4):
            known = [(row[column], index) for index, row in enumerate(coordinates) if row[column] is not None]
            if known:
                low = min(known)
                high = max(known)
                protected.update((low[1], high[1]))
                scales.append(max(high[0] - low[0], 1e-12))
            else:
                scales.append(1.0)

        def significance(index: int) -> float:
            before, current, after = coordinates[index - 1:index + 2]
            score = 0.0
            for column, scale in enumerate(scales):
                a, b, c = before[column], current[column], after[column]
                if a is None or b is None or c is None:
                    # Prefer retaining transitions into/out of unknown costs.
                    score += float(not (a is b is c))
                else:
                    score += abs(b - (a + c) / 2) / scale
            return score

        removable = (index for index in range(1, len(kept) - 1) if index not in protected)
        del kept[min(removable, key=significance)]
    return tuple(kept)


def _event_id(point: Mapping[str, object]) -> int | None:
    value = point.get("event_id")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


class TrailRecorder:
    """Record only observed live endpoints, with a last-16-group read cache."""

    def __init__(self, store: _TrailStore) -> None:
        self._store = store
        self._session_id: str | None = None
        self._histories: dict[int, tuple[Sample, ...]] = {}
        self._loaded: set[int] = set()
        self._active_id: int | None = None

    def update(
        self, session_id: str | None, points: Sequence[dict], live: bool,
        revised_event_ids: Collection[int] = (),
    ) -> dict[int, tuple[Sample, ...]]:
        if session_id != self._session_id:
            self._session_id = session_id
            self._histories.clear()
            self._loaded.clear()
            self._active_id = None
        if session_id is None:
            self._active_id = None
            return {}

        visible = points[-_VISIBLE_GROUPS:]
        by_id = {event_id: point for point in visible if (event_id := _event_id(point)) is not None}
        ids = set(by_id)
        self._histories = {event_id: history for event_id, history in self._histories.items() if event_id in ids}
        self._loaded.intersection_update(ids)
        missing = [event_id for event_id in by_id if event_id not in self._loaded]
        if missing:
            loaded = self._store.load_chart_trails(session_id, missing)
            self._histories.update({event_id: bounded_samples(history) for event_id, history in loaded.items() if event_id in ids and history})
            self._loaded.update(missing)
        revised_visible = ids.intersection(revised_event_ids)
        if live and revised_visible:
            # Allocation commits may already have atomically appended an
            # observed revision, including for a previously off-screen member.
            # Reload before appending so an older UI cache cannot erase it.
            refreshed = self._store.load_chart_trails(session_id, list(revised_visible))
            for event_id in revised_visible:
                if refreshed.get(event_id):
                    self._histories[event_id] = bounded_samples(refreshed[event_id])

        if not live:
            # A paused/closed-HUD gap cannot be reconstructed on the next call.
            self._active_id = None
            return dict(self._histories)

        latest_id = _event_id(visible[-1]) if visible else None
        observed = []
        if self._active_id in by_id and self._active_id != latest_id:
            observed.append(self._active_id)
        if latest_id is not None:
            observed.append(latest_id)
        # Reconsidered allocations are new observations of an earlier endpoint,
        # not a reconstruction of its missing past. Only extend existing trails.
        observed.extend(event_id for event_id in by_id
                        if event_id in revised_event_ids and self._histories.get(event_id)
                        and event_id not in observed)
        changed: dict[int, tuple[Sample, ...]] = {}
        for event_id in observed:
            point = by_id[event_id]
            sample = _sample((point.get("cost"), point.get("avg_cost"), point.get("loot")))
            history = self._histories.get(event_id, ())
            if not history or sample != history[-1]:
                changed[event_id] = bounded_samples((*history, sample))
        if changed:
            # Commit before changing cached observations, so storage errors can
            # be retried without silently losing an observed endpoint.
            self._store.save_chart_trails(session_id, changed)
            self._histories.update(changed)
        self._active_id = latest_id
        return dict(self._histories)
