"""Real Tk/SQLite/file behavior while recorded Resume views are being prepared."""
from dataclasses import asdict
import json
import sqlite3
import threading
import time

import pytest

import ped_hunter.app as module
from ped_hunter.app import PedHunterApp, calculate_loadout_cost
from ped_hunter.catalog import Catalog
from ped_hunter.resume_handoff import has_resume_handoff, read_resume_handoff, stage_resume_handoff
from ped_hunter.storage import LoadoutRecord, Store


OLD = [
    '2026-01-01 10:00:00 [System] [] You inflicted 4.0 points of damage',
    '2026-01-01 10:00:01 [System] [] You received Animal Muscle Oil x (1) Value: 0.02 PED',
]
SHOT = '2026-01-01 10:01:00 [System] [] You inflicted 5.0 points of damage'
LOOT = '2026-01-01 10:01:01 [System] [] You received Animal Muscle Oil x (1) Value: 0.03 PED'


def append(path, lines):
    with path.open('ab') as stream:
        stream.write(('\r\n'.join(lines) + '\r\n').encode())


def rows(store):
    with store.connect() as conn:
        return [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]


def until(app, predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), 'Owned Resume fixture did not reach the required state'


@pytest.fixture
def run(tmp_path, monkeypatch):
    path = tmp_path / 'chat.log'
    path.write_bytes(b'')
    append(path, OLD)
    store = Store(tmp_path / 'resume.sqlite3')
    setup = LoadoutRecord(id=None, name='Resume rifle', weapon='Frontier Hunting Rifle',
                          ammo_burn=100, decay=.0002, cost_per_shot=.0102)
    setup.id = store.save_loadout(setup, make_active=True)
    sid = store.start_session('hunt', setup)
    headless = object.__new__(PedHunterApp)
    headless.store, headless.catalog = store, Catalog.load()
    headless._process_lines(OLD, sid, None)
    monkeypatch.setattr(module, '_default_chat_log_path', lambda: path)
    errors, information, confirmations = [], [], []
    monkeypatch.setattr(module.messagebox, 'showerror', lambda *args, **kwargs: errors.append(args))
    monkeypatch.setattr(module.messagebox, 'showinfo', lambda *args, **kwargs: information.append(args))
    monkeypatch.setattr(module.messagebox, 'askyesno', lambda *args, **kwargs: confirmations.append(args) or True)
    app = PedHunterApp(store=store)
    app.reduced_motion.set(True)
    app.feedback_sounds.set(False)
    app.currency_mode.set('PED')
    app.sessions_tree.selection_set(sid)
    app.update()
    releases = []
    yield app, sid, path, errors, information, confirmations, releases
    for release in releases:
        release.set()
    app._resume_pending = None
    app._stop_log_worker()
    app.destroy()


@pytest.mark.parametrize('ended', [False, True])
def test_resume_services_ui_and_preserves_arrivals_while_history_is_loading(run, monkeypatch, ended):
    app, sid, path, errors, information, confirmations, releases = run
    if ended:
        app.store.end_session(sid)
    original = rows(app.store)
    entered, release = threading.Event(), threading.Event()
    releases.append(release)
    query_released = []
    original_read = app.store.list_all_sessions

    def held_read():
        entered.set()
        query_released.append(release.wait(5))
        return original_read()

    monkeypatch.setattr(app.store, 'list_all_sessions', held_read)
    app.resume_selected_session()
    assert entered.wait(2)
    append(path, [SHOT, SHOT, LOOT])  # Repeated consecutive line must charge once.
    app.loadout_name.set('Unsaved draft must survive')
    app._open_streamer_window()  # Opening during preparation must not do the slow read on Tk.
    serviced = []
    app.after(0, lambda: (serviced.append(True), release.set()))
    until(app, lambda: app.hero_events.get() == '4')
    current = app.store.get_session(sid)
    assert serviced and query_released and all(query_released)
    assert current.events == 4
    assert current.hunting_cost == pytest.approx(.0204)
    assert current.loot_value == pytest.approx(.05)
    assert rows(app.store)[:len(original)] == original
    assert app.session_id == sid and app.running
    assert app.store.get_active_loadout().name == 'Resume rifle'
    assert app.loadout_name.get() == 'Unsaved draft must survive'
    assert app.currency_mode.get() == 'PED' and app.reduced_motion.get()
    assert not app.feedback_sounds.get()
    assert app.streamer_window.vars['cost'].get() == '0.02 PED'
    assert app.streamer_window.vars['loot'].get() == '0.05 PED'
    assert bool(confirmations) == ended
    assert not information and not errors


