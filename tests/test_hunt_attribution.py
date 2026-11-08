"""Receipt-interval estimates conserve booked input without inventing targets."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder
from ped_hunter.hunt_attribution import HuntAttribution


class MemoryStore:
    """The public persistence contract, including delta-only decision writes."""

    def __init__(self):
        self.sessions = {}
        self.writes = 0
        self.fail_save = False

    def load_hunt_attribution(self, session_id):
        return deepcopy(self.sessions.get(session_id, (None, {})))

    def save_hunt_attribution(self, session_id, state, decisions):
        if self.fail_save:
            raise OSError('temporary attribution persistence failure')
        _, previous = self.sessions.get(session_id, (None, {}))
        merged = deepcopy(previous)
        merged.update(deepcopy(decisions))
        self.sessions[session_id] = (deepcopy(state), merged)
        self.writes += 1


def assert_conserved(raw, projected):
    hunting_input = sum(p['cost'] or 0 for p in raw if p['kind'] == 'hunt')
    known = sum(p['cost'] or 0 for p in projected if p['kind'] == 'hunt')
    assert known + projected.attribution_pending == pytest.approx(hunting_input, abs=1e-10)
    assert projected.attribution_pending >= 0
    assert 0 <= projected.attribution_active_pending <= projected.attribution_pending + 1e-10
    assert [p['event_id'] for p in projected] == [p['event_id'] for p in raw]
    assert len({p['event_id'] for p in projected}) == len(projected)
    for original, revised in zip(raw, projected):
        assert revised['loot'] == original['loot']
        assert revised['avg_cost'] == original['avg_cost']
        if original['kind'] == 'craft':
            assert revised == original
        if revised['cost'] is not None:
            assert revised['cost'] >= 0
            assert revised['profit'] == pytest.approx(revised['loot'] - revised['cost'])
        else:
            assert revised['profit'] is None


class HuntStream:
    def __init__(self, *, activate=True):
        self.store = MemoryStore()
        self.engine = HuntAttribution(self.store)
        self.builder = ActivityTrendBuilder()
        self.rows = []
        self.clock = 0.0
        self.loadout = 'Regression carbine'
        self.price = 0.1
        self.points = None
        if activate:
            self.refresh()

    def refresh(self, *, live=True):
        raw = self.builder.snapshot()
        before = deepcopy(raw)
        self.points = self.engine.update('hunt', raw, live=live)
        assert raw == before
        assert raw.__dict__ == before.__dict__
        assert_conserved(raw, self.points)
        assert self.points.last_event_id == raw.last_event_id
        assert self.points.has_crafting == raw.has_crafting
        assert self.points.unknown_craft_groups == raw.unknown_craft_groups
        assert self.points.manufacturing == raw.manufacturing
        return self.points

    def event(self, kind, *, advance=0.01, timestamp=None, **payload):
        self.clock += advance
        when = timestamp if timestamp is not None else (
            datetime(2026, 1, 1, 10) + timedelta(seconds=self.clock)
        ).isoformat(timespec='microseconds')
        row = {'id': len(self.rows) + 1, 'kind': kind, 'timestamp': when,
               'payload': json.dumps(payload)}
        self.rows.append(row)
        self.builder.extend([row])
        self.refresh()
        return row['id']

    def shots(self, count):
        first_id = len(self.rows) + 1
        for _ in range(count):
            self.event('combat', damage=4, shot_cost=self.price,
                       ammo_cost=self.price * 0.9, repair_decay=self.price * 0.1,
                       loadout=self.loadout)
        return first_id

    def receipt(self, value=1.5, **kwargs):
        return self.event('loot', advance=2, value=value,
                          item_name='Animal Muscle Oil', is_personal=True, **kwargs)

    def baseline(self, counts=(10,) * 6):
        for count in counts:
            self.shots(count)
            self.receipt()
        return self.points

    def pending_pool(self, shots=90):
        self.baseline()
        first_id = self.shots(shots)
        self.receipt()
        second_id = self.receipt()
        return first_id, second_id


def pooled(points, ids):
    return [p for p in points if p['event_id'] in ids]


def test_nine_kills_before_nine_receipts_revise_stable_ids_at_every_prefix():
    stream = HuntStream()
    stream.baseline()
    first_id = stream.shots(90)
    stream.receipt(value=0.4)
    first_snapshot = stream.points
    first_copy = deepcopy(first_snapshot)
    assert first_snapshot[-1]['cost'] == pytest.approx(9)
    ids = [first_id]
    for count in range(2, 10):
        ids.append(stream.receipt())
        current = pooled(stream.points, ids)
        assert len(current) == count
        assert [p['cost'] for p in current] == pytest.approx([1] * count)
        assert all(p['cost_status'] == 'shared_estimate' for p in current)
        assert stream.points.attribution_pending == pytest.approx(9 - count)
        assert stream.points.attribution_active_pending == pytest.approx(9 - count)
        assert all(p['cost_pool_id'] == first_id for p in current)
        assert all(p['cost_pool_total'] == pytest.approx(9) for p in current)
        assert all(p['raw_cost'] == pytest.approx(9 if p['event_id'] == first_id else 0)
                   for p in current)
        assert first_id in stream.points.attribution_changed_ids
    assert first_snapshot == first_copy
    assert first_snapshot.__dict__ == first_copy.__dict__
    assert [p['event_id'] for p in stream.points[-9:]] == ids
    assert stream.points[-1]['avg_cost'] == pytest.approx(1)
    frozen = deepcopy(stream.points[-9:])
    stream.shots(10)
    stream.receipt()
    assert stream.points[-1]['cost'] == pytest.approx(1)
    assert [p['cost'] for p in stream.points[-10:-1]] == [p['cost'] for p in frozen]
    assert stream.points.attribution_pending == pytest.approx(0)


def test_receipt_chunks_merge_without_a_phantom_kill_or_new_identity():
    stream = HuntStream()
    first_id, second_id = stream.pending_pool()
    before = deepcopy(stream.points)
    stream.event('loot', advance=0.1, value=0.25, item_name='Shrapnel')
    assert len(stream.points) == len(before)
    assert stream.points[-1]['event_id'] == second_id
    assert stream.points[-1]['loot'] == pytest.approx(before[-1]['loot'] + 0.25)
    assert stream.points[-2]['event_id'] == first_id
    assert stream.points.attribution_pending == pytest.approx(7)
    assert stream.points[-1]['cost'] == pytest.approx(1)


def test_intervening_shots_are_counted_once_and_ongoing_input_stays_separate():
    stream = HuntStream()
    first_id, second_id = stream.pending_pool()
    old = stream.points
    old_copy = deepcopy(old)
    ongoing_id = stream.shots(5)
    assert stream.points[-1]['event_id'] == ongoing_id
    assert stream.points[-1]['loot_events'] == 0
    assert stream.points[-1]['cost'] == pytest.approx(0.5)
    assert stream.points.attribution_pending == pytest.approx(7)
    stream.receipt()
    ids = [first_id, second_id, ongoing_id]
    assert [p['cost'] for p in pooled(stream.points, ids)] == pytest.approx([1, 1, 1])
    assert stream.points.attribution_pending == pytest.approx(6.5)
    assert all(p['cost_pool_total'] == pytest.approx(9.5) for p in pooled(stream.points, ids))
    for _ in range(3):
        current = deepcopy(stream.points)
        writes = stream.store.writes
        stream.refresh()
        assert stream.points == current
        assert stream.points.attribution_pending == pytest.approx(6.5)
        assert stream.store.writes == writes
    for _ in range(6):
        ids.append(stream.receipt())
    assert [p['cost'] for p in pooled(stream.points, ids)] == pytest.approx([1] * 9)
    assert stream.points.attribution_pending == pytest.approx(0.5)
    # The extra shots may belong to a not-yet-looted creature. Do not spread
    # them over nine receipts merely because the difference looks small.
    ids.append(stream.receipt())
    assert [p['cost'] for p in pooled(stream.points, ids)] == pytest.approx([9.5 / 10] * 10)
    assert stream.points.attribution_pending == pytest.approx(0)
    assert old == old_copy
    assert old.__dict__ == old_copy.__dict__


def test_normal_single_kills_and_high_solo_without_low_followup_remain_raw():
    stream = HuntStream()
    stream.baseline()
    stream.shots(40)
    stream.receipt()
    high_id = stream.points[-1]['event_id']
    assert stream.points[-1]['cost'] == pytest.approx(4)
    stream.shots(10)
    stream.receipt()
    assert next(p for p in stream.points if p['event_id'] == high_id)['cost'] == pytest.approx(4)
    assert stream.points[-1]['cost'] == pytest.approx(1)
    assert stream.points.attribution_pending == pytest.approx(0)
    assert not any(p.get('cost_status', '').startswith('shared_') for p in stream.points)


def test_low_receipt_alone_does_not_revise_previous_normal_kill_or_claim_free_profit():
    stream = HuntStream()
    stream.baseline()
    normal = deepcopy(stream.points[-1])
    stream.receipt()
    previous = next(p for p in stream.points if p['event_id'] == normal['event_id'])
    assert previous['cost'] == normal['cost']
    assert previous['profit'] == normal['profit']
    assert stream.points[-1]['cost'] is None
    assert stream.points[-1]['profit'] is None
    assert stream.points[-1]['delta_profit'] is None
    assert stream.points.attribution_pending == pytest.approx(0)


def test_incoming_damage_healing_miss_and_evade_do_not_become_shots_or_costs():
    stream = HuntStream()
    stream.baseline()
    before = deepcopy(stream.points)
    for payload in ({'damage_taken': 20}, {'healed': 8}, {'missed': True}, {'evaded': True}):
        stream.event('combat', **payload)
    assert stream.points == before
    stream.shots(10)
    stream.receipt()
    assert stream.points[-1]['cost'] == pytest.approx(1)
    assert stream.points[-1]['shot_count'] == 10


@pytest.mark.parametrize('counts', [(), (10, 10, 10, 10), (3, 20, 3, 20, 3, 20),
                                    (5, 8, 13, 21, 34, 55)])
def test_insufficient_or_variable_reference_preserves_ambiguity_not_equal_shares(counts):
    stream = HuntStream()
    stream.baseline(counts)
    first_id = stream.shots(90)
    stream.receipt()
    ids = [first_id, stream.receipt(), stream.receipt()]
    members = pooled(stream.points, ids)
    assert len(members) == 3
    assert all(p['cost'] is None and p['profit'] is None for p in members)
    assert all(p['cost_status'] == 'shared_ambiguous' for p in members)
    assert stream.points.attribution_pending == pytest.approx(9)
    assert stream.points[-1]['delta_profit'] is None


def test_legacy_activation_and_nonlive_reads_never_backfill_historical_points():
    stream = HuntStream(activate=False)
    # Build raw legacy data without calling a live estimator.
    rows = []
    for event_id, (kind, second, payload) in enumerate([
        ('combat', 0, {'damage': 4, 'shot_cost': 9, 'loadout': 'Legacy'}),
        ('loot', 1, {'value': 1}), ('loot', 3, {'value': 1}), ('loot', 5, {'value': 1}),
    ], 1):
        rows.append({'id': event_id, 'kind': kind, 'timestamp': f'2026-01-01T10:00:{second:02d}',
                     'payload': json.dumps(payload)})
    stream.rows = rows
    stream.clock = 5
    stream.builder.extend(rows)
    raw = deepcopy(stream.builder.snapshot())
    def historical_values(points):
        return [(p['event_id'], p['cost'], p['avg_cost'], p['profit'], p['loot'])
                for p in points[:len(raw)]]

    expected_history = historical_values(raw)
    stream.refresh(live=False)
    assert stream.points == raw
    assert stream.store.writes == 0
    stream.refresh(live=True)
    assert stream.points == raw
    stream.receipt()
    assert historical_values(stream.points) == expected_history
    assert not {p['event_id'] for p in raw} & stream.store.sessions['hunt'][1].keys()
    before = deepcopy(stream.store.sessions)
    stream.engine = HuntAttribution(stream.store)
    stream.refresh(live=False)
    assert stream.store.sessions == before
    assert historical_values(stream.points) == expected_history


@pytest.mark.parametrize('boundary', ['weapon', 'repair', 'craft', 'price', 'loadout'])
def test_context_boundaries_freeze_pending_and_do_not_borrow_new_input(boundary):
    stream = HuntStream()
    first_id, second_id = stream.pending_pool()
    old_ids = [first_id, second_id]
    if boundary == 'craft':
        stream.event('manufacturing_offset', attempts_total=1, cost_per_attempt=0.7,
                     total_cost=0.7, blueprint='Regression widget')
        stream.event('craft', result='fail')
        stream.receipt(value=0.2)
    elif boundary == 'price':
        stream.price = 0.2
    elif boundary == 'loadout':
        stream.loadout = 'Other carbine'
    else:
        stream.event(boundary, weapon='Other carbine')
    stream.shots(10)
    stream.receipt()
    frozen = deepcopy(pooled(stream.points, old_ids))
    assert [p['cost'] for p in frozen] == pytest.approx([1, 1])
    assert stream.points.attribution_pending == pytest.approx(7)
    assert stream.points.attribution_active_pending == pytest.approx(0)
    for _ in range(3):
        stream.receipt()
    assert pooled(stream.points, old_ids) == frozen
    assert stream.points.attribution_pending >= 7


@pytest.mark.parametrize('bound', ['time', 'receipts', 'shots', 'event_span'])
def test_bounded_windows_freeze_existing_decisions_and_retain_unassigned(bound):
    stream = HuntStream()
    first_id, second_id = stream.pending_pool(shots=200)
    old_ids = [first_id, second_id]
    if bound == 'time':
        stream.clock += 301
        stream.receipt()
    elif bound == 'receipts':
        for _ in range(11):
            stream.receipt()
    elif bound == 'shots':
        stream.shots(1025)
        stream.receipt()
    else:
        for _ in range(4097):
            stream.event('combat', advance=0.001, damage_taken=1)
        stream.receipt()
    frozen = deepcopy(pooled(stream.points, old_ids))
    assert all(p['cost'] is not None for p in frozen)
    assert stream.points.attribution_pending > 0
    assert stream.points.attribution_active_pending == pytest.approx(0)
    for _ in range(3):
        stream.receipt()
    assert pooled(stream.points, old_ids) == frozen


@pytest.mark.parametrize('timestamp', ['invalid-time', '2025-01-01T00:00:00'])
def test_invalid_or_backward_receipt_times_never_support_confident_split(timestamp):
    stream = HuntStream()
    stream.baseline()
    first_id = stream.shots(90)
    stream.receipt()
    stream.receipt(timestamp=timestamp)
    stream.receipt()
    members = [p for p in stream.points if p['event_id'] >= first_id]
    assert not any(p.get('cost_status') == 'shared_estimate' for p in members)


def test_failed_save_does_not_advance_in_memory_past_unpersisted_revision():
    stream = HuntStream()
    stream.baseline()
    stream.shots(90)
    stream.receipt()
    saved = deepcopy(stream.store.sessions)
    previous = stream.points
    stream.store.fail_save = True
    with pytest.raises(OSError):
        stream.receipt()
    assert stream.store.sessions == saved
    assert previous[-1]['cost'] == pytest.approx(9)
    stream.store.fail_save = False
    retried = stream.refresh()
    expected = HuntAttribution(stream.store).update('hunt', stream.builder.snapshot(), live=False)
    assert retried == expected
    assert retried.attribution_pending == pytest.approx(7)
    assert [p['cost'] for p in retried[-2:]] == pytest.approx([1, 1])


def test_more_observed_receipts_than_supported_input_reverts_to_ambiguity():
    stream = HuntStream()
    first_id, second_id = stream.pending_pool(shots=30)
    assert stream.points.attribution_pending == pytest.approx(1)
    third_id = stream.receipt()
    assert [p['cost'] for p in pooled(stream.points, [first_id, second_id, third_id])] == pytest.approx([1, 1, 1])
    fourth_id = stream.receipt()
    members = pooled(stream.points, [first_id, second_id, third_id, fourth_id])
    assert all(p['cost'] is None and p['profit'] is None for p in members)
    assert stream.points.attribution_pending == pytest.approx(3)
    assert stream.points[-1]['delta_profit'] is None


def test_old_stable_baseline_outside_reference_lookback_cannot_support_a_split():
    stream = HuntStream()
    stream.baseline()
    stream.baseline((3, 20) * 16)
    first_id = stream.shots(90)
    stream.receipt()
    ids = [first_id, stream.receipt(), stream.receipt()]
    assert all(p['cost'] is None for p in pooled(stream.points, ids))
    assert stream.points.attribution_pending == pytest.approx(9)


def test_mixed_shot_price_in_one_interval_does_not_claim_a_clean_reference():
    stream = HuntStream()
    stream.baseline()
    first_id = stream.shots(45)
    stream.price = 0.2
    stream.shots(23)
    stream.receipt()
    assert stream.points[-1]['context_mixed']
    stream.receipt()
    stream.receipt()
    members = [p for p in stream.points if p['event_id'] >= first_id]
    assert not any(p.get('cost_status') == 'shared_estimate' for p in members)


def test_changed_ids_and_revision_are_stable_on_idle_poll():
    stream = HuntStream()
    first_id, second_id = stream.pending_pool()
    previous = deepcopy(pooled(stream.points, [first_id, second_id]))
    third_id = stream.receipt()
    changed = stream.points.attribution_changed_ids
    assert {first_id, second_id} <= changed
    revised = pooled(stream.points, [first_id, second_id])
    assert all(new['attribution_revision'] > old['attribution_revision']
               for old, new in zip(previous, revised))
    stable = deepcopy(stream.points)
    stream.refresh()
    assert stream.points == stable
    assert stream.points.attribution_changed_ids == set()
    assert stream.points[-1]['event_id'] == third_id
