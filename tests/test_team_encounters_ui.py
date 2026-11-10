"""Real native HUD: pending means no green zero; whole encounter stays visible."""
import json
from pathlib import Path

import pytest

import ped_hunter.app as app_module
from ped_hunter.app import PedHunterApp
from ped_hunter.storage import Store


@pytest.fixture
def owned_hud(tmp_path, monkeypatch):
    log = tmp_path / 'owned-chat.log'
    log.write_bytes(b'')
    monkeypatch.setattr(app_module, '_default_chat_log_path', lambda: log)
    app = PedHunterApp(store=Store(tmp_path / 'owned-ui.sqlite3'))
    app.title('PED015 ISOLATED native regression')
    try:
        app.reduced_motion.set(True)
        app.currency_mode.set('PED')
        sid = app.store.start_session('hunt')
        app.session_id, app.running = sid, True
        app._open_streamer_window()
        app.update()
        yield app, app.streamer_window, sid
    finally:
        app.destroy()


def add(app, sid, kind, second=0, **payload):
    app.store.add_event(sid, {'kind': kind, 'timestamp': f'2026-01-01T10:00:{second:02d}',
                              'raw_message': f'owned native {kind} {second} {payload}', 'payload': payload})


def refresh(app, hud, sid):
    hud.update_from_session(app.store.get_session(sid))
    app.update_idletasks()
    hud._chart_frame(1)


def test_pending_team_notice_draws_cost_but_no_false_zero_return(owned_hud):
    app, hud, sid = owned_hud
    add(app, sid, 'combat', shot_cost=.1, ammo_cost=.1, repair_decay=0)
    refresh(app, hud, sid)
    add(app, sid, 'chat', 1, channel='Team', speaker='', message="This creature didn't carry any loot")
    refresh(app, hud, sid)
    assert len(hud.kill_trend_points) == 1
    assert hud.kill_trend_points[0]['loot'] is None
    assert hud.kill_trend_points[0]['profit'] is None
    assert hud.kill_chart.find_withtag('cost')
    assert not hud.kill_chart.find_withtag('loot')
    assert hud.vars['net_big'].get() == hud.vars['return'].get() == '—'
    assert not hud._trend_error and not hud._attribution_error and not hud._trail_error
    assert app.store.get_session(sid).hunting_cost == pytest.approx(.1)
    assert app.store.get_session(sid).loot_value == 0


@pytest.mark.parametrize('width,height', [(720, 660), (520, 480)])
def test_captured_whole_encounter_is_retained_and_legible_at_supported_sizes(owned_hud, width, height):
    app, hud, sid = owned_hud
    fixture = json.loads((Path(__file__).parent / 'fixtures/team_encounter_sacred_idol.json').read_text())
    for event_id, timestamp, kind, payload in fixture['events']:
        app.store.add_event(sid, {'kind': kind, 'timestamp': timestamp,
                                  'raw_message': f'captured semantic event {event_id}', 'payload': payload})
    hud.geometry(f'{width}x{height}+40+40')
    app.update()
    refresh(app, hud, sid)
    assert hud.winfo_width() == width and hud.winfo_height() == height
    assert len(hud.kill_trend_points) == 44
    assert len(hud._chart_scales.event_ids) == 44
    assert len(hud.kill_chart.find_withtag('loot')) == 44
    for item in hud.kill_chart.find_withtag('loot'):
        left, top, right, bottom = hud.kill_chart.coords(item)
        assert 0 <= left < right <= hud.kill_chart.winfo_width()
        assert -3 <= top < bottom <= hud.kill_chart.winfo_height()
    assert not hud._trend_error and not hud._attribution_error and not hud._trail_error
    assert app.store.get_session(sid).hunting_cost == pytest.approx(22.6188)
    assert app.store.get_session(sid).loot_value == pytest.approx(8.45)
    # One newly observed endpoint is allowed; no prior development is invented.
    assert all(len(history) <= 1 for history in hud.kill_trail_histories.values())
    assert not hud.kill_chart.find_withtag('trail')


