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
# Eligibility is independent of display caches. Retain a whole pool while its
# last receipt is recent, including older members (at most 16 + 12 - 1).
RECENT_LOOTS = 16


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
    if not isinstance(state, dict) or state.get('version') not in {1, 2}:
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
    closed = state.get('closed_pools', [])
    if not isinstance(closed, list) or len(closed) > RECENT_LOOTS:
        raise ValueError("Unbounded closed attribution pools")
    retained = closed + ([state['active']] if state['active'] is not None else [])
    retained_ids = set()
    for pool in retained:
        if not isinstance(pool, dict) or not isinstance(pool.get('members'), list) or not 1 <= len(pool['members']) <= MAX_GROUPS:
            raise ValueError("Unbounded attribution pool")
        ids.add(_identifier(pool['id']))
        _number(pool['reference'], nullable=True)
        if pool['reference'] == 0:
            raise ValueError("Invalid zero attribution reference")
        _number(pool['pending'])
        member_ids = set()
        for member in pool['members']:
            ids.update(_validate_member(member))
            member_ids.add(member['event_id'])
        if (len(member_ids) != len(pool['members']) or retained_ids & member_ids
                or pool['id'] != pool['members'][0]['event_id']
                or [member['event_id'] for member in pool['members']] != sorted(member_ids)):
            raise ValueError("Invalid attribution pool membership")
        retained_ids.update(member_ids)
        if _outside(pool['members']) or not _timed(pool['members']) or not all(
            _same_context(pool['members'][0], member) for member in pool['members']
        ):
            raise ValueError("Attribution retained pool exceeds its evidence window")
    if len(retained_ids) > RECENT_LOOTS + MAX_GROUPS - 1:
        raise ValueError("Unbounded retained attribution members")
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
    if decision['cost_status'] not in {
        'shared_estimate', 'shared_ambiguous', 'closed_shared_estimate',
        'closed_shared_ambiguous', 'frozen_shared_estimate', 'frozen_shared_ambiguous',
    }:
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


def _reference(tail, candidate, decisions, excluded=()):
    """Independent raw closed intervals only; never allocated estimates."""
    excluded = set(excluded) | {candidate['event_id']}
    # A name/price returning after another context does not reopen the earlier
    # hunt cohort, including when all changes arrived in a single reader batch.
    before = [member['event_id'] for member in tail
              if member['event_id'] < candidate['event_id'] and not _same_context(member, candidate)]
    after = [member['event_id'] for member in tail
             if member['event_id'] > candidate['event_id'] and not _same_context(member, candidate)]
    lower, upper = max(before, default=0), min(after, default=2**63)
    values = [member['raw_cost'] for member in tail[-REFERENCE_LOOKBACK:]
              if member['closed'] and member['event_id'] not in excluded
              and lower < member['event_id'] < upper
              and member['raw_cost'] > 0 and member['event_id'] not in decisions
              and _same_context(member, candidate) and _timed([member])
              and candidate['first_loot_time'] is not None
              and abs(member['first_loot_time'] - candidate['first_loot_time']) <= MAX_SECONDS
              and abs(member['event_id'] - candidate['event_id']) <= MAX_EVENT_SPAN]
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


