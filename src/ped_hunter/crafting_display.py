"""Pure presentation of booked inputs plus evidenced excess, never a ledger write."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CostDisplay:
    booked: float
    excess_estimate: float
    unknown_groups: int
    cost: float | None
    net: float | None
    return_pct: float | None
    marker: str
    incomplete_reasons: tuple[str, ...] = ()

    @property
    def incomplete(self) -> bool:
        return bool(self.incomplete_reasons)


def crafting_cost_display(booked: float, returns: float, points, *,
                          projection_unavailable: bool = False,
                          unpriced_plans: int | None = None) -> CostDisplay:
    """Conserve the original booking, adding only supported excess ONCE.

    Missing prices leave actual returns and any booked costs usable. A partial
    net is before unknown inputs, never final profit; the rate is then withheld.
    All amounts remain PED; only the caller's renderer converts currency.
    """
    estimates = []
    unknown = 0
    for point in points:
        if point.get('kind') != 'craft' or point.get('cost') is not None:
            continue
        estimate = point.get('excess_input_estimate')
        if isinstance(estimate, (int, float)) and math.isfinite(estimate) and estimate >= 0:
            estimates.append(estimate)
        else:
            unknown += 1
    excess = math.fsum(estimates)
    known = math.fsum((booked, excess))
    if unpriced_plans is None:
        unpriced_plans = int(getattr(points, 'unpriced_craft_plans', 0))
    reasons = []
    if unknown:
        reasons.append(f'{unknown} groups have unknown additional inputs')
    if unpriced_plans:
        reasons.append(f'{unpriced_plans} saved setups have missing input prices')
    unpriced_heals = int(getattr(points, 'unpriced_heals', 0))
    if unpriced_heals:
        reasons.append(f'{unpriced_heals} healing observations have unknown inputs')
    if projection_unavailable:
        reasons.append('input projection unavailable')
    incomplete = bool(reasons)
    cost = None if incomplete and known <= 0 else known
    net = None if cost is None else returns - cost
    rate = 100 * returns / cost if cost is not None and cost > 0 and not incomplete else None
    return CostDisplay(booked, excess, unknown, cost, net, rate,
                       '*' if incomplete else '≈' if estimates else '', tuple(reasons))
