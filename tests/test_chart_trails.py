"""Persist observations of a group, never reconstruct unobserved development."""
import json
import math

import pytest

from ped_hunter.activity_trend import build_activity_points
from ped_hunter.chart_trails import TRAIL_MAX_SAMPLES, TrailRecorder
from ped_hunter.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'trails.sqlite3')


def add_event(store, sid, kind='loot', second=0, **payload):
    store.add_event(sid, {'kind': kind, 'timestamp': f'2026-01-01T10:00:{second:02d}',
                          'raw_message': 'isolated trail regression', 'payload': payload})
    with store.connect() as conn:
        return conn.execute('SELECT MAX(id) FROM events WHERE session_id = ?', (sid,)).fetchone()[0]


def point(event_id, cost=1.0, avg_cost=1.0, loot=0.0):
    return {'event_id': event_id, 'cost': cost, 'avg_cost': avg_cost, 'loot': loot,
            'profit': None if cost is None else loot - cost, 'kind': 'hunt', 'plan_event_id': None}


def projected(store, sid):
    with store.connect() as conn:
        rows = conn.execute('SELECT * FROM events WHERE session_id = ? ORDER BY id', (sid,)).fetchall()
    return build_activity_points(rows)


def sample(p):
    return (p['cost'], p['avg_cost'], p['loot'])


def test_live_observations_follow_stable_first_event_through_cost_and_receipts(store):
    sid = store.start_session('hunt')
    recorder = TrailRecorder(store)
    first_id = add_event(store, sid, 'combat', shot_cost=0.25)
    first_points = projected(store, sid)
    assert first_points[-1]['event_id'] == first_id
    first = recorder.update(sid, first_points, live=True)
    add_event(store, sid, 'combat', shot_cost=0.50)
    cost_points = projected(store, sid)
    recorder.update(sid, cost_points, live=True)
    add_event(store, sid, value=0.4, item_name='Animal Muscle Oil')
    partial_points = projected(store, sid)
    recorder.update(sid, partial_points, live=True)
    add_event(store, sid, value=0.6, item_name='Shrapnel')
    final_points = projected(store, sid)
    history = recorder.update(sid, final_points, live=True)[first_id]
    assert history == tuple(sample(ps[-1]) for ps in (first_points, cost_points, partial_points, final_points))
    assert first[first_id] == (sample(first_points[-1]),)
    assert len({ps[-1]['event_id'] for ps in (first_points, cost_points, partial_points, final_points)}) == 1
    assert len(final_points) == 1
    assert store.get_session(sid).loot_value == pytest.approx(1.0)
    assert store.get_session(sid).hunting_cost == pytest.approx(0.75)



def test_same_timestamp_craft_notices_and_new_plans_have_distinct_group_identities(store):
    sid = store.start_session('hunt')
    plan_a = add_event(store, sid, 'manufacturing_offset', attempts_total=2,
                       cost_per_attempt=0.7, total_cost=1.4, blueprint='First')
    receipt = add_event(store, sid, value=0.2, item_name='Widget')
    recorder = TrailRecorder(store)
    recorder.update(sid, projected(store, sid), live=True)
    add_event(store, sid, value=0.3, item_name='Residue')
    explicit = add_event(store, sid, 'craft', result='fail')
    plan_b = add_event(store, sid, 'manufacturing_offset', attempts_total=1,
                       cost_per_attempt=1.2, total_cost=1.2, blueprint='Second')
    next_receipt = add_event(store, sid, value=2, item_name='Other Widget')
    points = projected(store, sid)
    assert [p['event_id'] for p in points] == [receipt, explicit, next_receipt]
    assert [p['plan_event_id'] for p in points] == [plan_a, plan_a, plan_b]
    histories = recorder.update(sid, points, live=True)
    assert histories[receipt] == ((0.7, 0.7, 0.2), (0.7, 0.7, 0.5))
    assert explicit not in histories
    assert histories[next_receipt] == ((1.2, 1.2, 2),)

