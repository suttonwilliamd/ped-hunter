"""Read-only cost attribution for hunting and configured crafting return groups.

A manufacturing_offset is a booked batch and an event-order boundary, not a
chat timestamp and not a second cost to charge per receipt. Real crafting often
logs only received items: product, returned inputs, residue and shrapnel belong
to one observed group. Silent attempts cannot be reconstructed from chat.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from typing import Iterable, Mapping

from .parser import is_conversion_output_item


# The log has whole-second timestamps: components of one crafting receipt can
# cross a clock-second boundary. Ordinary hunting retains its narrower window.
CRAFT_RECEIPT_WINDOW_SECONDS = 1.25


@dataclass
class ManufacturingPlan:
    event_id: int
    blueprint: str
    attempts: int
    per_attempt: float | None
    booked_cost: float
    observed: int = 0
    active: bool = True
    returns: float = 0.0


class ActivityPoints(list):
    """List-compatible trend plus input context from the same streaming read."""
    def __init__(self):
        super().__init__()
        self.manufacturing: ManufacturingPlan | None = None
        self.has_crafting = False
        self.unknown_craft_groups = 0


@dataclass
class _Group:
    kind: str
    cost: float | None
    scope: object
    event_id: int
    plan: ManufacturingPlan | None = None
    inferred: bool = True
    loot: float = 0.0
    loot_events: int = 0
    last_loot_time: float | None = None
    attempt: int = 0


def _amount(value) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _time(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else None
    except (ValueError, OverflowError, OSError):
        return None


def build_activity_points(rows: Iterable[Mapping], cluster_window: float = 0.75) -> ActivityPoints:
    """Project rows in insertion order; never allocate, backfill or rebill data.

    Each manual setup starts a new batch at its saved position. Its price applies
    at most to the configured number of subsequent observed craft groups, until
    another setup or an outgoing attack. Excess groups have unknown cost, not
    fictitious zero-cost profit. Incoming damage/healing is not a mode switch.
    """
    result = ActivityPoints()
    plan = None
    crafting = False
    group = None
    numbers = {'hunt': 0, 'craft': 0}
    costs, counts, previous_profits = {}, {}, {}

    def finish():
        nonlocal group
        if group is None:
            return
        numbers[group.kind] += 1
        cost = group.cost
        if group.plan:
            group.plan.returns += group.loot
        profit = None if cost is None else group.loot - cost
        previous = previous_profits.get(group.scope)
        first = previous is None
        delta = None if profit is None else 0.0 if first else profit - previous
        symbol = '•' if first else '=' if delta is not None and math.isclose(delta, 0.0, abs_tol=1e-9) else '↑' if delta is not None and delta > 0 else '↓'
        average = None
        if cost is not None:
            costs[group.scope] = costs.get(group.scope, 0.0) + cost
            counts[group.scope] = counts.get(group.scope, 0) + 1
            average = costs[group.scope] / counts[group.scope]
        previous_profits[group.scope] = profit
        if group.kind == 'craft' and cost is None:
            result.unknown_craft_groups += 1
        result.append({
            'kill': len(result) + 1,  # Legacy key: point sequence, not confirmed kills.
            'kind': group.kind,
            'label': ('C' if group.kind == 'craft' else 'K') + str(numbers[group.kind]),
            'timestamp': '' if group.last_loot_time is None else datetime.fromtimestamp(group.last_loot_time).isoformat(timespec='seconds'),
            'loot': group.loot, 'cost': cost, 'avg_cost': average, 'profit': profit,
            'delta_profit': delta, 'trend_symbol': symbol, 'first_point': first,
            'loot_events': group.loot_events, 'event_id': group.event_id,
            'inferred': group.inferred, 'attempt_number': group.attempt,
            'plan_event_id': group.plan.event_id if group.plan else None,
        })
        group = None

    def begin_craft(event_id, payload=None, explicit=False):
        nonlocal group
        result.has_crafting = True
        cost, attempt = None, 0
        if plan:
            plan.observed += 1
            attempt = plan.observed
            if attempt <= plan.attempts:
                cost = plan.per_attempt
        elif payload:
            cost = _amount(payload.get('input_cost'))
        scope = ('craft', plan.event_id if plan else 'explicit')
        group = _Group('craft', cost, scope, event_id, plan, not explicit, attempt=attempt)

    for row in rows:
        try:
            payload = json.loads(row['payload'] or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        kind, event_id = row['kind'], int(row['id'])
        when = _time(row['timestamp'])
        if kind == 'manufacturing_offset':
            finish()
            if plan:
                plan.active = False
            try:
                total = float(payload.get('attempts_total', payload.get('attempts', 0)))
                attempts = int(total) if math.isfinite(total) and total.is_integer() and total > 0 else 0
            except (ValueError, TypeError, OverflowError):
                attempts = 0
            price = _amount(payload.get('cost_per_attempt'))
            booked = _amount(payload.get('total_cost'))
            if price is None and booked is not None and attempts:
                price = booked / attempts
            plan = ManufacturingPlan(event_id, str(payload.get('blueprint') or 'Unknown blueprint'),
                                     attempts, price, booked or 0.0)
            result.manufacturing, result.has_crafting = plan, True
            crafting = True
        elif kind == 'craft' and payload.get('result') in {'success', 'fail'}:
            finish()
            crafting = True
            if plan:
                plan.active = True
            begin_craft(event_id, payload, explicit=True)
        elif kind == 'combat':
            outgoing = ('damage' in payload or bool(payload.get('dodged')) or
                        payload.get('shot_cost') is not None or payload.get('ammo_cost') is not None)
            if not outgoing:
                continue
            if crafting or (group and group.loot_events):
                finish()
            crafting = False
            if plan:
                plan.active = False
            if group is None:
                group = _Group('hunt', 0.0, ('hunt', 0), event_id)
            price = _amount(payload.get('shot_cost'))
            if price is None:
                price = (_amount(payload.get('ammo_cost')) or 0.0) + (_amount(payload.get('repair_decay')) or 0.0)
            group.cost += price
        elif kind == 'loot':
            value = _amount(payload.get('value'))
            if value is None or value <= 0 or is_conversion_output_item(str(payload.get('item_name') or '')):
                continue
            if group and group.loot_events and when is not None and group.last_loot_time is not None:
                window = CRAFT_RECEIPT_WINDOW_SECONDS if crafting else cluster_window
                if when - group.last_loot_time > window or when < group.last_loot_time:
                    finish()
            if group is None:
                if crafting:
                    begin_craft(event_id)
                else:
                    group = _Group('hunt', 0.0, ('hunt', 0), event_id)
            group.loot += value
            group.loot_events += 1
            group.last_loot_time = when
    finish()
    return result