def test_missing_prices_keep_native_cost_coverage_unknown_and_receipts_visible_once(owned_hud):
    app, hud, sid = owned_hud
    add(app, sid, 'combat', damage=8)
    add(app, sid, 'chat', 1, channel='Team', speaker='', message="This creature didn't carry any loot")
    add(app, sid, 'chat', 2, channel='Force', speaker='', message='Teammate opened a Sacred Idol')
    add(app, sid, 'loot', 2, item_name='Shrapnel', quantity=100, value=2, is_personal=True)
    refresh(app, hud, sid)
    assert [p['cost'] for p in hud.kill_trend_points] == [None, None]
    assert [p['loot'] for p in hud.kill_trend_points] == [None, 2]
    assert len(hud.kill_chart.find_withtag('loot')) == 1
    assert hud.vars['net_big'].get() == '—'
    assert not hud._trend_error and not hud._attribution_error and not hud._trail_error
    assert app.store.get_session(sid).loot_value == 2


def test_live_team_cost_fan_survives_marker_receipts_and_resize_without_invented_returns(owned_hud):
    app, hud, sid = owned_hud
    add(app, sid, 'combat', shot_cost=.1, ammo_cost=.1, repair_decay=0)
    refresh(app, hud, sid)
    add(app, sid, 'combat', 1, shot_cost=.1, ammo_cost=.1, repair_decay=0)
    refresh(app, hud, sid)
    add(app, sid, 'chat', 2, channel='Team', speaker='', message="This creature didn't carry any loot")
    refresh(app, hud, sid)
    first = hud.kill_trend_points[0]['event_id']
    assert {sample[0] for sample in hud.kill_trail_histories[first]} == {.1, .2}
    assert all(sample[2] is None for sample in hud.kill_trail_histories[first])
    assert hud.kill_chart.find_withtag('trail:cost')
    assert not hud.kill_chart.find_withtag('loot')
    add(app, sid, 'combat', 3, shot_cost=.3, ammo_cost=.3, repair_decay=0)
    refresh(app, hud, sid)
    add(app, sid, 'chat', 4, channel='Team', speaker='', message="This creature didn't carry any loot")
    add(app, sid, 'chat', 5, channel='Force', speaker='', message='Teammate opened a Sacred Idol')
    add(app, sid, 'loot', 5, item_name='Shrapnel', quantity=100, value=1.2, is_personal=True)
    refresh(app, hud, sid)
    assert [p['loot'] for p in hud.kill_trend_points] == pytest.approx([.48, .72])
    assert hud.kill_trail_histories[first][-1][2] == pytest.approx(.48)
    assert hud.kill_chart.find_withtag('trail:cost')
    assert all(point['profit'] is None for point in hud.kill_trend_points)
    assert hud.vars['net_big'].get() == '—'
    add(app, sid, 'equipment', 7, item_damaged=True)
    refresh(app, hud, sid)
    assert [point['profit'] for point in hud.kill_trend_points] == pytest.approx([.28, .42])
    before = dict(hud.kill_trail_histories)
    hud.geometry('520x480+40+40')
    app.update()
    refresh(app, hud, sid)
    assert hud.winfo_width() == 520 and hud.winfo_height() == 480
    assert hud.kill_trail_histories == before
    assert hud.kill_chart.find_withtag('trail:cost')
    assert app.store.get_session(sid).hunting_cost == pytest.approx(.5)
    assert app.store.get_session(sid).loot_value == pytest.approx(1.2)