def test_between_polls_finalize_only_observed_active_group_and_seed_latest(store):
    sid = store.start_session('hunt')
    ids = [add_event(store, sid, second=i * 4, value=1) for i in range(5)]
    recorder = TrailRecorder(store)
    initial = point(ids[0], loot=0.2)
    recorder.update(sid, [initial], live=True)
    final = point(ids[0], cost=2, avg_cost=2, loot=3)
    unseen = [point(event_id, loot=5 + index) for index, event_id in enumerate(ids[1:])]
    histories = recorder.update(sid, [final, *unseen], live=True)
    assert histories[ids[0]] == (sample(initial), sample(final))
    assert histories[ids[-1]] == (sample(unseen[-1]),)
    assert not set(ids[1:-1]) & histories.keys()
    assert not set(ids[1:-1]) & store.load_chart_trails(sid, ids).keys()


def test_no_replay_for_legacy_or_paused_points_and_no_finalization_across_gap(store):
    sid = store.start_session('hunt')
    ids = [add_event(store, sid, second=i * 4, value=i) for i in range(4)]
    points = [point(event_id, loot=index) for index, event_id in enumerate(ids)]
    recorder = TrailRecorder(store)
    assert recorder.update(sid, points[:2], live=False) == {}
    observed = recorder.update(sid, points[:2], live=True)
    assert set(observed) == {ids[1]}
    paused_points = [points[0], point(ids[1], loot=99), points[2]]
    assert recorder.update(sid, paused_points, live=False) == observed
    resumed = recorder.update(sid, paused_points + [points[3]], live=True)
    assert resumed[ids[1]] == observed[ids[1]]  # 99 was not observed during tracking.
    assert ids[0] not in resumed and ids[2] not in resumed
    assert resumed[ids[3]] == (sample(points[3]),)


def test_repeated_updates_do_not_mutate_returned_histories_or_input(store):
    sid = store.start_session('hunt')
    event_id = add_event(store, sid, value=1)
    recorder = TrailRecorder(store)
    p = point(event_id, loot=1)
    before = recorder.update(sid, [p], live=True)
    for _ in range(8):
        assert recorder.update(sid, [dict(p)], live=True) == before
    p.update(loot=2, profit=1)
    after = recorder.update(sid, [p], live=True)
    assert before[event_id] == ((1.0, 1.0, 1),)
    assert after[event_id] == ((1.0, 1.0, 1), (1.0, 1.0, 2))
    assert isinstance(after[event_id], tuple)
    assert all(isinstance(s, tuple) for s in after[event_id])
    after.clear()  # A consumer's map is not the recorder's mutable cache.
    assert recorder.update(sid, [p], live=True)[event_id][-1] == (1.0, 1.0, 2)
    assert p == point(event_id, loot=2)


def test_session_switch_and_new_recorder_reload_only_matching_event_histories(store):
    sid_a, sid_b = store.start_session('hunt'), store.start_session('hunt')
    a = add_event(store, sid_a, value=1)
    b = add_event(store, sid_b, value=2)
    recorder = TrailRecorder(store)
    recorder.update(sid_a, [point(a, loot=0.5)], live=True)
    saved = recorder.update(sid_a, [point(a, loot=1.5)], live=True)
    assert recorder.update(None, [], live=False) == {}
    assert recorder.update(sid_b, [point(b, loot=2)], live=False) == {}
    assert set(recorder.update(sid_b, [point(b, loot=2)], live=True)) == {b}
    reopened = Store(store.db_path)
    assert TrailRecorder(reopened).update(sid_a, [point(a, loot=7)], live=False) == saved
    assert set(reopened.load_chart_trails(sid_a, [a, b])) == {a}
    assert set(reopened.load_chart_trails(sid_b, [a, b])) == {b}


def varied_samples():
    samples = [(2 + i / 100, 3 + i / 100, 4 + i / 100) for i in range(100)]
    for index, value in ((11, (0, 3, 4)), (22, (90, 3, 4)),
                         (33, (2, 0, 4)), (44, (2, 80, 4)),
                         (55, (2, 3, 0)), (66, (2, 3, 100))):
        samples[index] = value
    return samples


def assert_bounded_extrema(history, observations):
    assert 2 <= len(history) <= TRAIL_MAX_SAMPLES == 24
    assert history[0] == observations[0]
    assert history[-1] == observations[-1]
    assert all(s in observations for s in history)
    assert [observations.index(s) for s in history] == sorted(observations.index(s) for s in history)
    for column in range(3):
        assert min(s[column] for s in history) == min(s[column] for s in observations)
        assert max(s[column] for s in history) == max(s[column] for s in observations)
    assert min(s[2] - s[0] for s in history) == min(s[2] - s[0] for s in observations)
    assert max(s[2] - s[0] for s in history) == max(s[2] - s[0] for s in observations)