def test_cancelled_restore_cannot_restart_its_reader_over_a_new_run(run, monkeypatch):
    app, sid, path, errors, information, confirmations, releases = run
    original = rows(app.store)
    entered, release = threading.Event(), threading.Event()
    releases.append(release)
    original_read = app.store.list_all_sessions

    def held_read():
        if threading.current_thread().name == 'ped-hunter-resume-view':
            entered.set()
            release.wait(5)
        return original_read()

    monkeypatch.setattr(app.store, 'list_all_sessions', held_read)
    app.resume_selected_session()
    assert entered.wait(2)
    app.stop()
    app.start()
    new_sid = app.session_id
    assert new_sid != sid
    release.set()
    append(path, [SHOT, LOOT])
    until(app, lambda: app.store.get_session(new_sid).events == 2)
    assert app.session_id == new_sid and app.running
    assert app.store.get_session(sid).ended_at is not None
    assert app.store.get_session(sid).events == len(original)
    assert rows(app.store)[:len(original)] == original
    assert app.store.get_session(new_sid).hunting_cost == pytest.approx(.0102)
    assert app.store.get_session(new_sid).loot_value == pytest.approx(.03)
    assert not errors


def test_setup_change_invalidates_prepared_view_without_repricing_old_events(run, monkeypatch):
    app, sid, path, errors, information, confirmations, releases = run
    original = rows(app.store)
    entered, release = threading.Event(), threading.Event()
    releases.append(release)
    load = module._load_resume_view
    attempts = []

    def held_snapshot(store, selected):
        view = load(store, selected)
        attempts.append(view)
        if len(attempts) == 1:
            entered.set()
            release.wait(5)
        return view

    monkeypatch.setattr(module, '_load_resume_view', held_snapshot)
    app.resume_selected_session()
    assert entered.wait(2)
    name = 'Basic Mining Laser, Type M (L)'
    ammo, decay = calculate_loadout_cost(catalog=app.catalog, weapon_name=name)
    changed = LoadoutRecord(id=None, name='Changed while restoring', weapon=name,
                            ammo_burn=ammo, decay=decay, cost_per_shot=.0223)
    changed.id = app.store.save_loadout(changed, make_active=True)
    app.store.update_session_loadout(sid, changed)
    release.set()
    until(app, lambda: app._resume_pending is None and app._log_worker is not None)
    assert changed.name in app.hero_session.get()
    append(path, [SHOT, LOOT])
    until(app, lambda: app.hero_events.get() == '4')
    current = app.store.get_session(sid)
    assert current.loadout_snapshot['weapon'] == name
    assert app.store.get_active_loadout().id == changed.id
    assert current.hunting_cost == pytest.approx(.0102 + .0223)
    assert current.loot_value == pytest.approx(.05)
    assert rows(app.store)[:len(original)] == original
    assert name in app.active_loadout_details.get()
    assert not errors and not information


def test_failed_preparation_keeps_claim_and_never_falls_through_to_eof(run, monkeypatch):
    app, sid, path, errors, information, confirmations, releases = run
    context = asdict(app.store.get_active_loadout())
    stage_resume_handoff(app.store.db_path, sid, path, OLD, 'isolated-failed-restore', context)
    append(path, [SHOT, LOOT])
    original = rows(app.store)

    def fail_read():
        raise sqlite3.OperationalError('Isolated snapshot read failure')

    monkeypatch.setattr(app.store, 'list_all_sessions', fail_read)
    app.resume_selected_session()
    until(app, lambda: bool(errors))
    assert not app.running and app._log_worker is None
    assert has_resume_handoff(app.store.db_path)
    with pytest.raises(ValueError):
        read_resume_handoff(app.store.db_path, sid, path, context)
    app.resume_selected_session()  # A retained claim must not look like no handoff.
    assert not app.running and app._log_worker is None
    assert rows(app.store) == original
    assert app.store.get_session(sid).hunting_cost == pytest.approx(.0102)
    assert not information


@pytest.mark.parametrize('encoded', [None, 'null', '[]', '{bad'])
def test_resume_metadata_keeps_missing_legacy_snapshot_distinct_from_missing_session(tmp_path, encoded):
    store = Store(tmp_path / 'legacy.sqlite3')
    sid = store.start_session()
    with store.connect() as conn:
        conn.execute('UPDATE sessions SET loadout_snapshot=? WHERE id=?', (encoded, sid))
        conn.commit()
    metadata = store.get_session_resume_state(sid)
    assert metadata.ended_at is None and metadata.loadout_snapshot is None
    assert store.get_session_resume_state('unknown-session') is None


def test_explicit_empty_lifetime_snapshot_does_not_requery_existing_runs(tmp_path):
    store = Store(tmp_path / 'totals.sqlite3')
    sid = store.start_session()
    store.add_event(sid, {'kind': 'loot', 'raw_message': 'Recorded return', 'payload': {'value': 3}})
    assert store.lifetime_totals().total_loot == 3
    totals = store.lifetime_totals([])
    assert totals.session_count == 0 and totals.total_loot == 0 and totals.total_cost == 0
