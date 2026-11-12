"""Original receipts survive corrections; every derived accounting view agrees."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import queue
import sqlite3

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder
from ped_hunter.app import PedHunterApp, _kill_trend_points, _load_resume_view, _loot_event_points, _recent_events
from ped_hunter.catalog import Catalog
from ped_hunter.chart_trails import TrailRecorder
from ped_hunter.feedback import ArrivalTracker, Snapshot
from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.storage import LoadoutRecord, Store


FAP = 'Frontier First Aid Pack, Adjusted'


def book(store, sid, kind, second=0, **payload):
    stamp = (datetime(2026, 1, 1, 10) + timedelta(seconds=second)).isoformat(timespec='microseconds')
    store.add_event(sid, {'kind': kind, 'timestamp': stamp,
                         'raw_message': f'Synthetic {kind} {second} {payload}', 'payload': payload})
    with store.connect() as conn:
        return conn.execute('SELECT MAX(id) FROM events WHERE session_id=?', (sid,)).fetchone()[0]


def originals(store):
    with store.connect() as conn:
        return [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]


@pytest.fixture
def hunt(tmp_path):
    store = Store(tmp_path / 'source.sqlite3')
    gear = LoadoutRecord(None, 'Synthetic recorded setup', 'Frontier Rifle', ammo_burn=100,
                         decay=.01, cost_per_shot=2)
    gear.id = store.save_loadout(gear, make_active=True)
    sid = store.start_session('hunt', gear)
    store.set_selected_healing_tool(FAP)
    engine, recorder = HuntAttribution(store), TrailRecorder(store)
    engine.update(sid, _kill_trend_points(store, sid), live=True)
    group = book(store, sid, 'combat', 0, damage=6, shot_cost=2, repair_decay=.01, loadout=gear.name)
    ordinary = book(store, sid, 'loot', 1, value=.0005, item_name='Shrapnel')
    recorder.update(sid, engine.update(sid, _kill_trend_points(store, sid), live=True), live=True)
    key = book(store, sid, 'loot', 1.1, value=500, item_name='Strongbox Key')
    book(store, sid, 'combat', 2, healed=23, healing_action='self', healing_cost=.02,
         healing_cost_status='selected_tool_estimate')
    recorder.update(sid, engine.update(sid, _kill_trend_points(store, sid), live=True), live=True)
    return store, sid, group, ordinary, key


def test_copied_receipt_exclusion_rebuilds_totals_history_resume_fans_and_restores_originals(hunt, tmp_path):
    source, sid, group, ordinary, key = hunt
    source_rows = originals(source)
    source_summary = source.get_session(sid)
    source_trails = source.load_chart_trails(sid, [group])
    path = tmp_path / 'copied.sqlite3'
    with source.connect() as conn:
        destination = sqlite3.connect(path)
        try:
            conn.backup(destination)
        finally:
            destination.close()
    store = Store(path)
    builder, engine, recorder = ActivityTrendBuilder(), HuntAttribution(store), TrailRecorder(store)
    recorder.update(sid, engine.update(sid, _kill_trend_points(store, sid, builder=builder), False), False)
    assert store.set_loot_excluded(sid, key, True)
    view = _load_resume_view(store, sid)
    assert view.session.loot_value == pytest.approx(.0005)
    assert view.session.hunting_cost == pytest.approx(2.02)
    assert view.session.healing_cost == pytest.approx(.02)
    assert view.session.net_value == pytest.approx(.0005 - 2.02)
    assert view.totals.total_loot == pytest.approx(.0005)
    assert view.totals.total_cost == pytest.approx(2.02)
    assert store.list_recent_sessions()[0] == view.session
    assert store.list_all_sessions()[0] == view.session
    assert {row['id'] for row in _recent_events(store, sid, 100)} == {row[0] for row in source_rows} - {key}
    assert sum(point[1] for point in _loot_event_points(store, sid)) == pytest.approx(.0005)
    points = engine.update(sid, _kill_trend_points(store, sid, builder=builder), live=False)
    assert points[0]['event_id'] == group
    assert (points[0]['cost'], points[0]['loot']) == pytest.approx((2, .0005))
    assert recorder.update(sid, points, False)[group] == ((2, 2, .0005),)
    assert originals(store) == source_rows
    retained = store.loot_event_history(sid, event_id=key)[0]
    assert retained['excluded'] and json.loads(retained['payload'])['value'] == 500
    reopened = Store(path)
    assert reopened.get_session(sid) == view.session
    assert reopened.load_chart_trails(sid, [group])[group][-1][2] == pytest.approx(.0005)
    assert reopened.set_loot_excluded(sid, key, False)
    assert reopened.get_session(sid).loot_value == source_summary.loot_value
    assert reopened.get_session(sid).hunting_cost == source_summary.hunting_cost
    assert reopened.load_chart_trails(sid, [group]) == source_trails
    assert originals(reopened) == source_rows
    assert originals(source) == source_rows
    assert source.get_session(sid) == source_summary
    assert source.load_chart_trails(sid, [group]) == source_trails


def test_repeated_exclude_restore_is_idempotent_and_equal_count_edits_reset_caches(hunt):
    store, sid, group, ordinary, key = hunt
    second = book(store, sid, 'loot', 1.2, value=20, item_name='Synthetic second receipt')
    store.set_loot_excluded(sid, key, True)
    builder, recorder = ActivityTrendBuilder(), TrailRecorder(store)
    cached = _kill_trend_points(store, sid, builder=builder)
    recorder.update(sid, cached, False)
    previous_count = store.get_session(sid).events
    revision = store.get_accounting_revision(sid)
    trails = store.load_chart_trails(sid, [group])
    for _ in range(3):
        assert not store.set_loot_excluded(sid, key, True)
        assert store.get_accounting_revision(sid) == revision
        assert store.get_session(sid).loot_value == pytest.approx(20.0005)
        assert store.load_chart_trails(sid, [group]) == trails
    store.set_loot_excluded(sid, key, False)
    store.set_loot_excluded(sid, second, True)
    assert store.get_session(sid).events == previous_count
    refreshed = _kill_trend_points(store, sid, builder=builder)
    assert refreshed.accounting_revision == revision + 2
    assert refreshed[0]['loot'] == pytest.approx(500.0005)
    assert recorder.update(sid, refreshed, False)[group][-1][2] == pytest.approx(500.0005)
    assert store.set_loot_excluded(sid, second, False)
    final_revision = store.get_accounting_revision(sid)
    assert not store.set_loot_excluded(sid, second, False)
    assert store.get_accounting_revision(sid) == final_revision
    assert store.get_session(sid).loot_value == pytest.approx(520.0005)


def test_fan_history_versions_restore_after_restart_and_continued_ingestion(hunt):
    store, sid, group, ordinary, key = hunt
    baseline_history = store.load_chart_trails(sid, [group])[group]
    store.set_loot_excluded(sid, key, True)
    next_group = book(store, sid, 'combat', 6, damage=1, shot_cost=3, loadout='Synthetic recorded setup')
    book(store, sid, 'loot', 7, value=4, item_name='Shrapnel')
    recorder = TrailRecorder(store)
    points = HuntAttribution(store).update(sid, _kill_trend_points(store, sid), live=True)
    recorder.update(sid, points, True)
    saved_rows = originals(store)
    store = Store(store.db_path)
    store.set_loot_excluded(sid, key, False)
    history = store.load_chart_trails(sid, [group, next_group])
    assert history[group][:len(baseline_history)] == baseline_history
    assert history[group][-1][2] == pytest.approx(500.0005)
    assert history[next_group][-1] == pytest.approx((3, 2.5, 4))
    store.set_loot_excluded(sid, key, True)
    history = store.load_chart_trails(sid, [group, next_group])
    assert all(sample[2] == pytest.approx(.0005) for sample in history[group])
    assert history[next_group][-1][2] == 4
    assert store.get_session(sid).hunting_cost == pytest.approx(5.02)
    assert store.get_session(sid).loot_value == pytest.approx(4.0005)
    assert originals(store) == saved_rows


def test_appends_during_rebuild_are_included_before_atomic_publication(hunt, monkeypatch):
    store, sid, group, ordinary, key = hunt
    real = HuntAttribution.rebuild

    def append_during_plan(engine, raw, activation):
        result = real(engine, raw, activation)
        writer = Store(store.db_path)
        book(writer, sid, 'combat', 6, damage=1, shot_cost=3, loadout='Synthetic recorded setup')
        book(writer, sid, 'loot', 7, value=4, item_name='Shrapnel')
        return result

    monkeypatch.setattr(HuntAttribution, 'rebuild', append_during_plan)
    store.set_loot_excluded(sid, key, True)
    points = HuntAttribution(store).update(sid, _kill_trend_points(store, sid), False)
    assert [point['loot'] for point in points] == pytest.approx([.0005, 4])
    assert sum(point['cost'] or 0 for point in points) + points.attribution_pending == pytest.approx(5)
    assert store.get_session(sid).hunting_cost == pytest.approx(5.02)
    assert store.load_chart_trails(sid, [group])[group][-1][2] == pytest.approx(.0005)
    assert store.load_hunt_attribution(sid)[0]['processed_count'] == len(points)


def test_competing_history_edit_rejects_stale_plan_without_partial_change(hunt, monkeypatch):
    store, sid, group, ordinary, key = hunt
    real = HuntAttribution.rebuild
    fired = False

    def edit_during_plan(engine, raw, activation):
        nonlocal fired
        result = real(engine, raw, activation)
        if not fired:
            fired = True
            Store(store.db_path).set_loot_excluded(sid, ordinary, True)
        return result

    monkeypatch.setattr(HuntAttribution, 'rebuild', edit_during_plan)
    before = originals(store)
    with pytest.raises(ValueError, match='Another history edit'):
        store.set_loot_excluded(sid, key, True)
    assert originals(store) == before
    assert store.loot_event_history(sid, event_id=key)[0]['excluded'] == 0
    assert store.loot_event_history(sid, event_id=ordinary)[0]['excluded'] == 1
    assert store.get_session(sid).loot_value == 500


def test_failed_publication_rolls_back_membership_totals_fans_and_attribution(hunt, monkeypatch):
    store, sid, group, ordinary, key = hunt
    before, summary = originals(store), store.get_session(sid)
    trails, attribution = store.load_chart_trails(sid, [group]), store.load_hunt_attribution(sid)
    real = store._validate_hunt_accounting

    def fail_publication(*args):
        raise ValueError('synthetic validation failure')

    monkeypatch.setattr(store, '_validate_hunt_accounting', fail_publication)
    with pytest.raises(ValueError, match='synthetic validation failure'):
        store.set_loot_excluded(sid, key, True)
    monkeypatch.setattr(store, '_validate_hunt_accounting', real)
    assert store.get_session(sid) == summary
    assert store.load_chart_trails(sid, [group]) == trails
    assert store.load_hunt_attribution(sid) == attribution
    assert originals(store) == before
    assert store.loot_event_history(sid, event_id=key)[0]['excluded'] == 0


def test_old_cached_writers_cannot_restore_contaminated_fans_or_attribution(hunt):
    store, sid, group, ordinary, key = hunt
    state, decisions = deepcopy(store.load_hunt_attribution(sid))
    old_history = store.load_chart_trails(sid, [group])
    store.set_loot_excluded(sid, key, True)
    current = store.load_hunt_attribution(sid)
    with pytest.raises(ValueError, match='accounting changed'):
        store.save_chart_trails(sid, old_history, accounting_revision=0)
    with pytest.raises(ValueError, match='accounting changed'):
        store.save_hunt_attribution(sid, state, decisions)
    assert store.load_hunt_attribution(sid) == current
    assert store.load_chart_trails(sid, [group])[group][-1][2] == pytest.approx(.0005)


def test_retained_history_pages_find_old_receipts_during_new_ingestion(hunt):
    store, sid, group, ordinary, key = hunt
    for i in range(220):
        book(store, sid, 'loot', 5 + 2 * i, value=.01, item_name='Synthetic page receipt')
    assert key not in {row['id'] for row in _recent_events(store, sid, 80)}
    first = store.loot_event_history(sid, limit=100)
    assert key not in {row['id'] for row in first}
    book(store, sid, 'loot', 500, value=7, item_name='New arrival')
    second = store.loot_event_history(sid, before_id=first[-1]['id'], limit=100)
    third = store.loot_event_history(sid, before_id=second[-1]['id'], limit=100)
    ids = [row['id'] for row in first + second + third]
    assert len(ids) == len(set(ids))
    assert key in ids and ordinary in ids
    assert json.loads(store.loot_event_history(sid, event_id=key)[0]['payload'])['value'] == 500
    store.set_loot_excluded(sid, key, True)
    assert store.loot_event_history(sid, event_id=key, excluded=True)[0]['excluded']
    assert not store.loot_event_history(sid, event_id=key, excluded=False)


def test_missing_foreign_and_nonloot_events_do_not_change_accounting(hunt):
    store, sid, group, ordinary, key = hunt
    other = store.start_session('hunt')
    before = originals(store)
    summary = store.get_session(sid)
    for run, event_id in ((other, key), (sid, group), (sid, key + 9999)):
        with pytest.raises(ValueError, match='loot receipt'):
            store.set_loot_excluded(run, event_id, True)
    assert store.get_session(sid) == summary
    assert originals(store) == before


def test_receipt_without_surviving_group_recovers_its_observed_fan_when_restored(tmp_path):
    store = Store(tmp_path / 'single.sqlite3')
    sid = store.start_session()
    receipt = book(store, sid, 'loot', 1, value=500, item_name='Strongbox Key')
    store.save_chart_trails(sid, {receipt: [(0, 0, 500)]})
    store.set_loot_excluded(sid, receipt, True)
    assert _kill_trend_points(store, sid) == []
    assert store.load_chart_trails(sid, [receipt]) == {}
    store = Store(store.db_path)
    store.set_loot_excluded(sid, receipt, False)
    assert store.load_chart_trails(sid, [receipt]) == {receipt: ((0, 0, 500),)}
    assert store.get_session(sid).loot_value == 500


def test_rebuilt_shared_attribution_conserves_weapon_input_not_healing(tmp_path):
    store = Store(tmp_path / 'shared.sqlite3')
    sid = store.start_session()
    engine = HuntAttribution(store)
    engine.update(sid, _kill_trend_points(store, sid), True)
    clock, receipts = 0.0, []
    for count in [10] * 6 + [30, 0, 0]:
        for _ in range(count):
            clock += .01
            book(store, sid, 'combat', clock, shot_cost=.1, damage=1, loadout='Same-context carbine')
        clock += 2
        receipts.append(book(store, sid, 'loot', clock, value=1.5, item_name='Shrapnel'))
        engine.update(sid, _kill_trend_points(store, sid), True)
    assert store.load_hunt_attribution(sid)[1]
    book(store, sid, 'combat', clock + .1, healed=10, healing_action='self', healing_cost=.02,
         healing_cost_status='selected_tool_estimate')
    before = originals(store)
    store.set_loot_excluded(sid, receipts[0], True)
    raw = _kill_trend_points(store, sid)
    projected = HuntAttribution(Store(store.db_path)).update(sid, raw, False)
    assert sum(point['cost'] or 0 for point in projected) + projected.attribution_pending == pytest.approx(9)
    assert sum(point['cost'] or 0 for point in raw) == pytest.approx(9)
    assert store.get_session(sid).hunting_cost == pytest.approx(9.02)
    assert store.get_session(sid).loot_value == pytest.approx(12)
    assert store.load_hunt_attribution(sid)[0] is not None
    assert originals(store) == before
    store.set_loot_excluded(sid, receipts[0], False)
    assert store.get_session(sid).loot_value == pytest.approx(13.5)
    assert store.get_session(sid).hunting_cost == pytest.approx(9.02)


def test_history_correction_is_not_new_loot_feedback():
    tracker = ArrivalTracker()
    tracker.observe(Snapshot('run', .0005, 2, accounting_revision=1), True)
    assert not tracker.observe(Snapshot('run', 500.0005, 3, accounting_revision=2), True).active
    arrival = tracker.observe(Snapshot('run', 501.0005, 4, accounting_revision=2), True)
    assert arrival.active and arrival.loot == pytest.approx(1)


def test_private_file_reader_continues_across_exclusion_and_heal_replay(tmp_path):
    from test_log_ingestion import ScriptedStop, append
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / 'reader.sqlite3')
    app.catalog = Catalog.load()
    app._log_queue = queue.Queue()
    gear = LoadoutRecord(None, 'Synthetic reader', 'Frontier Rifle', ammo_burn=100, decay=.0002, cost_per_shot=.0102)
    app.store.save_loadout(gear, make_active=True)
    app.store.set_selected_healing_tool(FAP)
    sid = app.store.start_session('hunt', gear)
    path = tmp_path / 'chat.log'
    path.write_text('2026-01-01 10:00:00 [System] [] You received Strongbox Key x (1) Value: 500 PED\n')
    heal = '2026-01-01 10:00:02 [System] [] You healed yourself 23.0 points\n'

    def correct_and_append():
        key = app.store.loot_event_history(sid)[0]['id']
        assert app.store.set_loot_excluded(sid, key, True)
        append(path, ('2026-01-01 10:00:01 [System] [] You inflicted 6.0 points of damage\n' + heal +
                      '2026-01-01 10:00:03 [System] [] You received Shrapnel x (1) Value: 0.0005 PED\n').encode())

    app._log_worker_loop(path, sid, 0, ScriptedStop([correct_and_append]))
    summary = app.store.get_session(sid)
    assert summary.loot_value == pytest.approx(.0005)
    assert summary.hunting_cost == pytest.approx(.0302)
    assert summary.healing_cost == pytest.approx(.02)
    assert summary.events == 3
    assert len(originals(app.store)) == 4
    assert not any(kind == 'error' for kind, _ in app._log_queue.queue)
    priced = originals(app.store)[2]
    app.store = Store(app.store.db_path)
    app._process_lines([heal], sid, None)
    assert app.store.get_session(sid).healing_cost == pytest.approx(.02)
    assert originals(app.store)[2] == priced
    key = app.store.loot_event_history(sid, excluded=True)[0]['id']
    app.store.set_loot_excluded(sid, key, False)
    assert app.store.get_session(sid).loot_value == pytest.approx(500.0005)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(.0302)