def test_recorder_and_storage_bound_samples_without_losing_extrema(store):
    sid = store.start_session('hunt')
    a, b = add_event(store, sid, value=1), add_event(store, sid, value=2)
    observations = varied_samples()
    recorder = TrailRecorder(store)
    for cost, average, loot in observations:
        history = recorder.update(sid, [point(a, cost, average, loot)], live=True)[a]
    assert_bounded_extrema(history, observations)
    store.save_chart_trails(sid, {b: observations})
    reopened = Store(store.db_path)
    persisted = reopened.load_chart_trails(sid, [a, b])
    assert persisted[a] == history
    assert_bounded_extrema(persisted[b], observations)


def test_only_last_sixteen_groups_are_returned_but_older_traces_remain_persisted(store):
    sid = store.start_session('hunt')
    recorder = TrailRecorder(store)
    points = []
    for index in range(22):
        event_id = add_event(store, sid, value=index)
        points.append(point(event_id, loot=index))
        recorder.update(sid, points, live=True)
        points[-1] = point(event_id, loot=index + 0.5)
        histories = recorder.update(sid, points, live=True)
    visible_ids = {p['event_id'] for p in points[-16:]}
    assert set(histories) == visible_ids
    assert len(store.load_chart_trails(sid, [p['event_id'] for p in points])) == 22
    assert set(TrailRecorder(Store(store.db_path)).update(sid, points, live=False)) == visible_ids


def test_storage_roundtrip_preserves_unknown_cost_and_never_changes_accounting(store):
    sid = store.start_session('hunt')
    a, b = add_event(store, sid, value=1), add_event(store, sid, value=2)
    before = store.get_session(sid)
    histories = {a: ((None, None, 1.0), (0.0, 0.0, 2.0)), b: ((3.0, 2.0, 1.0),)}
    store.save_chart_trails(sid, histories)
    assert Store(store.db_path).load_chart_trails(sid, [a]) == {a: histories[a]}
    assert store.load_chart_trails(sid, []) == {}
    assert store.get_session(sid) == before
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM events WHERE session_id = ?', (sid,)).fetchone()[0] == 2


@pytest.mark.parametrize('invalid', [(-1, 1, 1), (1, -1, 1), (1, 1, -1),
                                     (math.nan, 1, 1), (1, math.inf, 1), (1, 1, -math.inf),
                                     (1, 1, None)])
def test_storage_rejects_nonfinite_negative_and_unknown_loot(store, invalid):
    sid = store.start_session('hunt')
    event_id = add_event(store, sid, value=1)
    with pytest.raises(ValueError):
        store.save_chart_trails(sid, {event_id: [invalid]})
    assert store.load_chart_trails(sid, [event_id]) == {}


def test_store_ignores_cross_session_or_deleted_event_and_corrupt_trail_rows(store):
    sid, other = store.start_session('hunt'), store.start_session('hunt')
    a, b = add_event(store, sid, value=1), add_event(store, other, value=1)
    store.save_chart_trails(sid, {a: [(1, 1, 1)], b: [(1, 1, 1)], 999999: [(1, 1, 1)]})
    assert set(store.load_chart_trails(sid, [a, b, 999999])) == {a}
    with store.connect() as conn:
        conn.execute('UPDATE chart_trails SET samples = ? WHERE session_id = ? AND event_id = ?',
                     (json.dumps([[1, 1, -1]]), sid, a))
        conn.commit()
    assert store.load_chart_trails(sid, [a]) == {}
    store.save_chart_trails(sid, {a: [(1, 1, 1)]})
    with store.connect() as conn:
        conn.execute('DELETE FROM events WHERE session_id = ?', (sid,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (sid,))
        conn.commit()
    assert Store(store.db_path).load_chart_trails(sid, [a]) == {}


def test_additive_schema_upgrade_preserves_existing_sessions_and_events(store):
    sid = store.start_session('hunt')
    event_id = add_event(store, sid, value=1.25)
    before = store.get_session(sid)
    with store.connect() as conn:
        rows_before = [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
        conn.execute('DROP TABLE chart_trails')  # A database created before development fans existed.
        conn.commit()
    reopened = Store(store.db_path)
    assert reopened.get_session(sid) == before
    assert reopened.load_chart_trails(sid, [event_id]) == {}
    with reopened.connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')] == rows_before
