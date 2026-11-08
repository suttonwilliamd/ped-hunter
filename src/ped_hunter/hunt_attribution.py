"""Conservative, persisted estimates over a bounded tail of receipt intervals.

These are receipt-interval estimates, not target identities. Raw events and booked
input remain authoritative; money not supported by observed receipts stays pending.
"""
from __future__ import annotations

from collections import ChainMap
from copy import deepcopy
import math
from statistics import median

from .activity_trend import ActivityPoints

MAX_GROUPS = 12
MAX_SECONDS = 300.0
MAX_SHOTS = 1024
MAX_EVENT_SPAN = 4096
REFERENCE_LOOKBACK = 32


def _number(value, *, nullable=False):
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Attribution amounts must be finite and nonnegative")
    return float(value)


def _identifier(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, int) or not (0 if zero else 1) <= value <= 2**63 - 1:
        raise ValueError("Invalid attribution event identifier")
    return value


def _validate_member(member):
    if not isinstance(member, dict):
        raise ValueError("Invalid attribution receipt")
    ids = {_identifier(member['event_id']), _identifier(member['last_event_id'])}
    _number(member['raw_cost'])
    if not isinstance(member['shot_count'], int) or isinstance(member['shot_count'], bool) or member['shot_count'] < 0:
        raise ValueError("Invalid attribution shot count")
    for key in ('first_event_time', 'last_event_time', 'first_loot_time'):
        value = member[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("Invalid attribution timestamp")
    if member['context_key'] is not None and not isinstance(member['context_key'], str):
        raise ValueError("Invalid attribution context")
    if not isinstance(member['context_mixed'], bool) or not isinstance(member['closed'], bool):
        raise ValueError("Invalid attribution receipt flags")
    return ids


def _validate_state(state):
    """Validate the bounded engine record and return every referenced event ID."""
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError("Unsupported attribution state")
    ids = set()
    for key in ('activation_event_id', 'watermark'):
        value = _identifier(state[key], zero=True)
        if value:
            ids.add(value)
    if state['watermark'] < state['activation_event_id']:
        raise ValueError("Attribution watermark precedes activation")
    count = state['processed_count']
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("Invalid attribution processed count")
    _number(state['frozen_pending'])
    tail = state['tail']
    if not isinstance(tail, list) or len(tail) > REFERENCE_LOOKBACK:
        raise ValueError("Unbounded attribution tail")
    for member in tail:
        ids.update(_validate_member(member))
    if [member['event_id'] for member in tail] != sorted({member['event_id'] for member in tail}):
        raise ValueError("Invalid attribution tail ordering")
    active = state['active']
    if active is not None:
        if not isinstance(active, dict) or not isinstance(active.get('members'), list) or not 1 <= len(active['members']) <= MAX_GROUPS:
            raise ValueError("Unbounded attribution pool")
        ids.add(_identifier(active['id']))
        _number(active['reference'], nullable=True)
        if active['reference'] == 0:
            raise ValueError("Invalid zero attribution reference")
        _number(active['pending'])
        member_ids = set()
        for member in active['members']:
            ids.update(_validate_member(member))
            member_ids.add(member['event_id'])
        if len(member_ids) != len(active['members']) or active['id'] != active['members'][0]['event_id']:
            raise ValueError("Invalid attribution pool membership")
        if _outside(active['members']) or not _timed(active['members']) or not all(
            _same_context(active['members'][0], member) for member in active['members']
        ):
            raise ValueError("Attribution active pool exceeds its evidence window")
    return ids


def _validate_decision(decision):
    if not isinstance(decision, dict):
        raise ValueError("Invalid attribution decision")
    ids = {_identifier(decision['cost_pool_id'])}
    raw = _number(decision['raw_cost'])
    cost = _number(decision['cost'], nullable=True)
    total = _number(decision['cost_pool_total'])
    pending = _number(decision['cost_unassigned'])
    if pending > total + 1e-9 or (cost is not None and cost > total + 1e-9):
        raise ValueError("Attribution exceeds its input pool")
    if raw > total + 1e-9:
        raise ValueError("Attribution pool omits original input")
    if decision['cost_status'] not in {'shared_estimate', 'shared_ambiguous', 'frozen_shared_estimate', 'frozen_shared_ambiguous'}:
        raise ValueError("Invalid attribution status")
    if ('ambiguous' in decision['cost_status']) != (cost is None):
        raise ValueError("Ambiguous attribution must not invent a cost")
    if not isinstance(decision['cost_reason'], str):
        raise ValueError("Invalid attribution explanation")
    _identifier(decision['attribution_revision'])
    return ids


def _member(point):
    return {key: point.get(key) for key in (
        'event_id', 'last_event_id', 'shot_count', 'first_event_time',
        'last_event_time', 'first_loot_time', 'context_key', 'context_mixed', 'closed'
    )} | {'raw_cost': float(point.get('cost') or 0.0)}


def _same_context(left, right):
    return bool(left['context_key']) and left['context_key'] == right['context_key'] and not left['context_mixed'] and not right['context_mixed']


def _timed(members):
    previous = None
    for member in members:
        first, last, receipt = (member[key] for key in ('first_event_time', 'last_event_time', 'first_loot_time'))
        if first is None or last is None or receipt is None or first > receipt or receipt > last or (previous is not None and first < previous):
            return False
        previous = last
    return True


def _reference(tail, candidate, decisions):
    values = [member['raw_cost'] for member in tail[-REFERENCE_LOOKBACK:]
              if member['event_id'] < candidate['event_id'] and member['closed']
              and member['raw_cost'] > 0 and member['event_id'] not in decisions
              and _same_context(member, candidate) and _timed([member])]
    if len(values) < 5:
        return None
    typical = median(values)
    ordered = sorted(values)
    mad = median(abs(value - typical) for value in values)
    spread = ordered[math.ceil(0.8 * len(ordered)) - 1] - ordered[math.floor(0.2 * len(ordered))]
    inliers = [value for value in values if 0.6 * typical <= value <= 1.4 * typical]
    if len(inliers) < 5 or mad > 0.2 * typical or spread > 0.4 * typical:
        return None
    return median(inliers)


def _outside(members, latest=None):
    first = members[0]
    last = latest or members[-1]
    if len(members) > MAX_GROUPS or sum(member['shot_count'] for member in members) > MAX_SHOTS:
        return True
    if last['last_event_id'] - first['event_id'] > MAX_EVENT_SPAN:
        return True
    start, end = first['first_loot_time'], last['last_event_time']
    return start is None or end is None or end < start or end - start > MAX_SECONDS


class HuntAttribution:
    """Project saved estimates without touching the raw activity or active session."""

    def __init__(self, store):
        self.store = store
        self._session_id = None
        self._state = None
        self._decisions = {}
        self._endpoints = {}

    @staticmethod
    def _put(decisions, changes, event_id, values):
        old = decisions.get(event_id)
        comparable = {key: value for key, value in (old or {}).items() if key != 'attribution_revision'}
        if comparable == values:
            return
        decision = values | {'attribution_revision': (old or {}).get('attribution_revision', 0) + 1}
        decisions[event_id] = decision
        changes[event_id] = decision

    def _allocate(self, pool, decisions, changes):
        members = pool['members']
        total = math.fsum(member['raw_cost'] for member in members)
        typical = pool['reference']
        supported = typical is not None and _timed(members) and all(_same_context(members[0], member) for member in members)
        average = total / len(members)
        if not supported or average < 0.85 * typical:
            costs = [None] * len(members)
            pending = total
            reason = 'Observed receipts do not support a stable same-context input allocation.'
        elif average <= typical or math.isclose(average, typical, rel_tol=1e-12, abs_tol=1e-12):
            costs = [average] * (len(members) - 1)
            costs.append(total - math.fsum(costs))
            pending = 0.0
            reason = 'Observed pool average agrees with the robust same-context receipt-interval reference.'
        else:
            costs = [typical] * len(members)
            pending = total - math.fsum(costs)
            reason = 'Provisional same-context receipt-interval estimate; excess input remains unassigned, not future loot.'
        pool['pending'] = pending
        for member, cost in zip(members, costs):
            self._put(decisions, changes, member['event_id'], {
                'raw_cost': member['raw_cost'], 'cost': cost,
                'cost_status': 'shared_ambiguous' if cost is None else 'shared_estimate',
                'cost_pool_id': pool['id'], 'cost_pool_total': total,
                'cost_unassigned': pending, 'cost_reason': reason,
            })

    def _freeze(self, state, decisions, changes):
        pool = state['active']
        if pool is None:
            return
        for member in pool['members']:
            old = decisions[member['event_id']]
            values = {key: value for key, value in old.items() if key != 'attribution_revision'}
            values['cost_status'] = 'frozen_' + old['cost_status']
            values['cost_reason'] += ' Frozen at the bounded window or context boundary.'
            self._put(decisions, changes, member['event_id'], values)
        state['frozen_pending'] = math.fsum((state['frozen_pending'], pool['pending']))
        state['active'] = None

    def _consume(self, state, decisions, changes, point):
        if point.get('kind') != 'hunt':
            self._freeze(state, decisions, changes)
            return
        member = _member(point)
        pool = state['active']
        receipt = point.get('loot_events', 0) > 0
        if pool:
            members = pool['members']
            existing = next((index for index, old in enumerate(members) if old['event_id'] == member['event_id']), None)
            if existing is not None:
                members[existing] = member
                self._allocate(pool, decisions, changes)
                if _outside(members):
                    self._freeze(state, decisions, changes)
            else:
                incompatible = not _same_context(members[0], member)
                over = _outside(members, member) or sum(item['shot_count'] for item in members) + member['shot_count'] > MAX_SHOTS
                ordinary = receipt and pool['pending'] == 0 and member['raw_cost'] > 0 and (pool['reference'] is None or member['raw_cost'] > 0.35 * pool['reference'])
                if incompatible or over or ordinary or (receipt and len(members) >= MAX_GROUPS):
                    self._freeze(state, decisions, changes)
                elif receipt:
                    members.append(member)
                    self._allocate(pool, decisions, changes)
                    if not _timed(members):
                        self._freeze(state, decisions, changes)
        if not receipt:
            return
        tail = state['tail']
        previous_tail = [old for old in tail if old['event_id'] < member['event_id']]
        eligible = member['event_id'] > state['activation_event_id']
        if eligible and member['event_id'] not in decisions and state['active'] is None:
            previous = previous_tail[-1] if previous_tail else None
            reference = _reference(previous_tail, previous, decisions) if previous else None
            supported_trigger = (previous is not None and previous['event_id'] > state['activation_event_id']
                                 and previous['event_id'] not in decisions and _same_context(previous, member)
                                 and reference is not None and previous['raw_cost'] >= 2.5 * reference
                                 and member['raw_cost'] <= 0.35 * reference)
            ambiguous_members = previous_tail[-2:] + [member]
            unsupported_trigger = (len(ambiguous_members) == 3
                                   and _reference(previous_tail, ambiguous_members[0], decisions) is None
                                   and ambiguous_members[0]['event_id'] > state['activation_event_id']
                                   and ambiguous_members[0]['event_id'] not in decisions
                                   and ambiguous_members[0]['raw_cost'] > 0
                                   and all(item['raw_cost'] == 0 for item in ambiguous_members[1:])
                                   and all(_same_context(ambiguous_members[0], item) for item in ambiguous_members))
            selected = [previous, member] if supported_trigger else ambiguous_members if unsupported_trigger else None
            if selected and not _outside(selected):
                pool = {'id': selected[0]['event_id'], 'members': selected,
                        'reference': reference if supported_trigger else None, 'pending': 0.0}
                state['active'] = pool
                self._allocate(pool, decisions, changes)
                if not _timed(selected):
                    self._freeze(state, decisions, changes)
            elif member['raw_cost'] == 0:
                self._put(decisions, changes, member['event_id'], {
                    'raw_cost': 0.0, 'cost': None, 'cost_status': 'shared_ambiguous',
                    'cost_pool_id': member['event_id'], 'cost_pool_total': 0.0, 'cost_unassigned': 0.0,
                    'cost_reason': 'No attributable input is available for this observed receipt; profit is unresolved.',
                })
        tail[:] = [old for old in tail if old['event_id'] != member['event_id']]
        tail.append(member)
        del tail[:-REFERENCE_LOOKBACK]

    def update(self, session_id, raw_points, live: bool):
        if session_id != self._session_id:
            saved, decisions = self.store.load_hunt_attribution(session_id)
            self._session_id, self._state, self._decisions = session_id, saved, decisions
            self._endpoints = {}
        state = deepcopy(self._state)
        decisions = ChainMap({}, self._decisions)
        changes = {}
        watermark = int(getattr(raw_points, 'last_event_id', 0))
        if live:
            if state is None:
                state = {'version': 1, 'activation_event_id': watermark, 'watermark': watermark,
                         'processed_count': len(raw_points), 'tail': [], 'active': None, 'frozen_pending': 0.0}
                state['tail'] = [_member(point) for point in raw_points if point.get('kind') == 'hunt' and point.get('loot_events', 0)][-REFERENCE_LOOKBACK:]
            else:
                boundary = int(getattr(raw_points, 'attribution_boundary_id', 0))
                for point in raw_points[max(0, state['processed_count'] - 1):]:
                    pool = state['active']
                    if pool and pool['members'][-1]['last_event_id'] < boundary < point['event_id']:
                        self._freeze(state, decisions, changes)
                    self._consume(state, decisions, changes, point)
                pool = state['active']
                if pool and boundary > pool['members'][-1]['last_event_id']:
                    self._freeze(state, decisions, changes)
                state['processed_count'] = len(raw_points)
            if state != self._state or changes:
                state['watermark'] = max(state['watermark'], watermark)
                # Do not publish any state, revisions or endpoints until the atomic save succeeds.
                self.store.save_hunt_attribution(session_id, state, changes)
                self._state = state
                self._decisions.update(changes)
        result = ActivityPoints()
        result.__dict__.update(getattr(raw_points, '__dict__', {}))
        changed_ids = set(changes)
        previous_profit = None
        had_previous = False
        previous_revised = False
        visible_ids = {point['event_id'] for point in raw_points[-16:]}
        endpoints = {}
        for raw in raw_points:
            if raw.get('kind') != 'hunt':
                result.append(raw)
                continue
            decision = decisions.get(raw['event_id'])
            point = raw
            if decision:
                point = raw | decision
                point['profit'] = None if decision['cost'] is None else raw['loot'] - decision['cost']
            profit = point.get('profit')
            if decision or previous_revised:
                delta = None if profit is None or (had_previous and previous_profit is None) else profit - previous_profit if had_previous else 0.0
                symbol = '•' if delta is None or not had_previous else '=' if math.isclose(delta, 0.0, abs_tol=1e-9) else '↑' if delta > 0 else '↓'
                if point is raw:
                    point = dict(raw)
                point['delta_profit'], point['trend_symbol'] = delta, symbol
            result.append(point)
            endpoint = (point.get('cost'), point.get('profit'), point.get('delta_profit'),
                        point.get('trend_symbol'), point.get('cost_status'), point.get('attribution_revision'))
            if (decision or previous_revised) and raw['event_id'] in self._endpoints and self._endpoints[raw['event_id']] != endpoint:
                changed_ids.add(raw['event_id'])
            if raw['event_id'] in visible_ids:
                endpoints[raw['event_id']] = endpoint
            previous_profit, had_previous, previous_revised = profit, True, decision is not None
        self._endpoints = endpoints
        active_pending = state['active']['pending'] if state and state['active'] else 0.0
        result.attribution_active_pending = active_pending
        result.attribution_pending = math.fsum((state['frozen_pending'], active_pending)) if state else 0.0
        result.attribution_changed_ids = changed_ids if live else set()
        result.attribution_note = ('Receipt-interval estimates; unassigned input is not a prediction of future loot.'
                                   if decisions or result.attribution_pending else '')
        return result