def _supported_cluster(members, typical):
    # Keep the strong high/low trigger. A moderate high interval instead needs
    # a balanced whole budget within one actually observed use's price. Merely
    # lowering the high threshold misclassifies alternating mixed-cost hunts.
    if typical is None or len(members) < 2:
        return False
    first = members[0]
    total = math.fsum(member['raw_cost'] for member in members)
    use_price = first['raw_cost'] / first['shot_count'] if first['shot_count'] else 0.0
    balanced = math.isclose(total, len(members) * typical, rel_tol=1e-12, abs_tol=use_price + 1e-12)
    following_average = math.fsum(member['raw_cost'] for member in members[1:]) / (len(members) - 1)
    return (first['raw_cost'] > 1.4 * typical
            and (first['raw_cost'] >= 2.5 * typical or balanced)
            and following_average <= 0.6 * typical
            and total / len(members) >= 0.85 * typical)


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

    def _allocate(self, pool, decisions, changes, *, closed=False):
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
                'cost_status': ('closed_' if closed else '') + ('shared_ambiguous' if cost is None else 'shared_estimate'),
                'cost_pool_id': pool['id'], 'cost_pool_total': total,
                'cost_unassigned': pending, 'cost_reason': reason,
            })

    def _finalize(self, state, pool, decisions, changes):
        for member in pool['members']:
            old = decisions[member['event_id']]
            values = {key: value for key, value in old.items() if key != 'attribution_revision'}
            values['cost_status'] = 'frozen_shared_ambiguous' if old['cost'] is None else 'frozen_shared_estimate'
            values['cost_reason'] += ' Hard-finalized at the evidence horizon or context boundary.'
            self._put(decisions, changes, member['event_id'], values)
        state['frozen_pending'] = math.fsum((state['frozen_pending'], pool['pending']))

    def _close(self, state, decisions, changes, *, hard=False):
        pool = state['active']
        if pool is None:
            return
        state['active'] = None
        if hard:
            self._finalize(state, pool, decisions, changes)
        else:
            state['closed_pools'].append(pool)
            self._allocate(pool, decisions, changes, closed=True)

    def _consume(self, state, decisions, changes, point):
        if point.get('kind') != 'hunt':
            for pool in state['closed_pools']:
                self._finalize(state, pool, decisions, changes)
            state['closed_pools'] = []
            self._close(state, decisions, changes, hard=True)
            return
        member = _member(point)
        receipt = point.get('loot_events', 0) > 0
        retained = []
        for pool in state['closed_pools']:
            if (member['event_id'] > pool['members'][-1]['event_id']
                    and not _same_context(pool['members'][0], member)):
                self._finalize(state, pool, decisions, changes)
            else:
                retained.append(pool)
        state['closed_pools'] = retained
        tail = state['tail']
        previous_tail = [old for old in tail if old['event_id'] < member['event_id']]
        for closed in state['closed_pools']:
            for index, old in enumerate(closed['members']):
                if old['event_id'] == member['event_id']:
                    closed['members'][index] = member
        pool = state['active']
        if pool:
            members = pool['members']
            existing = next((index for index, old in enumerate(members) if old['event_id'] == member['event_id']), None)
            if existing is not None:
                members[existing] = member
                self._allocate(pool, decisions, changes)
                if _outside(members) or not _timed(members):
                    self._close(state, decisions, changes, hard=True)
            elif member['event_id'] > members[-1]['event_id']:
                incompatible = not _same_context(members[0], member)
                over = _outside(members, member) or sum(item['shot_count'] for item in members) + member['shot_count'] > MAX_SHOTS
                # Without a reference, paid ordinary intervals must remain
                # independent evidence, not be swallowed by an unknown pool.
                ordinary = receipt and member['raw_cost'] > 0 and (
                    pool['reference'] is None or (pool['pending'] == 0 and member['raw_cost'] > 0.35 * pool['reference']))
                if incompatible or over:
                    self._close(state, decisions, changes, hard=True)
                elif ordinary or (receipt and len(members) >= MAX_GROUPS):
                    self._close(state, decisions, changes)
                elif receipt and member['event_id'] not in decisions:
                    members.append(member)
                    self._allocate(pool, decisions, changes)
                    if not _timed(members):
                        self._close(state, decisions, changes, hard=True)
        if not receipt:
            return
        eligible = member['event_id'] > state['activation_event_id']
        if eligible and member['event_id'] not in decisions and state['active'] is None:
            previous = previous_tail[-1] if previous_tail else None
            selected = [previous, member] if previous else []
            reference = _reference(previous_tail, previous, decisions) if previous else None
            candidate = (previous is not None and previous['event_id'] > state['activation_event_id']
                         and previous['event_id'] not in decisions and _same_context(previous, member)
                         and _timed(selected) and not _outside(selected))
            triggered = candidate and (_supported_cluster(selected, reference) or (
                reference is None and previous['raw_cost'] > 0 and member['raw_cost'] == 0))
            if triggered:
                state['active'] = {'id': previous['event_id'], 'members': selected,
                                   'reference': reference, 'pending': 0.0}
                self._allocate(state['active'], decisions, changes)
            elif member['raw_cost'] == 0:
                if _timed([member]) and _same_context(member, member):
                    pool = {'id': member['event_id'], 'members': [member], 'reference': None, 'pending': 0.0}
                    state['closed_pools'].append(pool)
                    self._allocate(pool, decisions, changes, closed=True)
                else:
                    self._put(decisions, changes, member['event_id'], {
                        'raw_cost': 0.0, 'cost': None, 'cost_status': 'shared_ambiguous',
                        'cost_pool_id': member['event_id'], 'cost_pool_total': 0.0, 'cost_unassigned': 0.0,
                        'cost_reason': 'Receipt context or timing is unknown; no attributable input is identified.',
                    })
        tail[:] = [old for old in tail if old['event_id'] != member['event_id']]
        tail.append(member)
        del tail[:-REFERENCE_LOOKBACK]

    @staticmethod
    def _eligible(pool, latest, recent_ids, boundary, watermark, tail):
        members = pool['members']
        uses = sum(member['shot_count'] for member in tail if member['event_id'] >= members[0]['event_id'])
        context_continues = all(_same_context(members[0], member)
                               for member in tail if member['event_id'] >= members[0]['event_id'])
        if latest is not None and not any(member['event_id'] == latest['event_id'] for member in tail):
            uses += latest['shot_count']
        return (members[-1]['event_id'] in recent_ids and latest is not None
                and _same_context(members[0], latest)
                and not _outside(members, latest) and uses <= MAX_SHOTS and context_continues
                and watermark - members[0]['event_id'] <= MAX_EVENT_SPAN
                and boundary <= members[0]['event_id'])

    def _upgrade(self, state, raw_points, decisions, changes):
        """Reopen only complete recent v1 pools whose original context survives."""
        state['version'] = 2
        state['closed_pools'] = []
        hunts = [point for point in raw_points if point.get('kind') == 'hunt' and point.get('loot_events')]
        by_id = {point['event_id']: _member(point) for point in hunts}
        recent = {point['event_id'] for point in hunts[-RECENT_LOOTS:]}
        latest = _member(raw_points[-1]) if raw_points and raw_points[-1].get('kind') == 'hunt' else None
        groups = {}
        for event_id, decision in decisions.items():
            if decision['cost_status'].startswith('frozen_') or decision['cost_pool_total'] == 0:
                groups.setdefault(decision['cost_pool_id'], []).append(event_id)
        for pool_id, ids in groups.items():
            if not all(event_id in by_id for event_id in ids):
                continue
            members = [by_id[event_id] for event_id in sorted(ids)]
            pool = {'id': pool_id, 'members': members, 'reference': None,
                    'pending': decisions[ids[0]]['cost_unassigned']}
            if (not _timed(members) or _outside(members)
                    or not all(_same_context(members[0], member) for member in members)
                    or not self._eligible(pool, latest, recent, raw_points.attribution_boundary_id,
                                          raw_points.last_event_id, list(by_id.values())[-REFERENCE_LOOKBACK:])):
                continue
            if decisions[ids[0]]['cost_status'].startswith('frozen_'):
                state['frozen_pending'] = math.fsum((state['frozen_pending'], -pool['pending']))
                if -1e-9 < state['frozen_pending'] < 0:
                    state['frozen_pending'] = 0.0
            state['closed_pools'].append(pool)
            self._allocate(pool, decisions, changes, closed=True)

    def _reconsider(self, state, raw_points, decisions, changes):
        tail = state['tail']
        recent = {member['event_id'] for member in tail[-RECENT_LOOTS:]}
        latest = _member(raw_points[-1]) if raw_points and raw_points[-1].get('kind') == 'hunt' else None
        boundary, watermark = raw_points.attribution_boundary_id, raw_points.last_event_id
        retained = []
        for pool in state['closed_pools']:
            if self._eligible(pool, latest, recent, boundary, watermark, tail):
                retained.append(pool)
            else:
                self._finalize(state, pool, decisions, changes)
        state['closed_pools'] = retained
        if state['active'] and not self._eligible(state['active'], latest, recent, boundary, watermark, tail):
            self._close(state, decisions, changes, hard=True)

        # Late support may join a preceding raw paid anchor to an entire
        # unresolved pool (or a low standalone interval), never a truncated
        # subset of another pool. All candidate input is excluded as evidence.
        for start, left in enumerate(tail):
            retained = state['closed_pools'] + ([state['active']] if state['active'] else [])
            owners = {member['event_id']: pool for pool in retained for member in pool['members']}
            original = owners.get(left['event_id'])
            if left['event_id'] <= state['activation_event_id']:
                continue
            if original is not None:
                if original['id'] != left['event_id'] or original['pending'] == 0:
                    continue
            elif left['event_id'] in decisions or left['raw_cost'] == 0:
                continue
            members, selected_owners = [], []
            signal = None
            selected = None
            current_reference = None
            for following in tail[start:]:
                if members and following['event_id'] <= members[-1]['event_id']:
                    continue
                owner = owners.get(following['event_id'])
                if owner is not None and owner['id'] != following['event_id']:
                    break  # Never take only the visible suffix of a pool.
                if owner is None and following['event_id'] in decisions:
                    break  # Hard-finalized or contextless history is not input.
                unit = owner['members'] if owner is not None else [following]
                if (signal is not None and current_reference is not None
                        and math.fsum(member['raw_cost'] for member in members) <= len(members) * current_reference + 1e-12
                        and unit[0]['raw_cost'] > 0.35 * current_reference):
                    break  # A completed budget does not consume a new ordinary kill.
                combined = members + unit
                if (_outside(combined) or not _timed(combined) or latest is None
                        or not all(_same_context(left, member) for member in combined + [latest])):
                    break
                members = combined
                if owner is not None:
                    selected_owners.append(owner)
                if len(members) < 2:
                    continue
                reference = _reference(tail, left, decisions, [member['event_id'] for member in members])
                if reference is None:
                    continue
                if signal is None and _supported_cluster(members, reference):
                    signal = members
                if signal is None or not _supported_cluster(signal, reference):
                    continue
                current_reference = reference
                suffix_average = math.fsum(member['raw_cost'] for member in members[1:]) / (len(members) - 1)
                if suffix_average > 0.6 * reference:
                    continue
                if self._eligible({'members': members}, latest, recent, boundary, watermark, tail):
                    # Zero-input receipts can invalidate a formerly sufficient
                    # budget: retain the full cluster and let _allocate report
                    # ambiguity rather than leave an earlier short split stale.
                    selected = (members, list(selected_owners), reference)
            if selected is None:
                continue
            members, selected_owners, reference = selected
            if original is not None and members == original['members']:
                continue
            was_active = any(owner is state['active'] for owner in selected_owners)
            for owner in selected_owners:
                if owner is state['active']:
                    state['active'] = None
                else:
                    state['closed_pools'].remove(owner)
            pool = {'id': left['event_id'], 'members': members, 'reference': reference, 'pending': 0.0}
            if was_active or (members[-1]['event_id'] == tail[-1]['event_id']
                              and state['active'] is None and not members[-1]['closed']):
                state['active'] = pool
                self._allocate(pool, decisions, changes)
            else:
                state['closed_pools'].append(pool)
                self._allocate(pool, decisions, changes, closed=True)
        for pool in state['closed_pools'] + ([state['active']] if state['active'] else []):
            pool['reference'] = _reference(tail, pool['members'][0], decisions)
            self._allocate(pool, decisions, changes, closed=pool is not state['active'])

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
                state = {'version': 2, 'activation_event_id': watermark, 'watermark': watermark,
                         'processed_count': len(raw_points), 'tail': [], 'active': None,
                         'closed_pools': [], 'frozen_pending': 0.0}
                state['tail'] = [_member(point) for point in raw_points if point.get('kind') == 'hunt' and point.get('loot_events', 0)][-REFERENCE_LOOKBACK:]
            else:
                if state['version'] == 1:
                    self._upgrade(state, raw_points, decisions, changes)
                for point in raw_points[max(0, state['processed_count'] - 1):]:
                    self._consume(state, decisions, changes, point)
                self._reconsider(state, raw_points, decisions, changes)
                state['processed_count'] = len(raw_points)
            if state != self._state or changes:
                state['watermark'] = max(state['watermark'], watermark)
                # Do not publish any state, revisions or endpoints until the atomic save succeeds.
                observations = {
                    point['event_id']: (changes[point['event_id']]['cost'], point.get('avg_cost'), point.get('loot'))
                    for point in raw_points if point['event_id'] in changes
                }
                self.store.save_hunt_attribution(session_id, state, changes, observations=observations)
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
        closed_pending = math.fsum(pool['pending'] for pool in state.get('closed_pools', [])) if state else 0.0
        result.attribution_pending = math.fsum((state['frozen_pending'], active_pending, closed_pending)) if state else 0.0
        result.attribution_changed_ids = changed_ids if live else set()
        result.attribution_note = ('Receipt-interval estimates; unassigned input is not a prediction of future loot.'
                                   if decisions or result.attribution_pending else '')
        return result
