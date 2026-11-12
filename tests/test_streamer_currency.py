"""Displayed chart money converts once; stored PED and receipt membership do not."""
from copy import deepcopy
import json
import tkinter as tk
from tkinter import ttk

import pytest

from ped_hunter import app as appmod
from ped_hunter.activity_trend import ActivityPoints
from ped_hunter.currency import format_chart_money
from ped_hunter.storage import Store
from test_event_exclusion import book, originals


@pytest.fixture
def currency_hud(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic-chat.log'
    path.write_text('')
    monkeypatch.setattr(appmod, '_default_chat_log_path', lambda: path)
    store = Store(tmp_path / 'streamer-currency.sqlite3')
    sid = store.start_session('hunt')
    key, group = None, None
    for i in range(16):
        anchor = book(store, sid, 'combat', i * 3, damage=1, shot_cost=10, loadout='Synthetic 10 PED input')
        receipt = book(store, sid, 'loot', i * 3 + 1, value=500 if i == 5 else 10, item_name='Synthetic receipt')
        if i == 5:
            group, key = anchor, receipt
    book(store, sid, 'combat', 50, healed=10, healing_action='self', healing_cost=.02,
         healing_cost_status='selected_tool_estimate')
    store.save_chart_trails(sid, {group: [(10, 10, 0), (10, 10, 500)]})
    app = appmod.PedHunterApp(store=store)
    app.title('PED Hunter isolated Streamer chart currency test')
    app.reduced_motion.set(True)
    app.session_id, app.running = sid, True
    app._refresh_all()
    app._open_streamer_window()
    app.update()
    try:
        yield app, app.streamer_window, sid, group, key
    finally:
        app.animator.cancel_all()
        app.destroy()


def canvas_texts(canvas, tag):
    return [canvas.itemcget(item, 'text') for item in canvas.find_withtag(tag)]


@pytest.mark.parametrize('path', ['variable', 'selector'])
def test_chart_axes_local_ranges_details_and_raw_fans_follow_repeated_currency_changes(currency_hud, path):
    app, hud, sid, group, key = currency_hud
    picker = next(widget for section in app.root_frame.winfo_children() for widget in section.winfo_children()
                  if isinstance(widget, ttk.Combobox) and str(widget.cget('textvariable')) == str(app.currency_mode))
    before = originals(app.store)
    points, samples = deepcopy(hud.kill_trend_points), deepcopy(hud.kill_trail_histories)
    with app.store.connect() as conn:
        persisted = [tuple(row) for row in conn.execute('SELECT * FROM chart_trails ORDER BY event_id')]
    for mode in ['PED', 'USD', 'PED', 'USD', 'PED']:
        if path == 'variable':
            app.currency_mode.set(mode)
        else:
            picker.current(0 if mode == 'USD' else 1)
            picker.event_generate('<<ComboboxSelected>>')
        app.update_idletasks()
        ordinary = canvas_texts(hud.kill_chart, 'axis:ordinary')
        local = canvas_texts(hud.kill_chart, 'axis:local')
        if mode == 'USD':
            assert set(ordinary) == {'+$0.00', '+$0.50', '+$1.00'}
            assert '$50.00' in local
            assert 'Cost $1.00' in hud.vars['kill_trend_badge'].get()
            assert 'Booked avg $1.00' in hud.vars['kill_trend_badge'].get()
        else:
            assert set(ordinary) == {'+0.00 PED', '+5.00 PED', '+10.00 PED'}
            assert '500.00 PED' in local
            assert 'Cost 10.00 PED' in hud.vars['kill_trend_badge'].get()
            assert 'Booked avg 10.00 PED' in hud.vars['kill_trend_badge'].get()
        assert mode in hud.chart_note.cget('text')
        assert hud.kill_trend_points == points
        assert hud.kill_trail_histories == samples
        assert hud._chart_scales.events[5].high == 500
        assert sum(point['cost'] or 0 for point in hud.kill_trend_points) == 160  # Healing stays run-only.
        assert originals(app.store) == before
        with app.store.connect() as conn:
            assert [tuple(row) for row in conn.execute('SELECT * FROM chart_trails ORDER BY event_id')] == persisted
    assert app.store.get_session(sid).hunting_cost == pytest.approx(160.02)


def test_exclusion_restore_resets_same_run_chart_and_currency_does_not_reintroduce_spike(currency_hud):
    app, hud, sid, group, key = currency_hud
    app.events_tab.master.select(app.events_tab)
    app.update()
    before = originals(app.store)
    app.store.set_loot_excluded(sid, key, True)
    app._history_accounting_changed(sid)
    for mode in ('USD', 'PED', 'USD'):
        app.currency_mode.set(mode)
        app.update_idletasks()
        assert app.store.get_session(sid).loot_value == 150
        assert app.store.get_session(sid).hunting_cost == pytest.approx(160.02)
        assert max(point['loot'] for point in hud.kill_trend_points) == 10
        assert all(sample[2] <= 10 for sample in hud.kill_trail_histories.get(group, ()))
        assert not any('500.00' in value or '$50.00' in value for value in canvas_texts(hud.kill_chart, 'money-axis'))
        assert mode in hud.chart_note.cget('text')
        canvas = app.loot_chart_canvas
        main_texts = {canvas.itemcget(item, 'text') for item in canvas.find_all()
                      if canvas.type(item) == 'text'}
        expected_ticks = {'$0.00', '$0.50', '$1.00'} if mode == 'USD' else {'0.00 PED', '5.00 PED', '10.00 PED'}
        assert expected_ticks.issubset(main_texts)
        assert not any('500.00' in text or '$50.00' in text for text in main_texts)
        assert originals(app.store) == before
    app.store.set_loot_excluded(sid, key, False)
    app._history_accounting_changed(sid)
    app.currency_mode.set('PED')
    app.update_idletasks()
    assert app.store.get_session(sid).loot_value == 650
    assert '500.00 PED' in canvas_texts(hud.kill_chart, 'axis:local')
    assert hud.kill_trail_histories[group][-1][2] == 500
    app.currency_mode.set('USD')
    assert '$50.00' in canvas_texts(hud.kill_chart, 'axis:local')
    assert hud.kill_trend_points[5]['loot'] == 500
    assert originals(app.store) == before


def test_craft_input_footer_hover_and_chart_details_convert_with_mode(tmp_path, monkeypatch):
    chat = tmp_path / 'chat.log'
    chat.write_text('')
    monkeypatch.setattr(appmod, '_default_chat_log_path', lambda: chat)
    store = Store(tmp_path / 'craft-currency.sqlite3')
    sid = store.start_session('hunt')
    book(store, sid, 'manufacturing_offset', 0, blueprint='Synthetic blueprint', attempts_total=2,
         cost_per_attempt=10, total_cost=20)
    book(store, sid, 'loot', 1, value=20, item_name='Synthetic crafted receipt')
    app = appmod.PedHunterApp(store=store)
    app.title('PED Hunter isolated crafting chart currency test')
    try:
        app.reduced_motion.set(True)
        app.session_id, app.running = sid, True
        app._refresh_all()
        app._open_streamer_window()
        app.update()
        hud = app.streamer_window
        rows = originals(store)
        for mode, unit_price, total in [('PED', '10.00 PED', '20.00 PED'), ('USD', '$1.00', '$2.00'), ('PED', '10.00 PED', '20.00 PED')]:
            app.currency_mode.set(mode)
            assert unit_price in hud.vars['input'].get()
            assert total in hud._footer_detail
            assert unit_price in hud.vars['kill_trend_badge'].get()
            hud._show_loadout()
            app.update_idletasks()
            tooltip = '\n'.join(str(widget.cget('text')) for widget in hud._loadout_tip.winfo_children() if isinstance(widget, tk.Label))
            assert unit_price in tooltip and total in tooltip
            assert hud.kill_trend_points[0]['cost'] == 10
            assert hud.kill_trend_points[0]['loot'] == 20
            assert originals(store) == rows
            other = 'USD' if mode == 'PED' else 'PED'
            app.currency_mode.set(other)
            assert hud._loadout_tip is None  # No old-unit monetary hover remains visible.
    finally:
        app.destroy()


@pytest.mark.parametrize('shared', [False, True])
def test_unresolved_return_and_pending_input_details_use_selected_currency(shared):
    points = ActivityPoints()
    points.append({'kill': 1, 'label': 'K1', 'kind': 'hunt', 'cost': None, 'avg_cost': None,
                   'profit': None, 'loot': 10, 'cost_status': 'shared_ambiguous' if shared else None})
    points.attribution_pending = 10
    before = deepcopy(points)
    for mode, expected in [('USD', '$1.00'), ('PED', '10.00 PED')]:
        text = appmod._streamer_trend_detail(points, mode)
        assert f'Return {expected}' in text
        if shared:
            assert f'Shared/unassigned {expected}' in text
        assert points == before


@pytest.mark.parametrize('value,mode,expected', [(10, 'USD', '$1.00'), (10, 'PED', '10.00 PED'),
    (.0005, 'USD', '$5e-05'), (.0005, 'PED', '0.0005 PED'), (-10, 'USD', '-$1.00')])
def test_compact_chart_money_preserves_conversion_sign_and_small_ranges(value, mode, expected):
    assert format_chart_money(value, mode, signed=value < 0) == expected
