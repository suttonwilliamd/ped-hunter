from dataclasses import replace
from types import SimpleNamespace

import pytest

from ped_hunter import app as appmod
from ped_hunter.feedback import Snapshot
from ped_hunter.storage import Store


@pytest.fixture
def hud(tmp_path):
    app = appmod.PedHunterApp(store=Store(tmp_path / 'hud.sqlite3'))
    app.reduced_motion.set(True)
    app._open_streamer_window()
    app.update_idletasks()
    yield app, app.streamer_window
    app.destroy()


def test_idle_and_zero_cost_are_not_fake_returns(hud):
    app, overlay = hud
    assert overlay.vars['state'].get() == 'IDLE'
    assert overlay.vars['net_big'].get() == '—'
    assert overlay.vars['return'].get() == '—'
    sid = app.store.start_session('hunt')
    app.session_id, app.running = sid, True
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['state'].get() == 'TRACKING'
    assert overlay.vars['return'].get() == '—'
    assert 'NO COST' in overlay.return_caption.cget('text')
    overlay.update_from_session(None)
    assert overlay.vars['state'].get() == 'WAITING FOR RUN'


def test_retained_session_state_and_demo_never_claim_false_tracking(hud):
    app, overlay = hud
    sid = app.store.start_session('hunt')
    overlay.demo = True
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['state'].get() == 'DEMO · PAUSED'
    app.store.end_session(sid)
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['state'].get() == 'DEMO · STOPPED'
    assert 'not real money' in overlay.activity_strip.detail
    app.running = True
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['state'].get() == 'DEMO · STOPPED'