def test_booked_average_counts_team_markers_without_inflating_following_hunts(owned_hud):
    app, hud, sid = owned_hud
    for index in range(3):
        add(app, sid, 'combat', index * 3, shot_cost=.1, ammo_cost=.1, repair_decay=0)
        add(app, sid, 'loot', index * 3 + 1, item_name='Shrapnel', quantity=1200, value=.12, is_personal=True)
    add(app, sid, 'combat', 12, shot_cost=.1, ammo_cost=.1, repair_decay=0)
    add(app, sid, 'chat', 13, channel='Team', speaker='', message="This creature didn't carry any loot")
    add(app, sid, 'combat', 14, shot_cost=.2, ammo_cost=.2, repair_decay=0)
    add(app, sid, 'chat', 15, channel='Team', speaker='', message="This creature didn't carry any loot")
    add(app, sid, 'chat', 16, channel='Force', speaker='', message='Teammate opened a Sacred Idol')
    add(app, sid, 'loot', 16, item_name='Shrapnel', quantity=6000, value=.6, is_personal=True)
    add(app, sid, 'combat', 20, shot_cost=.1, ammo_cost=.1, repair_decay=0)
    add(app, sid, 'loot', 21, item_name='Shrapnel', quantity=1500, value=.15, is_personal=True)
    refresh(app, hud, sid)
    assert [point['kind'] for point in hud.kill_trend_points] == ['hunt'] * 3 + ['encounter'] * 2 + ['hunt']
    assert [point['avg_cost'] for point in hud.kill_trend_points] == pytest.approx([.1, .1, .1, .1, .12, .7 / 6])
    assert len(hud.kill_chart.find_withtag('series:avg_cost')) == 1
    assert len(hud.kill_chart.find_withtag('series:cost')) == 1
    assert app.store.get_session(sid).hunting_cost == pytest.approx(.7)
    assert app.store.get_session(sid).loot_value == pytest.approx(1.11)


def test_notice_only_native_view_has_no_invented_cost_return_or_fan(owned_hud):
    app, hud, sid = owned_hud
    add(app, sid, 'chat', 1, channel='Team', speaker='', message="This creature didn't carry any loot")
    add(app, sid, 'chat', 2, channel='Team', speaker='', message="This creature didn't carry any loot")
    refresh(app, hud, sid)
    assert [point['cost'] for point in hud.kill_trend_points] == [None, None]
    assert len(hud._chart_scales.event_ids) == 2
    assert not hud.kill_chart.find_withtag('cost')
    assert not hud.kill_chart.find_withtag('loot')
    assert not hud.kill_chart.find_withtag('trail')
    assert hud.vars['net_big'].get() == hud.vars['return'].get() == '—'
    assert not hud._trend_error and not hud._encounter_error and not hud._trail_error
    assert app.store.get_session(sid).hunting_cost == 0
    add(app, sid, 'chat', 3, channel='Force', speaker='', message='Teammate opened a Sacred Idol')
    add(app, sid, 'loot', 3, item_name='Shrapnel', quantity=10000, value=1, is_personal=True)
    add(app, sid, 'equipment', 5, item_damaged=True)
    refresh(app, hud, sid)
    assert [point['cost'] for point in hud.kill_trend_points] == [None, None, None]
    assert [point['loot'] for point in hud.kill_trend_points] == [None, None, 1]
    assert all(point['profit'] is None for point in hud.kill_trend_points)
    assert len(hud.kill_chart.find_withtag('loot')) == 1
    assert not hud.kill_chart.find_withtag('cost')
    assert app.store.get_session(sid).hunting_cost == 0
    assert app.store.get_session(sid).loot_value == 1


def test_unpriced_container_receipt_keeps_native_profit_pending_not_zero(owned_hud):
    app, hud, sid = owned_hud
    add(app, sid, 'combat', shot_cost=.1, ammo_cost=.1, repair_decay=0)
    add(app, sid, 'chat', 1, channel='Team', speaker='', message="This creature didn't carry any loot")
    add(app, sid, 'chat', 2, channel='Force', speaker='', message='Teammate opened a Sacred Idol')
    add(app, sid, 'loot', 2, item_name='Unknown fixture item', quantity=1, is_personal=True)
    refresh(app, hud, sid)
    assert hud.kill_trend_points[0]['loot'] is None
    assert not hud.kill_chart.find_withtag('loot')
    assert hud.vars['net_big'].get() == hud.vars['return'].get() == '—'
    assert app.store.get_session(sid).hunting_cost == pytest.approx(.1)
    assert app.store.get_session(sid).loot_value == 0
