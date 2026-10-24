"""Exercise real file appends through the worker, parser and SQLite, not just regexes."""
from __future__ import annotations

import queue
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.catalog import Catalog
from ped_hunter.storage import LoadoutRecord, Store


SHOT = b'2026-10-24 04:36:26 [System] [] You inflicted 9.6 points of damage\r\n'
LOOT = b'2026-10-24 04:36:36 [System] [] You received Shrapnel x (527) Value: 0.0527 PED\r\n'
SKILL = '2026-10-24 04:36:27 [System] [] You have gained 0.0460 experience in your Rifle skill\r\n'.encode()


class ScriptedStop:
    """Each wait represents a completed poll; write the next chunk deterministically."""
    def __init__(self, steps):
        self.steps = iter(steps)

    def is_set(self):
        return False

    def wait(self, _timeout):
        action = next(self.steps, None)
        if action is None:
            return True
        action()
        return False


@pytest.fixture
def worker(tmp_path):
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / 'worker.sqlite3')
    app.catalog = Catalog.load()
    app._log_queue = queue.Queue()
    loadout = LoadoutRecord(id=None, name='Test rifle', weapon='Frontier Rifle',
                            ammo_burn=100, decay=0.0002, cost_per_shot=0.0102)
    app.store.save_loadout(loadout, make_active=True)
    sid = app.store.start_session('hunt', loadout)
    path = tmp_path / 'chat.log'
    path.write_bytes(b'')
    return app, sid, path


def append(path, value):
    with path.open('ab') as stream:
        stream.write(value)


def run_polls(worker, steps, start_size=0):
    app, sid, path = worker
    app._log_worker_loop(path, sid, start_size, ScriptedStop(steps))
    return app.store.get_session(sid)


def test_worker_ingests_appended_combat_loot_skill_with_costs(worker):
    session = run_polls(worker, [lambda: append(worker[2], SHOT + LOOT + SKILL)])
    assert session.events == 3
    assert session.combat_damage == 9.6
    assert session.loot_value == pytest.approx(0.0527)
    assert session.hunting_cost == pytest.approx(0.0102)
    assert worker[0]._log_queue.get_nowait() == ('parsed', 3)


@pytest.mark.parametrize('split', [10, 45, 70, len(LOOT) - 1])
def test_split_writes_are_buffered_until_newline_and_ingested_exactly_once(worker, split):
    app, sid, path = worker
    def first():
        append(path, LOOT[:split])
    def finish():
        assert app.store.get_session(sid).events == 0, 'Do not commit an incomplete line'
        append(path, LOOT[split:])
    session = run_polls(worker, [first, finish, lambda: None])
    assert session.events == 1
    assert session.loot_value == pytest.approx(0.0527)


def test_utf8_split_between_bytes_preserves_skill_name(worker):
    line = '2026-10-24 04:36:27 [System] [] Your Médecine has improved by 0.25\n'.encode()
    split = line.index('é'.encode()) + 1
    session = run_polls(worker, [lambda: append(worker[2], line[:split]), lambda: append(worker[2], line[split:])])
    assert session.events == 1
    assert worker[0].store.skill_gains_for_session(worker[1])[0].skill == 'Médecine'


def test_resume_skips_history_but_reads_new_appends(worker):
    worker[2].write_bytes(LOOT)
    session = run_polls(worker, [lambda: append(worker[2], SHOT)], start_size=len(LOOT))
    assert session.events == 1
    assert session.loot_value == 0
    assert session.combat_damage == 9.6


def test_truncation_discards_old_partial_line(worker):
    app, sid, path = worker
    session = run_polls(worker, [lambda: append(path, LOOT[:-2]), lambda: path.write_bytes(SHOT)])
    assert session.events == 1
    assert session.combat_damage == 9.6
    assert session.loot_value == 0


def test_replacement_larger_than_old_file_reads_from_start(worker):
    app, sid, path = worker
    path.write_bytes(SHOT)
    replacement = path.with_suffix('.new')
    replacement.write_bytes(LOOT + SKILL)
    session = run_polls(worker, [lambda: replacement.replace(path)], start_size=len(SHOT))
    assert session.events == 2
    assert session.loot_value == pytest.approx(0.0527)


def test_ended_session_never_receives_worker_events(worker):
    app, sid, path = worker
    app.store.end_session(sid)
    session = run_polls(worker, [lambda: append(path, SHOT + LOOT)])
    assert session.events == 0


def test_malformed_numeric_line_does_not_drop_following_valid_events(worker):
    bad = b'2026-10-24 04:36:26 [System] [] You inflicted 9..6 points of damage\n'
    session = run_polls(worker, [lambda: append(worker[2], bad + SHOT + LOOT)])
    assert session.events == 2
    assert session.loot_value == pytest.approx(0.0527)


