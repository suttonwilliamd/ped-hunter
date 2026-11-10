"""Derived team no-loot intervals, never kill identities or new booked charges.

The supported completion *association* is deliberately narrow: an empty-speaker
Force Sacred Idol opening followed by personal System receipts in the existing
receipt-sized window. Neither a team notice nor an opening proves a dungeon or
boss. Everything is reproducible from original rows; unrelated receipts retain
the ordinary projection. No team/Force receipt is booked as personal loot here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from datetime import datetime
from .parser import is_conversion_output_item

NO_LOOT = "This creature didn't carry any loot"
CONTAINER_SUFFIX = " opened a Sacred Idol"
MAX_MARKERS = 256
MAX_SECONDS = 7200
MAX_EVENT_SPAN = 65536
OPENING_WINDOW = 60
RECEIPT_WINDOW = 0.75


def is_team_no_loot_notice(payload):
    """Recognize the evidenced server notice, not a unique kill/server event ID."""
    return (not payload.get('speaker') and payload.get('channel') == 'Team'
            and payload.get('message') == NO_LOOT)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _when(value):
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else None
    except (ValueError, OverflowError, OSError):
        return None


@dataclass
class _Input:
    first_id: int | None = None
    last_id: int | None = None
    first_time: float | None = None
    last_timestamp: str | None = None
    cost: float = 0.0
    booked: float = 0.0
    uses: int = 0
    priced: bool = True

    def add(self, event_id, when, payload, timestamp):
        if self.first_id is None:
            self.first_id, self.first_time = event_id, when
        self.last_id = event_id
        self.last_timestamp = timestamp
        self.uses += 1
        price = _number(payload.get('shot_cost'))
        if price is not None:
            self.cost += price
            self.booked += price
        else:
            ammo, decay = _number(payload.get('ammo_cost')), _number(payload.get('repair_decay'))
            self.cost += (ammo or 0.0) + (decay or 0.0)
            self.booked += ammo or 0.0
            self.priced &= ammo is not None and decay is not None

    def segment(self, event_id, *, marker=True, timestamp=None):
        return {'event_id': event_id, 'development_event_id': self.first_id,
                'timestamp': timestamp if timestamp is not None else self.last_timestamp, 'first_event_id': self.first_id,
                'last_input_event_id': self.last_id, 'uses': self.uses,
                'recorded_input': self.cost if self.priced else None,
                'booked_input': self.booked,
                'cost': self.cost if self.priced and self.uses else None,
                'marker': marker}


@dataclass
class _Encounter:
    first_marker_id: int
    base_event_id: int
    start_time: float | None
    members: list[dict] = field(default_factory=list)
    last_marker_time: float | None = None
    last_event_id: int = 0
    status: str = 'pending'
    opener_id: int | None = None
    opening_time: float | None = None
    receipt_ids: list[int] = field(default_factory=list)
    receipts: list[float] = field(default_factory=list)
    receipt_price_unknown: bool = False

    def snapshot(self, developing=None):
        members = [dict(member) for member in self.members]
        if developing is not None:
            members.append(developing)
        return {'id': self.first_marker_id, 'base_event_id': self.base_event_id,
                'start_time': self.start_time, 'members': members,
                'last_event_id': self.last_event_id, 'status': self.status,
                'opener_id': self.opener_id, 'opening_time': self.opening_time,
                'receipt_ids': tuple(self.receipt_ids), 'receipts': tuple(self.receipts),
                'receipt_price_unknown': self.receipt_price_unknown}


class TeamEncounterBuilder:
    """One ordered raw-row observer; a bounded candidate, not a dungeon detector."""
    def __init__(self):
        self.input = _Input()
        self.active = None
        self.finished = []
        self.last_id = 0

    def _close(self, status):
        encounter = self.active
        if encounter is None:
            return
        if self.input.uses:
            encounter.members.append(self.input.segment(self.input.first_id, marker=False))
        encounter.status = status
        self.finished.append(encounter.snapshot())
        self.active = None
        self.input = _Input()

    def extend_row(self, row, payload=None):
        event_id = int(row['id'])
        if event_id <= self.last_id:
            return
        self.last_id = event_id
        if payload is None:
            try:
                payload = json.loads(row['payload'] or '{}')
            except (TypeError, ValueError):
                return
            if not isinstance(payload, dict):
                return
        kind, when = row['kind'], _when(row['timestamp'])
        outgoing = kind == 'combat' and ('damage' in payload or bool(payload.get('dodged') or payload.get('outgoing_miss')) or payload.get('shot_cost') is not None or payload.get('ammo_cost') is not None)
        personal = (kind == 'loot' and payload.get('is_personal') is True
                    and not is_conversion_output_item(str(payload.get('item_name') or '')))
        encounter = self.active
        if encounter and (event_id - min(encounter.base_event_id, encounter.first_marker_id) > MAX_EVENT_SPAN
                          or when is not None and encounter.start_time is not None
                          and (when < encounter.start_time or when - encounter.start_time > MAX_SECONDS)):
            self._close('interrupted_limit')
            encounter = None
        if encounter and encounter.opener_id is not None:
            linked = (personal and when is not None and encounter.opening_time is not None
                      and 0 <= when - encounter.opening_time <= RECEIPT_WINDOW)
            if linked:
                if encounter.base_event_id == encounter.first_marker_id:
                    encounter.base_event_id = event_id
                # Recorded receipt IDs already passed normal ingestion. Every
                # booked row is conserved, even if old text happens to repeat.
                value = _number(payload.get('value'))
                encounter.receipt_price_unknown |= value is None
                encounter.receipt_ids.append(event_id)
                if value is not None:
                    encounter.receipts.append(value)
                encounter.last_event_id = event_id
                return
            if outgoing or (when is not None and encounter.opening_time is not None
                            and when - encounter.opening_time > RECEIPT_WINDOW):
                self._close('container_linked' if encounter.receipt_ids else 'container_pending')
                encounter = None
        if kind in {'craft', 'manufacturing_offset'}:
            self._close('interrupted_activity')
            self.input = _Input()
            return
        if kind == 'repair':
            # A repair charge can refer to uses before this candidate. Keep
            # its booked money outside enemy estimates rather than loaning it.
            self._close('container_linked' if encounter and encounter.receipt_ids else 'interrupted_repair')
            self.input = _Input()
            return
        if outgoing:
            if encounter and encounter.base_event_id == encounter.first_marker_id:
                encounter.base_event_id = event_id
            self.input.add(event_id, when, payload, row['timestamp'])
            return
        if kind == 'loot':
            if (_number(payload.get('value')) in (None, 0)
                    or is_conversion_output_item(str(payload.get('item_name') or ''))):
                return
            # No container association: never grab an ordinary or delayed receipt.
            self._close('unlinked_receipt')
            self.input = _Input()
            return
        if kind != 'chat' or payload.get('speaker'):
            return
        channel, message = payload.get('channel'), payload.get('message')
        if channel == 'Team' and message == 'Your team was disbanded':
            self._close('interrupted_team')
            self.input = _Input()
            return
        if is_team_no_loot_notice(payload):
            if self.active and self.active.opener_id is not None:
                self._close('container_linked' if self.active.receipt_ids else 'container_pending')
            if self.active and len(self.active.members) >= MAX_MARKERS:
                self._close('interrupted_limit')
            if self.active is None:
                self.active = _Encounter(event_id, self.input.first_id or event_id,
                                         self.input.first_time if self.input.first_time is not None else when)
            self.active.members.append(self.input.segment(event_id, timestamp=row['timestamp']))
            self.active.last_marker_time = when
            self.active.last_event_id = event_id
            self.input = _Input()
        elif channel == 'Force' and isinstance(message, str) and message.endswith(CONTAINER_SUFFIX) and message[:-len(CONTAINER_SUFFIX)].strip():
            encounter = self.active
            if (encounter and when is not None and encounter.last_marker_time is not None
                    and 0 <= when - encounter.last_marker_time <= OPENING_WINDOW):
                if self.input.uses:
                    encounter.members.append(self.input.segment(self.input.first_id, marker=False))
                self.input = _Input()
                encounter.opener_id, encounter.opening_time = event_id, when
                encounter.last_event_id = event_id
                encounter.status = 'container_pending'

    def snapshot(self):
        result = list(self.finished)
        if self.active:
            developing = self.input.segment(self.input.first_id, marker=False) if self.input.uses else None
            result.append(self.active.snapshot(developing))
        return result


def project_team_encounters(points, *, session_closed: bool = False):
    """Replace ONLY whole conserved receipt/input intervals; leave raw models intact."""
    source_records = getattr(points, 'team_encounters', ())
    if not source_records:
        return points
    result = type(points)()
    result.__dict__.update(getattr(points, '__dict__', {}))
    # Reconcile genuine visible fans when a provisional team view reverts.
    result.encounter_changed_ids = {point['event_id'] for point in points[-16:]}
    result.encounter_unresolved_ids = set()
    result.encounter_note = ''
    records = {record['base_event_id']: record | {'number': number}
               for number, record in enumerate(source_records, 1)
               if record['status'] != 'unlinked_receipt'}
    if not records:
        result.extend(points)
        return result
    bases = list(points)
    for record in records.values():
        if record['base_event_id'] == record['id'] and not record['receipt_ids']:
            # Real notice IDs, not fabricated kills or zero-cost booked events.
            bases.append(dict(event_id=record['base_event_id'], kind='hunt',
                              kill=0, cost=0.0, loot=0.0, avg_cost=None,
                              profit=None, delta_profit=None, trend_symbol='•',
                              first_point=False, timestamp=record['members'][0]['timestamp'],
                              marker_only=True))
    if len(bases) != len(points):
        bases.sort(key=lambda point: point['event_id'])
    pool_members = {}
    for point in points:
        pool_id = point.get('cost_pool_id')
        if pool_id is not None:
            pool_members[pool_id] = pool_members.get(pool_id, 0) + 1
    for base in bases:
        encounter = records.get(base['event_id'])
        if encounter is None or base.get('kind') != 'hunt':
            result.append(base)
            continue
        members = encounter['members']
        if not members:
            result.append(base)
            continue
        inputs = [member['recorded_input'] for member in members]
        known = all(value is not None for value in inputs)
        total = math.fsum(member['booked_input'] for member in members)
        # A lone unpriced interval can show pending markers and its unallocated
        # receipt without inventing weights. Do not take another pool's budget.
        basis = base.get('cost')
        if any(member['cost'] is None for member in members) and pool_members.get(base.get('cost_pool_id'), 0) <= 1:
            basis = base.get('raw_cost', basis)
        if (basis is None or not math.isclose(total, basis, rel_tol=1e-12, abs_tol=1e-10)
                or known and not math.isclose(total, math.fsum(inputs), rel_tol=1e-12, abs_tol=1e-10)):
            result.append(base)
            result.encounter_unresolved_ids.add(base['event_id'])
            result.encounter_note = 'Team notices observed; input ownership/prices unresolved, ordinary allocation retained'
            continue
        returns = math.fsum(encounter['receipts'])
        associated = bool(encounter['opener_id'] and encounter['receipt_ids'])
        if (associated and not math.isclose(returns, base['loot'], abs_tol=1e-8)
                or not associated and base['loot'] > 0):
            result.append(base)
            result.encounter_unresolved_ids.add(base['event_id'])
            result.encounter_note = 'Personal receipts retained; container/complete-return linkage unresolved'
            continue
        linked = associated and not encounter['receipt_price_unknown'] and known and total > 0
        batch_closed = session_closed or encounter['status'] == 'container_linked'
        last_weighted = max((index for index, value in enumerate(inputs) if value), default=-1) if linked else -1
        allocated = 0.0
        previous_profit = None
        marker_count = sum(member['marker'] for member in members)
        for index, member in enumerate(members):
            weight = member['recorded_input']
            share = None
            if linked:
                remaining = max(0.0, returns - allocated)
                share = (remaining if index == last_weighted else min(remaining, returns * (weight / total))) if weight else 0.0
                allocated += share
            uncertain = member['cost'] is None
            loot = None if uncertain or share is None else max(0.0, share)
            cost = member['cost']
            profit = loot - cost if batch_closed and loot is not None and cost is not None else None
            point = dict(base)
            for key in ('raw_cost', 'cost_status', 'cost_pool_id', 'cost_pool_total',
                        'cost_unassigned', 'cost_basis', 'cost_support', 'attribution_revision'):
                point.pop(key, None)
            point.update(event_id=member['event_id'], development_event_id=member['development_event_id'],
                         kind='encounter', label=f"T{encounter['number']}.{index + 1}",
                         timestamp=member['timestamp'],
                         encounter_id=encounter['id'], encounter_index=index + 1,
                         encounter_markers=marker_count, encounter_status=encounter['status'],
                         encounter_opener_id=encounter['opener_id'],
                         encounter_receipt_ids=encounter['receipt_ids'],
                         encounter_batch_closed=batch_closed,
                         encounter_return_pending=not associated or encounter['receipt_price_unknown'] or not batch_closed,
                         recorded_own_input=weight, allocated_return=share,
                         booked_input=member['booked_input'],
                         source_group_event_id=None if base.get('marker_only') else base['event_id'],
                         cost_status='encounter_estimate',
                         cost=cost, loot=loot, profit=profit,
                         avg_cost=None,
                         delta_profit=profit - previous_profit if profit is not None and previous_profit is not None else None,
                         trend_symbol='•', loot_events=0, inferred=True,
                         closed=member['marker'] or bool(encounter['opener_id']),
                         shot_count=member['uses'], is_observed_no_loot_marker=member['marker'],
                         trail_recordable=not member['marker'] and not encounter['opener_id'],
                         observation_event_id=(encounter['receipt_ids'][-1] if encounter['receipt_ids']
                                               else member['event_id']),
                         cost_reason='Logged own inputs between team notices; target/kill identity unproven. '
                         + ('Personal container receipts shared in proportion to logged own input; not observed enemy loot.' if linked
                            else 'Return and profit pending; a team notice alone does not establish dungeon completion.'))
            result.append(point)
            result.encounter_changed_ids.add(point['event_id'])
            previous_profit = profit
        if associated and not linked and encounter['receipts']:
            # Recorded personal returns remain visible ONCE, but missing input
            # prices cannot be turned into invented enemy weights/profits.
            unallocated = dict(base, kind='encounter_receipt',
                               event_id=encounter['receipt_ids'][0],
                               label=f"T{encounter['number']}.R",
                               encounter_id=encounter['id'], encounter_markers=marker_count,
                               encounter_status='unallocated_receipts',
                               encounter_batch_closed=batch_closed,
                               encounter_return_pending=encounter['receipt_price_unknown'] or not batch_closed,
                               cost=None, avg_cost=None, profit=None, delta_profit=None,
                               loot=returns, trail_recordable=False,
                               observation_event_id=encounter['receipt_ids'][-1])
            for key in ('raw_cost', 'cost_status', 'cost_pool_id', 'cost_pool_total',
                        'cost_unassigned', 'cost_basis', 'cost_support', 'attribution_revision'):
                unallocated.pop(key, None)
            result.append(unallocated)
            result.encounter_changed_ids.add(unallocated['event_id'])
        result.encounter_note = ('Container-linked estimates' if linked else 'Team encounter pending') + ' · enemy/boss identity unconfirmed'
    booked, groups, team_seen = 0.0, 0, False
    for index, point in enumerate(result):
        if point.get('kind') == 'encounter':
            point['kill'] = index + 1
            amount = point['booked_input']
            team_seen = True
        elif point.get('kind') == 'hunt':
            amount = _number(point.get('raw_cost', point.get('cost')))
        else:
            continue
        if amount is None:
            continue
        booked += amount
        groups += 1
        if team_seen:
            # Keep the same hunting-input budget, including unassigned input,
            # but count derived markers instead of one giant receipt interval.
            average = booked / groups
            result[index] = dict(point, avg_cost=average, avg_group_count=groups)
            if point.get('avg_cost') != average:
                result.encounter_changed_ids.add(point['event_id'])
    return result


def visible_chart_points(points):
    """Keep the latest complete bounded encounter until ordinary activity replaces it."""
    start = max(0, len(points) - 16)
    for index in range(len(points) - 1, max(-1, len(points) - 17), -1):
        encounter = points[index].get('encounter_id')
        if encounter is not None:
            while index > 0 and points[index - 1].get('encounter_id') == encounter:
                index -= 1
            start = min(start, index)
            break
    return points[start:]
