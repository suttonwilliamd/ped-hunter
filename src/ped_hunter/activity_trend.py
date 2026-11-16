"""Read-only, incremental attribution of booked inputs to observed return groups.

Silent attempts cannot be reconstructed from chat. A saved crafting setup is
an event-order boundary and a booked batch, not a second charge per receipt.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import itertools
import json
import math
from typing import Iterable, Mapping

from .parser import is_conversion_output_item

CRAFT_RECEIPT_WINDOW_SECONDS = 1.25


_HISTORIES = itertools.count(1)


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
    context_intact: bool = True


class ActivityPoints(list):
    """List-compatible trend plus input context from the same streaming read."""
    def __init__(self):
        super().__init__()
        self.manufacturing: ManufacturingPlan | None = None
        self.has_crafting = False
        self.unknown_craft_groups = 0
        self.unpriced_craft_plans = 0
        self.last_event_id = 0
        self.attribution_boundary_id = 0
        self.accounting_revision = 0
        self.healing_expenses = ()
        self.healing_contexts = {}
        self.healing_invalidated = frozenset()
        self.healing_booked = 0.0
        self.healing_allocated = 0.0
        self.healing_pending = 0.0
        self.healing_pending_reasons = {}
        self.unpriced_heals = 0
        # (builder history, rows consumed): equal versions mean equal content.
        self.source_version = None


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
    recipe_output_seen: bool = False
    explicit_input: float | None = None
    recipe_context_intact: bool = True
    recipe_context_epoch: int = 0


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
        self.reset()

    def reset(self, accounting_revision: int = 0) -> None:
        """History membership changed; an append cursor cannot repair old groups."""
        self.accounting_revision = accounting_revision
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
        self._healing_expenses = []
        self._healing_contexts = {}
        self._healing_invalidated = set()
        self._incoming_before_outgoing = False
        self._unpriced_heals = 0
        self._last_craft_event_time = None
        self._history = next(_HISTORIES)  # a new identity for every reset; equal across copies
        self._rows_seen = 0
        self._snapshot = None

    def _point(self, group, closed=False):
        # A display estimate is NOT another booked input or attributed cost.
        # A saved price alone does not establish the recipe of excess receipts.
        excess_estimate, excess_basis = None, None
        if group.plan and group.attempt > group.plan.attempts:
            if group.explicit_input is not None:
                excess_estimate, excess_basis = group.explicit_input, 'saved explicit input'
            elif (group.recipe_output_seen and group.recipe_context_intact and
                  group.timing_valid and group.plan.per_attempt is not None):
                excess_estimate = group.plan.per_attempt
                excess_basis = 'matching recipe output; uninterrupted saved setup'
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
            'input_cost': cost, 'weapon_cost': cost if group.kind == 'hunt' else None,
            'cost_scope': group.scope,
            'excess_input_estimate': excess_estimate,
            'excess_input_basis': excess_basis,
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
        self.group = _Group('craft', cost, scope, event_id, self.plan, not explicit, attempt=attempt,
                            explicit_input=_amount(payload.get('input_cost')) if explicit and payload else None,
                            recipe_context_intact=bool(self.plan and self.plan.context_intact),
                            recipe_context_epoch=self._attribution_epoch)

    def _springs_resume_signal(self, item, when):
        # Narrow interrupted-batch recovery: generic oils/Shrapnel are also
        # hunting loot. These distinctive receipts support an inferred return
        # to the saved Springs plan, not proof of any silent clicks or a bill.
        if (self.crafting or not self.plan or when is None or
                self.plan.blueprint.strip().casefold() != 'simple 1 plastic springs blueprint' or
                item.strip().casefold() not in {'simple 1 plastic springs', 'animal oil residue'}):
            return False
        group = self.group
        return (group is None or (group.timing_valid and group.last_event_time is not None
                                 and when >= group.last_event_time))

    def _resume_springs_receipt(self, event_id, when):
        pending = self.group
        if pending is not None and (
                pending.kind != 'hunt' or pending.shot_count or not pending.loot_events or
                pending.event_id <= self._attribution_boundary_id or
                pending.first_loot_time is None or
                not 0 <= when - pending.first_loot_time <= CRAFT_RECEIPT_WINDOW_SECONDS):
            return
        # A generic input at the next clock second may already have closed
        # the first input under the shorter hunting window. Rejoin only that
        # bounded, shotless tail after a distinctive signal confirms the inferred
        # receipt. Until then ordinary hunting grouping remains unchanged.
        while pending is not None and self.points:
            prefix = self.points[-1]
            first = prefix.get('first_loot_time')
            last = prefix.get('last_event_time')
            if (prefix['kind'] != 'hunt' or prefix['shot_count'] or prefix['cost'] != 0 or
                    first is None or last is None or
                    prefix['event_id'] <= self._attribution_boundary_id or
                    not 0 <= when - first <= CRAFT_RECEIPT_WINDOW_SECONDS or
                    last > pending.first_loot_time):
                break
            self.points.pop()
            self.numbers['hunt'] -= 1
            scope = prefix['cost_scope']
            self.counts[scope] -= 1  # This retracted hunting input was zero.
            self.previous_profits.pop(scope, None)
            for previous in reversed(self.points):
                if previous['cost_scope'] == scope:
                    self.previous_profits[scope] = previous['profit']
                    break
            pending = replace(pending, event_id=prefix['event_id'],
                              loot=prefix['loot'] + pending.loot,
                              loot_events=prefix['loot_events'] + pending.loot_events,
                              first_event_time=prefix['first_event_time'], first_loot_time=first)
        self._attribution_boundary(event_id)
        self.crafting = True
        self.plan.active = True
        self._begin_craft(pending.event_id if pending else event_id)
        if pending is not None:
            # Promote the unfinished receipt, including input oils delivered on
            # an earlier poll. Never discard/rebook it or increment per line.
            self.group = replace(pending, kind='craft', cost=self.group.cost,
                                 scope=self.group.scope, plan=self.plan,
                                 attempt=self.group.attempt, inferred=True,
                                 context_key=None)

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

    def _observe_healing(self, event_id, payload):
        selection = payload.get('healing_selection')
        if isinstance(selection, dict) and selection.get('version') == 2:
            group = self.group
            if self._incoming_before_outgoing:
                context = (None, 'Incoming combat has no supported outgoing encounter boundary')
            elif self.crafting or not group or group.kind != 'hunt':
                context = (None, 'No supported hunting encounter at activation')
            elif group.context_mixed or self._attribution_boundary_id > group.last_event_id:
                context = (None, 'Equipment/accounting boundary makes the encounter uncertain')
            else:
                context = (group.event_id, 'previous' if group.loot_events else 'upcoming')
            self._healing_contexts[event_id] = context
        invalidated = payload.get('healing_invalidates_activation_ids')
        if isinstance(invalidated, list):
            self._healing_invalidated.update(key for key in invalidated if type(key) is int and key > 0)
        cost = _amount(payload.get('healing_cost'))
        if cost is not None:
            anchor = payload.get('healing_activation_event_id')
            if type(anchor) is not int or anchor <= 0:
                anchor = None
            self._healing_expenses.append((
                event_id, anchor, cost,
                payload.get('healing_allocation_eligible') is True,
                payload.get('healing_allocation_reason') or 'Legacy healing has no saved allocation context; not backfilled',
            ))
        if payload.get('healing_cost_status') in {
            'not_selected', 'tool_not_found', 'missing_timestamp', 'unresolved_activation', 'unknown_cost',
        }:
            self._unpriced_heals += 1

    def extend(self, rows: Iterable[Mapping]) -> None:
        for row in rows:
            self._rows_seen += 1
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
            if kind == 'combat' and ('healing_action' in payload or 'healing_cost' in payload):
                self._observe_healing(event_id, payload)
            if kind in {'weapon', 'repair'}:
                if self.plan:
                    self.plan.context_intact = False
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
                if price is None:
                    self.points.unpriced_craft_plans += 1
                self._last_craft_event_time = None
                self.points.has_crafting = True
                self.crafting = True
            elif kind == 'craft' and payload.get('result') in {'success', 'fail'}:
                if self.plan:
                    if (when is None or (self._last_craft_event_time is not None and
                                         when < self._last_craft_event_time)):
                        self.plan.context_intact = False
                    if when is not None:
                        self._last_craft_event_time = max(when, self._last_craft_event_time or when)
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
                    if any(key in payload for key in ('damage_taken', 'miss', 'evaded')):
                        if not self.group or self.group.loot_events or self.crafting:
                            self._incoming_before_outgoing = True
                    continue
                self._incoming_before_outgoing = False
                if self.plan:
                    self.plan.context_intact = False
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
                if (self.crafting and self.plan and when is not None and
                        self._last_craft_event_time is not None and when < self._last_craft_event_time):
                    # Restarting a group must not reset chronology evidence.
                    self.plan.context_intact = False
                resume_signal = self._springs_resume_signal(str(payload.get('item_name') or ''), when)
                if self.group and self.group.loot_events and when is not None and self.group.last_loot_time is not None:
                    pending_receipt = (resume_signal and not self.group.shot_count and
                                       self.group.first_loot_time is not None and
                                       0 <= when - self.group.first_loot_time <= CRAFT_RECEIPT_WINDOW_SECONDS)
                    window = CRAFT_RECEIPT_WINDOW_SECONDS if self.crafting or pending_receipt else self.cluster_window
                    if when - self.group.last_loot_time > window or when < self.group.last_loot_time:
                        self._finish()
                if resume_signal:
                    self._resume_springs_receipt(event_id, when)
                if self.group is None:
                    if self.crafting:
                        self._begin_craft(event_id)
                    else:
                        self.group = _Group('hunt', 0.0, ('hunt', 0), event_id,
                                            context_key=self._last_shot_context)
                self._touch_group(event_id, when)
                if self.group.kind == 'craft' and self.group.plan:
                    if (self.group.recipe_context_epoch != self._attribution_epoch or
                            not self.group.plan.context_intact):
                        self.group.recipe_context_intact = False
                    if when is not None:
                        self._last_craft_event_time = max(when, self._last_craft_event_time or when)
                    product = self.group.plan.blueprint.strip().casefold().removesuffix(' blueprint').strip()
                    if product and str(payload.get('item_name') or '').strip().casefold() == product:
                        self.group.recipe_output_seen = True
                if not self.group.loot_events:
                    self.group.first_loot_time = when
                self.group.loot += value
                self.group.loot_events += 1
                self.group.last_loot_time = when

    def snapshot(self) -> ActivityPoints:
        version = (self._history, self._rows_seen)
        if self._snapshot is not None and self._snapshot.source_version == version:
            # Nothing appended since the last snapshot: same points, fresh containers.
            result = ActivityPoints()
            result.extend(self._snapshot)
            result.__dict__.update(self._snapshot.__dict__)
            result.manufacturing = replace(result.manufacturing) if result.manufacturing else None
            result.healing_contexts = dict(result.healing_contexts)
            result.healing_pending_reasons = dict(result.healing_pending_reasons)
            return result
        result = self._snapshot = self._build_snapshot()
        result.source_version = version
        return self.snapshot()

    def _build_snapshot(self) -> ActivityPoints:
        result = ActivityPoints()
        result.extend(self.points)
        result.has_crafting = self.points.has_crafting
        result.unknown_craft_groups = self.points.unknown_craft_groups
        result.unpriced_craft_plans = self.points.unpriced_craft_plans
        result.manufacturing = replace(self.plan) if self.plan else None
        result.last_event_id = self.last_event_id
        result.attribution_boundary_id = self._attribution_boundary_id
        result.accounting_revision = self.accounting_revision
        if self.group:
            result.append(self._point(self.group))
            if self.group.kind == 'craft' and self.group.cost is None:
                result.unknown_craft_groups += 1
            if result.manufacturing and self.group.plan is self.plan:
                result.manufacturing.returns += self.group.loot
        result.healing_expenses = tuple(self._healing_expenses)
        result.healing_contexts = dict(self._healing_contexts)
        result.healing_invalidated = frozenset(self._healing_invalidated)
        result.unpriced_heals = self._unpriced_heals
        compose_healing_costs(result)
        result.attribution_pending = result.healing_pending
        return result


def build_activity_points(rows: Iterable[Mapping], cluster_window: float = 0.75) -> ActivityPoints:
    """Project accounted history from saved cost/context snapshots; never reprice."""
    builder = ActivityTrendBuilder(cluster_window)
    builder.extend(rows)
    return builder.snapshot()


def compose_healing_costs(points: ActivityPoints) -> None:
    """Compose supported healing after weapon allocation, without another charge.

    Context is fixed at the activation's event, never at its later tick. Raw
    snapshots and prior fan observations are not mutated or retrospectively
    assigned a recipient.
    """
    for index, raw in enumerate(points):
        point = dict(raw)
        if point.get('kind') == 'hunt':
            point['weapon_cost'] = raw.get('weapon_cost', raw.get('input_cost', raw.get('cost')))
        point['healing_cost'] = 0.0
        points[index] = point
    by_id = {point['event_id']: point for point in points}
    ambiguous_recipients = set()
    previous_paid = None
    for point in points:
        if point.get('kind') != 'hunt':
            previous_paid = None
        elif point.get('shot_count'):
            previous_paid = point['event_id']
        elif point.get('loot_events') and previous_paid is not None:
            # A second return without new outgoing inputs can be overlapping
            # mobs or a delayed receipt. Neither identifies a unique recipient.
            ambiguous_recipients.add(previous_paid)
    allocated, pending = {}, {}
    for _event_id, anchor, cost, eligible, limit in points.healing_expenses:
        recipient, direction = points.healing_contexts.get(anchor, (None, 'No saved activation context'))
        point = by_id.get(recipient)
        reason = limit if not eligible else ''
        if not reason and anchor in points.healing_invalidated:
            reason = 'External/overlapping effect evidence makes the activation recipient uncertain'
        if not reason and point is None:
            reason = direction
        if not reason and (point.get('kind') != 'hunt' or not point.get('shot_count')):
            reason = 'No observed outgoing hunting inputs support this recipient'
        if not reason and not point.get('loot_events'):
            reason = 'Awaiting upcoming loot; no recipient is assigned yet'
        if not reason and (point.get('first_event_time') is None or point.get('first_loot_time') is None):
            reason = 'Encounter timing is incomplete or out of order'
        if not reason and (point.get('context_mixed') or 'shared' in str(point.get('cost_status', ''))
                           or 'ambiguous' in str(point.get('cost_status', '')) or point.get('weapon_cost') is None
                           or point['event_id'] in ambiguous_recipients):
            reason = 'Shared/overlapping receipt interval has no unique healing recipient'
        if reason:
            pending.setdefault(reason, []).append(cost)
        else:
            allocated.setdefault(recipient, []).append(cost)
    totals, counts, weapon_totals, previous = {}, {}, {}, {}
    for point in points:
        if point.get('kind') != 'hunt':
            continue  # Healing never changes manufacturing group semantics.
        scope = point.get('cost_scope', (point.get('kind', 'hunt'), 0))
        cost = point.get('weapon_cost')
        healing = math.fsum(allocated.get(point['event_id'], ()))
        point['healing_cost'] = healing
        point['cost'] = None if cost is None else math.fsum((cost, healing))
        point['profit'] = None if point['cost'] is None else point['loot'] - point['cost']
        if cost is not None:
            counts[scope] = counts.get(scope, 0) + 1
            weapon_totals[scope] = math.fsum((weapon_totals.get(scope, 0.0), cost))
            totals[scope] = math.fsum((totals.get(scope, 0.0), point['cost']))
            point['avg_cost'] = totals[scope] / counts[scope]
            point['weapon_avg_cost'] = weapon_totals[scope] / counts[scope] if point.get('kind') == 'hunt' else None
        else:
            point['avg_cost'], point['weapon_avg_cost'] = None, None
        first = scope not in previous
        profit, prior = point['profit'], previous.get(scope)
        delta = None if profit is None or (not first and prior is None) else 0.0 if first else profit - prior
        point['delta_profit'] = delta
        point['first_point'] = first
        point['trend_symbol'] = '•' if first or delta is None else '=' if math.isclose(delta, 0.0, abs_tol=1e-9) else '↑' if delta > 0 else '↓'
        previous[scope] = profit
    points.healing_booked = math.fsum(expense[2] for expense in points.healing_expenses)
    points.healing_allocated = math.fsum(math.fsum(values) for values in allocated.values())
    points.healing_pending_reasons = {reason: math.fsum(values) for reason, values in pending.items()}
    points.healing_pending = math.fsum(points.healing_pending_reasons.values())
