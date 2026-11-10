"""Recent derived estimates can improve; raw input and target uncertainty cannot."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.activity_trend import ActivityPoints
from test_hunt_attribution import HuntStream, MemoryStore, assert_conserved, pooled


def test_stable_two_delayed_receipts_reconsider_both_endpoints_without_extra_input():
    stream = HuntStream()
    stream.baseline()
    first = stream.shots(20)
    stream.receipt()
    assert stream.points[-1]['cost'] == pytest.approx(2)
    second = stream.receipt()
    assert [p['cost'] for p in pooled(stream.points, [first, second])] == pytest.approx([1, 1])
    assert {first, second} <= stream.points.attribution_changed_ids
    assert stream.points.attribution_pending == pytest.approx(0)
    before = deepcopy(stream.points)
    rows = deepcopy(stream.rows)
    stream.engine = HuntAttribution(stream.store)
    stream.refresh()
    assert stream.points == before
    assert stream.rows == rows
    assert stream.points.attribution_changed_ids == set()


@pytest.mark.parametrize('receipts', [2, 3, 9, 12])
def test_closed_unsupported_whole_pool_learns_later_independent_reference(receipts):
    stream = HuntStream()
    first = stream.shots(receipts * 10)
    stream.receipt()
    ids = [first] + [stream.receipt() for _ in range(receipts - 1)]
    assert all(p['cost'] is None for p in pooled(stream.points, ids))
    original = deepcopy(stream.rows)
    # A paid ordinary receipt closes the ambiguous pool to new members. Its
    # independent raw interval, rather than an allocated estimate, is evidence.
    for _ in range(6):
        stream.shots(10)
        stream.receipt()
    members = pooled(stream.points, ids)
    assert [p['cost'] for p in members] == pytest.approx([1] * receipts)
    assert all(p['cost_pool_id'] == first for p in members)
    assert all(p['cost_pool_total'] == pytest.approx(receipts) for p in members)
    assert stream.points.attribution_pending == pytest.approx(0)
    assert stream.rows[:len(original)] == original
    # Twelve members plus later evidence exceeds a literal sixteen-entry cache:
    # the original full budget and anchor must nevertheless survive together.
    state, decisions = stream.store.load_hunt_attribution('hunt')
    assert {p['event_id'] for p in members} <= decisions.keys()
    snapshot = deepcopy(stream.points)
    writes = stream.store.writes
    stream.refresh()
    assert stream.points == snapshot
    assert stream.store.writes == writes


@pytest.mark.parametrize('receipts', [2, 3, 9])
@pytest.mark.parametrize('learn_later', [False, True])
def test_interleaved_uses_and_later_support_do_not_orphan_the_original_paid_anchor(receipts, learn_later):
    stream = HuntStream()
    if not learn_later:
        stream.baseline()
    first = stream.shots(receipts * 10 - 5)
    stream.receipt()
    middle = stream.shots(5)
    stream.receipt()
    ids = [first, middle] + [stream.receipt() for _ in range(receipts - 2)]
    if learn_later:
        for _ in range(6):
            stream.shots(10)
            stream.receipt()
    assert [p['cost'] for p in pooled(stream.points, ids)] == pytest.approx([1] * receipts)
    assert all(p['cost_pool_id'] == first for p in pooled(stream.points, ids))
    assert stream.points.attribution_pending == pytest.approx(0)


def test_variable_later_intervals_do_not_turn_an_unsupported_cluster_into_exact_kills():
    stream = HuntStream()
    first = stream.shots(30)
    stream.receipt()
    ids = [first, stream.receipt(), stream.receipt()]
    for count in (3, 20, 3, 20, 3, 20):
        stream.shots(count)
        stream.receipt()
    assert all(p['cost'] is None and p['profit'] is None for p in pooled(stream.points, ids))
    assert stream.points.attribution_pending == pytest.approx(3)


def test_batched_context_away_and_back_cannot_reopen_a_closed_pool():
    stream = HuntStream()
    first = stream.shots(30)
    stream.receipt()
    ids = [first, stream.receipt(), stream.receipt()]
    stream.shots(3)
    stream.receipt()
    # One UI poll can receive both an intervening different context and a
    # return to the former name/price. End-of-batch equality is not continuity.
    stream.event('combat', refresh=False, damage=4, shot_cost=.1, loadout='Other')
    stream.event('loot', refresh=False, advance=2, value=1.5)
    for _ in range(6):
        for _ in range(10):
            stream.event('combat', refresh=False, damage=4, shot_cost=.1,
                         ammo_cost=.09, repair_decay=.01, loadout=stream.loadout)
        stream.event('loot', refresh=False, advance=2, value=1.5)
    stream.refresh()
    assert all(p['cost'] is None and p['cost_status'] == 'frozen_shared_ambiguous'
               for p in pooled(stream.points, ids))


def test_preserved_variable_historical_pattern_keeps_k3154_unknown_without_altering_raw_values():
    fixture = json.loads((Path(__file__).parent / 'fixtures/hunt_attribution_historical_window.json').read_text(encoding='utf-8'))
    points = fixture['points']
    original = deepcopy(points)
    store = MemoryStore()
    engine = HuntAttribution(store)
    engine.update('historical-pattern', ActivityPoints(), live=True)
    for end in range(1, len(points) + 1):
        raw = ActivityPoints()
        raw.extend(deepcopy(points[:end]))
        raw.last_event_id = raw[-1]['last_event_id']
        result = engine.update('historical-pattern', raw, live=True)
        assert_conserved(raw, result)
    unresolved = next(point for point in result if point['label'] == 'K3154')
    assert unresolved['cost'] is None and unresolved['profit'] is None
    assert points == original
    saved = deepcopy(store.sessions)
    assert HuntAttribution(store).update('historical-pattern', raw, live=False) == result
    assert store.sessions == saved


@pytest.mark.parametrize('kind', ['repair', 'weapon'])
def test_explicit_boundary_inside_a_batched_receipt_chunk_hard_finalizes(kind):
    stream = HuntStream()
    first, second = stream.pending_pool()
    stream.event(kind, refresh=False, estimated_cost=0, weapon='Other')
    stream.event('loot', refresh=False, advance=.1, value=.25, item_name='Shrapnel')
    raw_before = deepcopy(stream.rows)
    stream.refresh()
    members = pooled(stream.points, [first, second])
    assert [p['cost'] for p in members] == pytest.approx([1, 1])
    assert all(p['cost_status'] == 'frozen_shared_estimate' for p in members)
    assert stream.points.attribution_active_pending == pytest.approx(0)
    assert stream.points.attribution_pending == pytest.approx(7)
    assert stream.rows == raw_before
