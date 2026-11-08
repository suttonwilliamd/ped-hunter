"""Read-only PED ranges for ordinary events and narrow event-local sections.

A clear relative gap in visible event envelopes, not an absolute jackpot price,
separates exceptional events. Each envelope includes its retained fan samples.
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


def plan_chart_scales(points: Sequence[Mapping], histories: Mapping) -> ChartScales:
    """Plan at most sixteen bounded event envelopes without changing their data.

    A threefold gap above an observed lower cohort is large enough to compress
    ordinary variation severely. Three ordinary peers are required when the
    visible window permits; short startup windows use the available peers.
    Separate multiple high-valued events together rather than letting a 600-PED
    event leave a 1.11-PED event on the ordinary axis. No event is discarded.
    """
    extents = [_extent(point, histories.get(point.get('event_id'), ())) for point in points]
    peaks = sorted(max(abs(low), abs(high)) for low, high in extents if low or high)
    boundary = None
    peers = min(3, len(peaks) - 1)
    for index in range(max(1, peers), len(peaks)):
        if peaks[index] / peaks[index - 1] >= 3.0:
            boundary = peaks[index - 1]
            break
    local = frozenset(index for index, (low, high) in enumerate(extents)
                      if boundary is not None and max(abs(low), abs(high)) > boundary)
    ordinary_extents = [extent for index, extent in enumerate(extents) if index not in local]
    ordinary = _range(min((low for low, _ in ordinary_extents), default=0.0),
                      max((high for _, high in ordinary_extents), default=0.0))
    return ChartScales(ordinary, tuple(_range(*extent) if index in local else ordinary
                                       for index, extent in enumerate(extents)), local)
