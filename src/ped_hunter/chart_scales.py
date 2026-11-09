"""Read-only PED ranges for ordinary events and narrow event-local sections.

A relative gap must also be exceptional in the visible event distribution.
Each event has one envelope/vote, including its retained fan samples.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ValueRange:
    low: float
    high: float

    def fraction(self, value: float) -> float:
        span = self.high - self.low
        if math.isfinite(span):
            return (value - self.low) / span
        # Retain signed values even at the finite float extremes.
        magnitude = max(abs(self.low), abs(self.high))
        return (value / magnitude - self.low / magnitude) / (self.high / magnitude - self.low / magnitude)


@dataclass(frozen=True)
class ChartScales:
    ordinary: ValueRange
    events: tuple[ValueRange, ...]
    local_indices: frozenset[int]
    event_ids: tuple[object, ...]

    def connected(self, left: int, right: int) -> bool:
        """Only ordinary events share an axis; adjacent local sections do not."""
        return left not in self.local_indices and right not in self.local_indices


def _extent(point, history) -> tuple[float, float]:
    low = high = 0.0
    values = (point.get('cost'), point.get('avg_cost'), point.get('loot'))
    for sample in (values, *history):
        for value in sample:
            if value is None or isinstance(value, bool):
                continue
            number = float(value)
            if math.isfinite(number):
                low, high = min(low, number), max(high, number)
    return low, high


def _range(low: float, high: float) -> ValueRange:
    return ValueRange(low, high if high != low else low + 1.0)


def plan_chart_scales(points: Sequence[Mapping], histories: Mapping, *,
                      previous: ChartScales | None = None) -> ChartScales:
    """Adapt the ordinary axis to the bounded visible distribution.

    A threefold gap alone does not make an upper cohort exceptional. It must
    also exceed the upper Tukey fence (Q3 + 1.5 IQR) of event-envelope peaks.
    Normalize before interpolation so finite extreme values cannot overflow.
    Short startup windows retain the gap rule until four visible events exist.

    Previous event identities provide display-only hysteresis: ordinary peers
    need the outer 3-IQR fence to become local; a local cohort stays local down
    to the 1-IQR fence/twofold gap. Unseen/mixed cohorts use the initial fence.
    No previous samples, axes or classifications are written into session data.
    """
    extents = [_extent(point, histories.get(point.get('event_id'), ())) for point in points]
    event_ids = tuple(point.get('event_id') for point in points)
    ranked = sorted((max(abs(low), abs(high)), index)
                    for index, (low, high) in enumerate(extents) if low or high)
    prior = ({event_id: index in previous.local_indices
              for index, event_id in enumerate(previous.event_ids) if event_id is not None}
             if previous is not None else {})
    peaks = [peak for peak, _ in ranked]
    normalized = [peak / peaks[-1] for peak in peaks]

    def quartile(fraction):
        position = (len(normalized) - 1) * fraction
        left = int(position)
        weight = position - left
        return normalized[left] * (1 - weight) + normalized[min(left + 1, len(normalized) - 1)] * weight

    q1, q3 = (quartile(.25), quartile(.75)) if peaks else (0.0, 0.0)
    boundary = None
    peers = min(3, len(peaks) - 1)
    for index in range(max(1, peers), len(peaks)):
        ratio = peaks[index] / peaks[index - 1]
        if ratio < 2:
            continue
        # Inspect only the next multiplicative cohort, not a distant jackpot:
        # an existing 600-PED section must not make a new ordinary cohort sticky.
        end = index + 1
        while end < len(peaks) and peaks[end] / peaks[end - 1] < 3:
            end += 1
        known = [prior[event_ids[point_index]] for _, point_index in ranked[index:end]
                 if event_ids[point_index] in prior]
        coefficient, gap = 1.5, 3.0
        if known and all(known):
            coefficient, gap = 1.0, 2.0
        elif known and not any(known):
            coefficient = 3.0
        fence = q3 + coefficient * (q3 - q1)
        exceptional = (normalized[index] > fence and
                       not math.isclose(normalized[index], fence, rel_tol=1e-12, abs_tol=0))
        if ratio >= gap and (len(peaks) < 4 or exceptional):
            boundary = peaks[index - 1]
            break
    local = frozenset(index for index, (low, high) in enumerate(extents)
                      if boundary is not None and max(abs(low), abs(high)) > boundary)
    ordinary_extents = [extent for index, extent in enumerate(extents) if index not in local]
    ordinary = _range(min((low for low, _ in ordinary_extents), default=0.0),
                      max((high for _, high in ordinary_extents), default=0.0))
    return ChartScales(ordinary, tuple(_range(*extent) if index in local else ordinary
                                       for index, extent in enumerate(extents)), local, event_ids)
