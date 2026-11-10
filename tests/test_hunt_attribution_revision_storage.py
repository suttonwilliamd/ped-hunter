"""Real SQLite revisions preserve raw money and existing observed fan provenance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder
from ped_hunter.chart_trails import TrailRecorder
from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.storage import Store
from test_hunt_attribution import HuntStream, pooled
from test_hunt_attribution_storage import DatabaseHunt


@pytest.mark.parametrize('receipts', [2, 3, 9, 12])
@pytest.mark.parametrize('intervened', [0, 5])
def test_later_reference_commits_all_retained_costs_and_only_existing_fans(tmp_path, receipts, intervened):
    hunt = DatabaseHunt(tmp_path / 'late.sqlite3')
    first = hunt.shots(receipts * 10 - intervened)
    recorder = TrailRecorder(hunt.store)
    recorder.update(hunt.sid, hunt.points, live=True)
    hunt.receipt()
    if intervened:
        middle = hunt.shots(intervened)
        hunt.receipt()
        ids = [first, middle] + [hunt.receipt() for _ in range(receipts - 2)]
    else:
        ids = [first] + [hunt.receipt() for _ in range(receipts - 1)]
    prefix = hunt.originals()[1]
    initial_fan = hunt.store.load_chart_trails(hunt.sid, ids)[first]
    if intervened:
        assert initial_fan[-1][0] == pytest.approx(receipts - .5)
    else:
        assert initial_fan[-1][0] is None
    revised = set()
    for _ in range(6):
        hunt.shots(10)
        revised.update(hunt.points.attribution_changed_ids)
        hunt.receipt()
        revised.update(hunt.points.attribution_changed_ids)
    fans = hunt.store.load_chart_trails(hunt.sid, ids)
    assert set(fans) == {first}  # No fabricated histories for unobserved events.
    assert fans[first][:len(initial_fan)] == initial_fan
    assert fans[first][-1] == pytest.approx((1, hunt.points[0]['avg_cost'], 1.5))
    assert set(ids) <= revised
    assert [p['cost'] for p in pooled(hunt.points, ids)] == pytest.approx([1] * receipts)
    # The recorder still holds its earlier cache: it must not erase the atomic
    # unknown -> supported observation when it sees a later UI refresh.
    recorder.update(hunt.sid, hunt.points, live=True, revised_event_ids=revised)
    assert hunt.store.load_chart_trails(hunt.sid, ids)[first] == fans[first]
    assert hunt.originals()[1][:len(prefix)] == prefix
    before = hunt.originals()
    saved = hunt.store.load_hunt_attribution(hunt.sid)
    reopened = Store(hunt.store.db_path)
    restored = HuntAttribution(reopened).update(hunt.sid, hunt.builder.snapshot(), live=False)
    assert restored == hunt.points
    assert reopened.load_hunt_attribution(hunt.sid) == saved
    assert reopened.load_chart_trails(hunt.sid, ids) == fans
    assert hunt.originals() == before


def test_real_fan_write_failure_rolls_back_entire_revision_then_projection_retry_is_idempotent(tmp_path):
    hunt = DatabaseHunt(tmp_path / 'atomic.sqlite3')
    for _ in range(6):
        hunt.shots(10)
        hunt.receipt()
    first = hunt.shots(20)
    hunt.receipt()
    TrailRecorder(hunt.store).update(hunt.sid, hunt.points, live=True)
    saved = hunt.store.load_hunt_attribution(hunt.sid)
    fans = hunt.store.load_chart_trails(hunt.sid, [first])
    second = hunt.event('loot', advance=2, refresh=False, value=1.5,
                        item_name='Animal Muscle Oil', is_personal=True)
    before = hunt.originals()
    with hunt.store.connect() as conn:
        conn.execute("CREATE TRIGGER reject_revision_fan BEFORE UPDATE ON chart_trails BEGIN SELECT RAISE(ABORT, 'fixture fan failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture fan failure'):
        hunt.refresh()
    assert hunt.store.load_hunt_attribution(hunt.sid) == saved
    assert hunt.engine._state == saved[0]
    assert hunt.store.load_chart_trails(hunt.sid, [first, second]) == fans
    assert hunt.originals() == before
    with hunt.store.connect() as conn:
        conn.execute('DROP TRIGGER reject_revision_fan')
    hunt.refresh()  # Retry derived projection only, never ingest a raw row twice.
    assert [p['cost'] for p in pooled(hunt.points, [first, second])] == pytest.approx([1, 1])
    after = hunt.store.load_hunt_attribution(hunt.sid)
    after_fans = hunt.store.load_chart_trails(hunt.sid, [first, second])
    assert set(after_fans) == {first}
    assert after_fans[first][-1][0] == pytest.approx(1)
    hunt.refresh()
    assert hunt.store.load_hunt_attribution(hunt.sid) == after
    assert hunt.store.load_chart_trails(hunt.sid, [first, second]) == after_fans
    assert hunt.originals() == before


@pytest.mark.parametrize('boundary', ['time', 'uses', 'events', 'context', 'receipts'])
def test_closed_unknown_pools_hard_finalize_at_whole_evidence_boundaries(boundary):
    stream = HuntStream()
    first = stream.shots(30)
    stream.receipt()
    ids = [first, stream.receipt(), stream.receipt()]
    stream.shots(3)
    stream.receipt()  # Close, do not absorb this independent paid interval.
    assert stream.points.attribution_pending == pytest.approx(3)
    if boundary == 'time':
        stream.clock += 301
        stream.shots(1)
    elif boundary == 'uses':
        stream.shots(1025)
    elif boundary == 'events':
        for _ in range(4097):
            stream.event('combat', damage_taken=1)
    elif boundary == 'context':
        stream.event('repair', estimated_cost=0)
    else:
        for count in (20, 3) * 8:
            stream.shots(count)
            stream.receipt()
    finalized = deepcopy(pooled(stream.points, ids))
    assert all(p['cost'] is None and p['cost_status'] == 'frozen_shared_ambiguous' for p in finalized)
    stream.engine = HuntAttribution(stream.store)
    for _ in range(6):
        stream.shots(10)
        stream.receipt()
    assert pooled(stream.points, ids) == finalized
    assert stream.points.attribution_pending >= 3 - 1e-10


@pytest.mark.parametrize('new_boundary', [False, True])
@pytest.mark.parametrize('fixture_name', ['delayed', 'standalone'])
@pytest.mark.parametrize('timestamp_utc', [False, True])
def test_actual_preserved_v1_closed_pool_upgrade_is_whole_atomic_and_context_bound(tmp_path, new_boundary, fixture_name, timestamp_utc):
    fixture = json.loads((Path(__file__).parent / f'fixtures/hunt_attribution_v1_{fixture_name}.json').read_text(encoding='utf-8'))
    store = Store(tmp_path / 'legacy.sqlite3')
    sid = store.start_session('hunt')
    for event in fixture['events']:
        timestamp = datetime.fromisoformat(event['timestamp'])
        if timestamp_utc:
            timestamp = timestamp.astimezone(timezone.utc)
        store.add_event(sid, dict(event, timestamp=timestamp.isoformat(), payload=json.loads(event['payload'])))
    old_decisions = {int(key): value for key, value in fixture['decisions'].items()}
    old_fans = {int(key): values for key, values in fixture['fans'].items()}
    store.save_hunt_attribution(sid, fixture['state'], old_decisions)
    store.save_chart_trails(sid, old_fans)
    if new_boundary:
        store.add_event(sid, {'timestamp': (timestamp + timedelta(seconds=10)).isoformat(), 'kind': 'weapon',
                             'raw_message': 'isolated context boundary', 'payload': {'weapon': 'Other'}})
    with store.connect() as conn:
        raw_before = [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
        builder = ActivityTrendBuilder()
        builder.extend(conn.execute('SELECT * FROM events ORDER BY id').fetchall())
    totals_before = store.get_session(sid)
    assert totals_before.hunting_cost == pytest.approx(fixture['expected_booked_PED'])
    engine = HuntAttribution(store)
    old = engine.update(sid, builder.snapshot(), live=False)
    assert all(p['cost'] is None for p in pooled(old, old_decisions))
    revised = engine.update(sid, builder.snapshot(), live=True)
    if new_boundary:
        assert store.load_hunt_attribution(sid)[1] == old_decisions
        assert store.load_chart_trails(sid, old_fans) == {
            event_id: tuple(tuple(sample) for sample in samples) for event_id, samples in old_fans.items()}
    else:
        assert [p['cost'] for p in pooled(revised, old_decisions)] == pytest.approx(
            [fixture['expected_reference_PED']] * len(old_decisions))
        assert set(old_decisions) <= revised.attribution_changed_ids
        fans = store.load_chart_trails(sid, old_fans)
        for event_id in old_decisions:
            assert fans[event_id][:-1] == tuple(tuple(sample) for sample in old_fans[event_id])
            assert fans[event_id][-1][0] == pytest.approx(fixture['expected_reference_PED'])
        assert revised.attribution_pending == pytest.approx(0)
    saved = store.load_hunt_attribution(sid)
    assert HuntAttribution(Store(store.db_path)).update(sid, builder.snapshot(), live=False) == revised
    engine.update(sid, builder.snapshot(), live=True)
    assert store.load_hunt_attribution(sid) == saved
    assert store.get_session(sid) == totals_before
    with store.connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')] == raw_before


@pytest.mark.parametrize('prior_reference', [False, True])
def test_many_interleaved_low_inputs_reconsider_complete_neighbor_pools_without_losing_budget(tmp_path, prior_reference):
    hunt = DatabaseHunt(tmp_path / 'many-interleaved.sqlite3')
    if prior_reference:
        for _ in range(6):
            hunt.shots(10)
            hunt.receipt()
    first = hunt.shots(70)
    TrailRecorder(hunt.store).update(hunt.sid, hunt.points, live=True)
    hunt.receipt()
    ids = [first]
    for uses in (4, 0, 5, 2, 0, 4, 0, 5):
        if uses:
            anchor = hunt.shots(uses)
            hunt.receipt()
        else:
            anchor = hunt.receipt()
        ids.append(anchor)
    original_rows = hunt.originals()[1]
    if not prior_reference:
        # Independent intervals must dominate the variable small inputs before
        # the existing robust dispersion test permits a reference.
        for _ in range(10):
            hunt.shots(10)
            hunt.receipt()
    members = pooled(hunt.points, ids)
    assert [p['cost'] for p in members] == pytest.approx([1] * 9)
    assert all(p['cost_pool_id'] == first and p['cost_pool_total'] == pytest.approx(9) for p in members)
    assert hunt.points.attribution_pending == pytest.approx(0)
    assert hunt.originals()[1][:len(original_rows)] == original_rows
    assert hunt.store.load_chart_trails(hunt.sid, ids)[first][-1][0] == pytest.approx(1)
    saved = hunt.store.load_hunt_attribution(hunt.sid)
    assert HuntAttribution(Store(hunt.store.db_path)).update(hunt.sid, hunt.builder.snapshot(), live=False) == hunt.points
    hunt.refresh()
    assert hunt.store.load_hunt_attribution(hunt.sid) == saved


@pytest.mark.parametrize('prior_reference', [False, True])
def test_typical_priced_intervening_kill_does_not_strand_an_earlier_paid_anchor(tmp_path, prior_reference):
    hunt = DatabaseHunt(tmp_path / 'typical-intervened.sqlite3')
    if prior_reference:
        for _ in range(6):
            hunt.shots(10)
            hunt.receipt()
    first = hunt.shots(20)
    TrailRecorder(hunt.store).update(hunt.sid, hunt.points, live=True)
    hunt.receipt()
    middle = hunt.shots(10)
    hunt.receipt()
    last = hunt.receipt()
    original_rows = hunt.originals()[1]
    if not prior_reference:
        for _ in range(6):
            hunt.shots(10)
            hunt.receipt()
    assert [p['cost'] for p in pooled(hunt.points, [first, middle, last])] == pytest.approx([1, 1, 1])
    assert hunt.points.attribution_pending == pytest.approx(0)
    assert hunt.originals()[1][:len(original_rows)] == original_rows


@pytest.mark.parametrize('prior_reference', [False, True])
def test_complete_neighbor_pools_share_supported_budget_but_extra_input_stays_unassigned(tmp_path, prior_reference):
    hunt = DatabaseHunt(tmp_path / 'neighbor-pools.sqlite3')
    if prior_reference:
        for _ in range(6):
            hunt.shots(10)
            hunt.receipt()
    first = hunt.shots(90)
    TrailRecorder(hunt.store).update(hunt.sid, hunt.points, live=True)
    hunt.receipt()
    ids = [first, hunt.receipt()]
    middle = hunt.shots(10)
    hunt.receipt()
    ids.append(middle)
    ids.extend(hunt.receipt() for _ in range(6))
    prefix = hunt.originals()[1]
    if not prior_reference:
        for _ in range(6):
            hunt.shots(10)
            hunt.receipt()
    members = pooled(hunt.points, ids)
    assert [p['cost'] for p in members] == pytest.approx([1] * 9)
    assert all(p['cost_pool_id'] == first for p in members)
    assert hunt.points.attribution_pending == pytest.approx(1)  # Not a forecast of another loot.
    assert hunt.originals()[1][:len(prefix)] == prefix
    saved = hunt.store.load_hunt_attribution(hunt.sid)
    hunt.refresh()
    assert hunt.store.load_hunt_attribution(hunt.sid) == saved
