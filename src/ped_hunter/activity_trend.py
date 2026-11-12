"""Read-only, incremental attribution of booked inputs to observed return groups.

Silent attempts cannot be reconstructed from chat. A saved crafting setup is
an event-order boundary and a booked batch, not a second charge per receipt.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
from typing import Iterable, Mapping

from .parser import is_conversion_output_item

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
        self.last_event_id = 0
        self.attribution_boundary_id = 0


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
    last_event_id: int = 0
    shot_count: int = 0
    first_event_time: float | None = None
    last_event_time: float | None = None
    first_loot_time: float | None = None
    context_key: str | None = None
    context_mixed: bool = False
    timing_valid: bool = True


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


class ActivityTrendBuilder:
    """Append-only projection retaining the unfinished receipt across refreshes.

    Snapshots never finalize that receipt or share mutable plan state, so a
    residue line arriving on the next poll updates the same point and old
    snapshots remain suitable for animation. Recreate after history edits.
    """
    def __init__(self, cluster_window: float = 0.75):
        self.cluster_window = cluster_window
        self.points = ActivityPoints()
        self.plan = None
        self.crafting = False
        self.group = None
        self.numbers = {'hunt': 0, 'craft': 0}
        self.costs, self.counts, self.previous_profits = {}, {}, {}
        self.last_event_id = 0
        self._attribution_epoch = 0
        self._last_shot_context: str | None = None
        self._attribution_boundary_id = 0

    def _point(self, group, closed=False):
        cost = group.cost
        profit = None if cost is None else group.loot - cost
        previous = self.previous_profits.get(group.scope)
        first = previous is None
        delta = None if profit is None else 0.0 if first else profit - previous
        symbol = '•' if first else '=' if delta is not None and math.isclose(delta, 0.0, abs_tol=1e-9) else '↑' if delta is not None and delta > 0 else '↓'
        average = None if cost is None else (self.costs.get(group.scope, 0.0) + cost) / (self.counts.get(group.scope, 0) + 1)
        return {
            'kill': len(self.points) + 1,
            'kind': group.kind,
            'label': ('C' if group.kind == 'craft' else 'K') + str(self.numbers[group.kind] + 1),
            'timestamp': '' if group.last_loot_time is None else datetime.fromtimestamp(group.last_loot_time).isoformat(timespec='seconds'),
            'loot': group.loot, 'cost': cost, 'avg_cost': average, 'profit': profit,
            'delta_profit': delta, 'trend_symbol': symbol, 'first_point': first,
            'loot_events': group.loot_events, 'event_id': group.event_id,
            'inferred': group.inferred, 'attempt_number': group.attempt,
            'plan_event_id': group.plan.event_id if group.plan else None,
            'last_event_id': group.last_event_id,
            'shot_count': group.shot_count,
            'first_event_time': group.first_event_time if group.timing_valid else None,
            'last_event_time': group.last_event_time if group.timing_valid else None,
            'first_loot_time': group.first_loot_time if group.timing_valid else None,
            'context_key': group.context_key,
            'context_mixed': group.context_mixed,
            'closed': closed,
        }

    def _finish(self):
        group = self.group
        if group is None:
            return
        point = self._point(group, closed=True)
        self.points.append(point)
        self.numbers[group.kind] += 1
        if group.plan:
            group.plan.returns += group.loot
        if group.cost is not None:
            self.costs[group.scope] = self.costs.get(group.scope, 0.0) + group.cost
            self.counts[group.scope] = self.counts.get(group.scope, 0) + 1
        self.previous_profits[group.scope] = point['profit']
        if group.kind == 'craft' and group.cost is None:
            self.points.unknown_craft_groups += 1
        self.group = None

    def _begin_craft(self, event_id, payload=None, explicit=False):
        self.points.has_crafting = True
        cost, attempt = None, 0
        if self.plan:
            self.plan.observed += 1
            attempt = self.plan.observed
            if attempt <= self.plan.attempts:
                cost = self.plan.per_attempt
        elif payload:
            cost = _amount(payload.get('input_cost'))
        scope = ('craft', self.plan.event_id if self.plan else 'explicit')
        self.group = _Group('craft', cost, scope, event_id, self.plan, not explicit, attempt=attempt)

    def _touch_group(self, event_id, when):
        group = self.group
        if not group.last_event_id:
            group.first_event_time = when
        if when is None or (group.last_event_time is not None and when < group.last_event_time):
            group.timing_valid = False
        group.last_event_id = event_id
        group.last_event_time = when

    def _attribution_boundary(self, event_id):
        self._attribution_epoch += 1
        self._attribution_boundary_id = event_id
        self._last_shot_context = None
        if self.group and self.group.kind == 'hunt' and not self.group.loot_events:
            self.group.context_mixed = True
            self.group.context_key = None

    def extend(self, rows: Iterable[Mapping]) -> None:
        for row in rows:
            event_id = int(row['id'])
            self.last_event_id = max(self.last_event_id, event_id)
            try:
                payload = json.loads(row['payload'] or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            kind = row['kind']
            when = _time(row['timestamp'])
            if kind in {'weapon', 'repair'}:
                # Context only: do not alter the established grouping or bill a
                # repair again. Attribution must not borrow across this boundary.
                self._attribution_boundary(event_id)
            elif kind == 'manufacturing_offset':
                self._attribution_boundary(event_id)
                self._finish()
                if self.plan:
                    self.plan.active = False
                try:
                    total = float(payload.get('attempts_total', payload.get('attempts', 0)))
                    attempts = int(total) if math.isfinite(total) and total.is_integer() and total > 0 else 0
                except (ValueError, TypeError, OverflowError):
                    attempts = 0
                price = _amount(payload.get('cost_per_attempt'))
                booked = _amount(payload.get('total_cost'))
                if price is None and booked is not None and attempts:
                    price = booked / attempts
                self.plan = ManufacturingPlan(event_id, str(payload.get('blueprint') or 'Unknown blueprint'), attempts, price, booked or 0.0)
                self.points.has_crafting = True
                self.crafting = True
            elif kind == 'craft' and payload.get('result') in {'success', 'fail'}:
                if not self.crafting:
                    self._attribution_boundary(event_id)
                self._finish()
                self.crafting = True
                if self.plan:
                    self.plan.active = True
                self._begin_craft(event_id, payload, explicit=True)
                self._touch_group(event_id, when)
            elif kind == 'combat':
                outgoing = ('damage' in payload or bool(payload.get('dodged') or payload.get('outgoing_miss')) or payload.get('shot_cost') is not None or payload.get('ammo_cost') is not None)
                if not outgoing:
                    continue
                if self.crafting:
                    self._attribution_boundary(event_id)
                if self.crafting or (self.group and self.group.loot_events):
                    self._finish()
                self.crafting = False
                if self.plan:
                    self.plan.active = False
                if self.group is None:
                    self.group = _Group('hunt', 0.0, ('hunt', 0), event_id)
                price = _amount(payload.get('shot_cost'))
                if price is None:
                    price = (_amount(payload.get('ammo_cost')) or 0.0) + (_amount(payload.get('repair_decay')) or 0.0)
                context = json.dumps((self._attribution_epoch, str(payload.get('loadout') or ''), price),
                                     separators=(',', ':'))
                if self.group.shot_count and self.group.context_key != context:
                    self.group.context_mixed = True
                self.group.context_key = None if self.group.context_mixed else context
                self._last_shot_context = context
                self.group.shot_count += 1
                self._touch_group(event_id, when)
                self.group.cost += price
            elif kind == 'loot':
                value = _amount(payload.get('value'))
                if value is None or value <= 0 or is_conversion_output_item(str(payload.get('item_name') or '')):
                    continue
                if self.group and self.group.loot_events and when is not None and self.group.last_loot_time is not None:
                    window = CRAFT_RECEIPT_WINDOW_SECONDS if self.crafting else self.cluster_window
                    if when - self.group.last_loot_time > window or when < self.group.last_loot_time:
                        self._finish()
                if self.group is None:
                    if self.crafting:
                        self._begin_craft(event_id)
                    else:
                        self.group = _Group('hunt', 0.0, ('hunt', 0), event_id,
                                            context_key=self._last_shot_context)
                self._touch_group(event_id, when)
                if not self.group.loot_events:
                    self.group.first_loot_time = when
                self.group.loot += value
                self.group.loot_events += 1
                self.group.last_loot_time = when

    def snapshot(self) -> ActivityPoints:
        result = ActivityPoints()
        result.extend(self.points)
        result.has_crafting = self.points.has_crafting
        result.unknown_craft_groups = self.points.unknown_craft_groups
        result.manufacturing = replace(self.plan) if self.plan else None
        result.last_event_id = self.last_event_id
        result.attribution_boundary_id = self._attribution_boundary_id
        if self.group:
            result.append(self._point(self.group))
            if self.group.kind == 'craft' and self.group.cost is None:
                result.unknown_craft_groups += 1
            if result.manufacturing and self.group.plan is self.plan:
                result.manufacturing.returns += self.group.loot
        return result


def build_activity_points(rows: Iterable[Mapping], cluster_window: float = 0.75) -> ActivityPoints:
    """Project a complete history without allocating, backfilling or rebilling."""
    builder = ActivityTrendBuilder(cluster_window)
    builder.extend(rows)
    return builder.snapshot()