def test_missing_file_notice_is_deduplicated_and_worker_recovers(worker):
    app, sid, path = worker
    session = run_polls(worker, [path.unlink, lambda: None, lambda: path.write_bytes(LOOT)])
    assert session.events == 1
    messages = list(app._log_queue.queue)
    assert len([m for m in messages if m[0] == 'error']) == 1
    assert ('parsed', 1) in messages


def test_idle_log_warning_then_new_data_recovers_without_restart(worker, monkeypatch):
    from ped_hunter import app as appmod
    monkeypatch.setattr(appmod.time, 'monotonic', Mock(side_effect=[0, 31, 32, 33]))
    session = run_polls(worker, [lambda: None, lambda: append(worker[2], LOOT)])
    assert session.events == 1
    messages = list(worker[0]._log_queue.queue)
    warnings = [text for kind, text in messages if kind == 'status' and 'No changes' in str(text)]
    assert len(warnings) == 1
    assert 'chat logging' in warnings[0]
    assert ('parsed', 1) in messages
    assert any('Reading new lines' in str(text) for kind, text in messages)


def test_same_file_truncated_and_regrown_larger_is_read_from_start(worker):
    app, sid, path = worker
    path.write_bytes(SHOT)
    session = run_polls(worker, [lambda: path.write_bytes(LOOT + SKILL)], start_size=len(SHOT))
    assert session.events == 2
    assert session.loot_value == pytest.approx(0.0527)


def test_resume_from_historical_partial_line_does_not_ingest_its_suffix(worker):
    app, sid, path = worker
    path.write_bytes(LOOT[:40])
    session = run_polls(worker, [lambda: append(path, LOOT[40:] + SHOT)], start_size=40)
    assert session.events == 1
    assert session.loot_value == 0


@pytest.mark.parametrize('resume', [False, True])
def test_real_gui_start_or_resume_updates_totals_event_rows_chart_and_streamer(tmp_path, resume):
    store = Store(tmp_path / 'gui.sqlite3')
    loadout = LoadoutRecord(id=None, name='Test rifle', weapon='Frontier Rifle',
                            ammo_burn=100, decay=0.0002, cost_per_shot=0.0102)
    store.save_loadout(loadout, make_active=True)
    saved = store.start_session('hunt', loadout) if resume else None
    path = tmp_path / 'chat.log'
    path.write_bytes(LOOT)  # Historical line must not inflate the new live run.
    app = PedHunterApp(store=store)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    try:
        app.reduced_motion.set(True)
        app.currency_mode.set('PED')
        app.chat_path.set(str(path))
        app.events_tab.master.select(app.events_tab)
        app.update()
        app.resume_selected_session() if resume else app.start()
        if resume:
            assert app.session_id == saved
        app._open_streamer_window()
        append(path, SHOT[:30])
        # Give the real worker a poll before completing the line.
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        assert store.get_session(app.session_id).events == 0
        append(path, SHOT[30:] + LOOT + SKILL)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            app.update()
            if app.hero_events.get() == '3' and len(app.events_tree.get_children()) == 3 and app.loot_chart_points:
                break
            time.sleep(0.01)
        session = store.get_session(app.session_id)
        assert session.events == 3
        assert session.loot_value == pytest.approx(0.0527)
        assert app.hero_events.get() == '3'
        assert len(app.events_tree.get_children()) == 3
        assert app.loot_chart_points
        assert app.streamer_window.vars['loot'].get() == '0.05 PED'
        assert app.streamer_window.vars['state'].get() == 'TRACKING'
        assert not errors
    finally:
        app._stop_log_worker()
        app.destroy()


def test_display_exception_does_not_permanently_stop_queue_drain():
    app = object.__new__(PedHunterApp)
    app.running = True
    app._log_drain_pending = True
    app._log_queue = queue.Queue()
    app._log_queue.put(('parsed', 1))
    app._log_worker = Mock()
    app._log_worker.is_alive.return_value = True
    app.status_text = Mock()
    app._refresh_live_views = Mock(side_effect=RuntimeError('streamer draw failed'))
    app._schedule_full_refresh = Mock()
    app._schedule_log_drain = Mock()
    app._drain_log_queue()
    app._schedule_log_drain.assert_called_once_with()
    app._schedule_full_refresh.assert_called_once_with()
    assert 'saved' in app.status_text.set.call_args.args[0].lower()
    app._refresh_live_views.side_effect = None
    app._log_queue.put(('parsed', 2))
    app._drain_log_queue()
    assert app._refresh_live_views.call_count == 2
