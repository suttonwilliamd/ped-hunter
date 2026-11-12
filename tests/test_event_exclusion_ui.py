"""Real history controls, private SQLite and asynchronous projection publication."""
import threading
import time

import pytest

from ped_hunter import app as appmod
from ped_hunter.currency import format_money
from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.storage import LoadoutRecord, Store
from test_event_exclusion import book, originals


def pump(app, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.update()
        threading.Event().wait(.005)
    assert predicate(), 'The isolated history operation did not finish'
    app.update_idletasks()


@pytest.fixture
def history_app(tmp_path, monkeypatch):
    chat = tmp_path / 'synthetic-chat.log'
    chat.write_text('')
    monkeypatch.setattr(appmod, '_default_chat_log_path', lambda: chat)
    monkeypatch.setattr('ped_hunter.event_history.messagebox.askyesno', lambda *args, **kwargs: True)
    store = Store(tmp_path / 'history-ui.sqlite3')
    gear = LoadoutRecord(None, 'Synthetic history setup', 'Frontier Rifle', ammo_burn=100, decay=.01, cost_per_shot=2)
    gear.id = store.save_loadout(gear, make_active=True)
    sid = store.start_session('hunt', gear)
    group = book(store, sid, 'combat', 0, damage=1, shot_cost=2, loadout=gear.name)
    book(store, sid, 'loot', 1, value=.0005, item_name='Shrapnel')
    key = book(store, sid, 'loot', 1.1, value=500, item_name='Strongbox Key')
    store.save_chart_trails(sid, {group: [(2, 2, .0005), (2, 2, 500.0005)]})
    for i in range(220):
        book(store, sid, 'loot', 5 + i * 2, value=.01, item_name='Synthetic page receipt')
    app = appmod.PedHunterApp(store=store)
    app.title('PED Hunter isolated event-exclusion test')
    app.reduced_motion.set(True)
    app.currency_mode.set('PED')
    app.session_id, app.running, app.last_size = sid, True, 987654
    app.current_log_path = chat
    app._refresh_all()
    app._open_streamer_window()
    app._open_event_history()
    app.update()
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    monkeypatch.setattr('ped_hunter.event_history.messagebox.showerror', lambda *args, **kwargs: errors.append(args))
    try:
        yield app, app._event_history_window, sid, group, key, errors
    finally:
        app._stop_log_worker()
        app.animator.cancel_all()
        app.destroy()


def test_history_controls_reach_old_receipt_exclude_and_restore_without_resuming(history_app):
    app, window, sid, group, key, errors = history_app
    rows = originals(app.store)
    summary = app.store.get_session(sid)
    cursor = app.running, app.session_id, app.current_log_path, app.last_size, app._log_worker
    assert not app.events_tree.exists(str(key))
    assert not window.tree.exists(str(key))
    first = set(window.tree.get_children())
    window.older_button.invoke()
    assert not first.intersection(window.tree.get_children())
    window.newer_button.invoke()
    assert set(window.tree.get_children()) == first
    window.event_id.set(str(key))
    window.find_button.invoke()
    assert window.tree.selection() == (str(key),)
    assert '500' in window.details.get('1.0', 'end')
    window.exclude_button.invoke()
    pump(app, lambda: not window.busy)
    assert window.tree.item(str(key), 'values')[-1] == 'Excluded'
    assert str(window.restore_button.cget('state')) == 'normal'
    corrected = app.store.get_session(sid)
    assert corrected.loot_value == pytest.approx(summary.loot_value - 500)
    assert app.hero_net.get() == format_money(corrected.net_value, 'PED', signed=True)
    assert app.lifetime_vars['total_loot'].get() == format_money(corrected.loot_value, 'PED')
    assert app.sessions_tree.item(sid, 'values')[3] == format_money(corrected.loot_value, 'PED')
    assert app.streamer_window.vars['net_big'].get() == app.hero_net.get()
    assert app.store.load_chart_trails(sid, [group])[group][-1][2] == pytest.approx(.0005)
    assert (app.running, app.session_id, app.current_log_path, app.last_size, app._log_worker) == cursor
    assert originals(app.store) == rows
    window.restore_button.invoke()
    pump(app, lambda: not window.busy)
    assert window.tree.item(str(key), 'values')[-1] == 'Included'
    assert app.store.get_session(sid).loot_value == summary.loot_value
    assert originals(app.store) == rows
    assert not errors


def test_history_rebuild_keeps_tk_responsive_and_incorporates_concurrent_ingestion(history_app, monkeypatch):
    app, window, sid, group, key, errors = history_app
    entered, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    real = HuntAttribution.rebuild

    def held(engine, raw, activation):
        entered.set()
        assert release.wait(8), 'Synthetic rebuild hold was not released'
        return real(engine, raw, activation)

    monkeypatch.setattr(HuntAttribution, 'rebuild', held)
    before = app.store.get_session(sid)
    window.event_id.set(str(key))
    window.find_event()
    window.exclude_button.invoke()
    try:
        pump(app, entered.is_set)
        app.after(10, heartbeat.set)
        pump(app, heartbeat.is_set, 2)
        assert window.busy
        book(app.store, sid, 'loot', 600, value=7, item_name='Arrival while rebuilding')
        assert app.store.loot_event_history(sid, event_id=key)[0]['excluded'] == 0
    finally:
        release.set()
    pump(app, lambda: not window.busy)
    assert app.store.get_session(sid).loot_value == pytest.approx(before.loot_value - 500 + 7)
    assert app.hero_loot.get() == format_money(before.loot_value - 500 + 7, 'PED')
    assert app.running and app.last_size == 987654
    assert not errors


def test_restart_can_find_and_restore_excluded_receipt_without_starting_tracking(history_app):
    app, window, sid, group, key, errors = history_app
    app.store.set_loot_excluded(sid, key, True)
    window.destroy()
    restarted = appmod.PedHunterApp(store=Store(app.store.db_path))
    restarted.title('PED Hunter isolated restarted history test')
    try:
        restarted.reduced_motion.set(True)
        restarted._open_event_history()
        history = restarted._event_history_window
        history.event_id.set(str(key))
        history.find_event()
        assert history.tree.item(str(key), 'values')[-1] == 'Excluded'
        assert str(history.restore_button.cget('state')) == 'normal'
        history.restore_button.invoke()
        pump(restarted, lambda: not history.busy)
        assert history.tree.item(str(key), 'values')[-1] == 'Included'
        assert restarted.store.get_session(sid).loot_value == pytest.approx(502.2005)
        assert not restarted.running and restarted.session_id is None
        assert restarted._log_worker is None
    finally:
        restarted.destroy()
    assert not errors


def test_persisted_history_controls_fit_minimum_dialog_without_hidden_actions(history_app):
    app, window, sid, group, key, errors = history_app
    window.geometry(f'{round(640 * app.display_scale)}x{round(440 * app.display_scale)}')
    app.update()
    for widget in (window.run_combo, window.filter_combo, window.id_entry, window.find_button,
                   window.tree, window.newest_button, window.older_button, window.exclude_button, window.restore_button):
        assert widget.winfo_ismapped()
        assert widget.winfo_width() > 10 and widget.winfo_height() > 10
        assert widget.winfo_rootx() >= window.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= window.winfo_rootx() + window.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    assert not errors