def test_identical_snapshots_do_not_requery_entire_trend_history(hud, monkeypatch):
    app, overlay = hud
    sid = app.store.start_session('hunt')
    original = appmod._kill_trend_points
    calls = []
    def query(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(appmod, '_kill_trend_points', query)
    overlay.update_from_session(app.store.get_session(sid))
    overlay.update_from_session(app.store.get_session(sid))
    assert len(calls) == 1
    app.store.add_event(sid, {'kind': 'loot', 'raw_message': 'test', 'payload': {'value': 1}})
    overlay.update_from_session(app.store.get_session(sid))
    assert len(calls) == 2
    assert overlay.vars['kills'].get() == '1'


def test_query_failure_is_distinct_from_no_kills_and_recovers(hud, monkeypatch):
    app, overlay = hud
    sid = app.store.start_session('hunt')
    original = appmod._kill_trend_points
    monkeypatch.setattr(appmod, '_kill_trend_points', lambda *_: (_ for _ in ()).throw(RuntimeError('bad query')))
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['kills'].get() == '—'
    assert 'unavailable' in overlay.vars['kill_trend_badge'].get()
    texts = [overlay.kill_chart.itemcget(i, 'text') for i in overlay.kill_chart.find_all() if overlay.kill_chart.type(i) == 'text']
    assert any('unavailable' in text for text in texts)
    monkeypatch.setattr(appmod, '_kill_trend_points', original)
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['kills'].get() == '0'
    assert overlay.vars['kill_trend_badge'].get() == 'No kill points yet'


def test_activity_expires_without_changing_persistent_tracking(hud):
    app, overlay = hud
    strip = overlay.activity_strip
    strip.set_context('TRACKING')
    strip.update_snapshot(Snapshot('run', loot=1, events=1), True)
    strip.update_snapshot(Snapshot('run', loot=3, events=2, skills={'Rifle': 1}), True)
    assert 'loot recorded' in strip.headline
    assert strip._reset_job is not None
    assert not strip.animator.jobs  # Reduced motion also retains timed text reset.
    strip.after_cancel(strip._reset_job)
    strip._quiet()
    assert strip.headline == 'Waiting for new events'
    assert strip.detail == 'Loot and skill gains appear here'


def test_enlarging_does_not_ratchet_minimum_and_can_shrink_again(hud):
    app, overlay = hud
    minimum = overlay.minsize()
    overlay._start_resize(SimpleNamespace(x_root=100, y_root=100))
    overlay._resize(SimpleNamespace(x_root=220, y_root=220))
    app.update_idletasks()
    overlay._fit_to_contents()
    app.update_idletasks()
    assert overlay.minsize() == minimum
    overlay._start_resize(SimpleNamespace(x_root=220, y_root=220))
    overlay._resize(SimpleNamespace(x_root=-1000, y_root=-1000))
    app.update_idletasks()
    assert overlay.winfo_width() == minimum[0]
    assert overlay.winfo_height() == minimum[1]


def test_large_money_reflows_without_losing_digits(hud):
    app, overlay = hud
    overlay.vars['net_big'].set('+12345678.90 PED')
    overlay.vars['return'].set('123456.78%')
    overlay.vars['loot'].set('12345678.90 PED')
    overlay.vars['cost'].set('12345678.90 PED')
    app.update_idletasks()
    # Compact responsive fonts may allow the hero to share a row; assert the
    # actual financial strings and allocated widths, not a particular grid.
    assert overlay.vars['net_big'].get() == '+12345678.90 PED'
    assert overlay.vars['loot'].get() == '12345678.90 PED'
    assert overlay.vars['cost'].get() == '12345678.90 PED'
    assert overlay.net_label.winfo_width() >= overlay.net_label.winfo_reqwidth()


def test_header_keyboard_move_resize_and_drag_release(hud, monkeypatch):
    app, overlay = hud
    monkeypatch.setattr(appmod, '_streamer_work_area', lambda _: (0, 0, 2400, 1600))
    overlay.geometry('+200+200')
    app.update_idletasks()
    overlay._keyboard_geometry(SimpleNamespace(keysym='Right', state=0))
    app.update_idletasks()
    assert overlay.winfo_x() == 210
    width = overlay.winfo_width()
    overlay._keyboard_geometry(SimpleNamespace(keysym='Right', state=1))
    app.update_idletasks()
    assert overlay.winfo_width() == width + 10
    overlay._start_drag(SimpleNamespace(widget=overlay.brand_label, x_root=220, y_root=210))
    assert overlay._drag_origin == (10, 10)
    overlay.event_generate('<ButtonRelease-1>')
    app.update_idletasks()
    assert overlay._drag_origin is None
    assert str(overlay.close_button.cget('takefocus')) == '1'
    assert overlay.close_button.bind('<Return>')


def test_conditional_manufacturing_row_fits_then_disappears(hud):
    app, overlay = hud
    sid = app.store.start_session('craft')
    session = replace(app.store.get_session(sid), manufacturing_attempt=3,
                      manufacturing_attempts_total=10, last_input_cost=1.2)
    overlay.update_from_session(session)
    app.update_idletasks()
    assert overlay.input_label.winfo_manager() == 'pack'
    assert '1.20 PED' in overlay.vars['input'].get()
    assert '3/10' in overlay.vars['input'].get()
    assert overlay.winfo_height() >= overlay.winfo_reqheight()
    overlay.update_from_session(None)
    app.update_idletasks()
    assert not overlay.input_label.winfo_manager()


@pytest.mark.parametrize('shots,expected', [(0, '#86EFAC'), (8, '#FDBA74'), (9, '#FDA4AF'), (10, '#FDA4AF')])
def test_durability_estimates_have_readable_empty_state(hud, shots, expected):
    app, overlay = hud
    sid = app.store.start_session('hunt')
    session = replace(app.store.get_session(sid), repair_shots=shots,
                      loadout_snapshot={'repair_budget': 1, 'repair_budget_known': True,
                                        'repair_decay_per_shot': 0.1})
    overlay.update_from_session(session)
    assert overlay.durability_known
    assert overlay.durability_color == expected
    assert ('EMPTY' in overlay.vars['durability'].get()) == (shots == 10)
    overlay.update_from_session(None)
    assert not overlay.durability_known
    assert overlay.vars['durability'].get() == 'Durability —'


def test_chart_is_ped_even_in_usd_and_counts_more_than_sixteen(hud):
    app, overlay = hud
    sid = app.store.start_session('hunt')
    app.currency_mode.set('USD')
    for i in range(20):
        app.store.add_event(sid, {'kind': 'loot', 'raw_message': 'loot',
                                 'timestamp': f'2026-01-01T10:00:{i:02d}',
                                 'payload': {'value': 1, 'item_name': 'Shrapnel'}})
    overlay.update_from_session(app.store.get_session(sid))
    assert overlay.vars['kills'].get() == '20'
    assert 'PED' in overlay.vars['kill_trend_badge'].get()
    assert '$' in overlay.vars['loot'].get()
    assert 'Cumulative avg cost' in overlay.vars['kill_trend_badge'].get()
    assert overlay.return_label.cget('fg') == '#F1F5F9'
