"""A saved crafting input must price real received-item groups, not just fictional craft notices."""
from unittest.mock import Mock

import pytest

from ped_hunter.app import PedHunterApp, _kill_trend_points
from ped_hunter.storage import Store


BLUEPRINT = 'Simple 1 Conductors Blueprint'


def plan(store, sid, attempts=16, price=0.7, name=BLUEPRINT):
    store.add_event(sid, {'kind': 'manufacturing_offset', 'raw_message': 'Manual crafting setup',
                         'payload': {'offset_id': f'{attempts}-{price}-{name}', 'blueprint': name,
                                     'attempts_total': attempts, 'cost_per_attempt': price,
                                     'total_cost': price * attempts}})


def output(store, sid, second, item, value):
    store.add_event(sid, {'kind': 'loot', 'timestamp': f'2026-01-01T10:00:{second:02d}',
                         'raw_message': f'{second} {item} {value}',
                         'payload': {'item_name': item, 'value': value}})


def test_real_received_outputs_get_the_configured_cost_not_zero(tmp_path):
    store = Store(tmp_path / 'craft.sqlite3')
    sid = store.start_session('hunt')  # The user can craft inside an existing hunt.
    plan(store, sid, attempts=1)
    output(store, sid, 1, 'Simple 1 Conductors', 0.6)
    output(store, sid, 1, 'Animal Oil Residue', 0.24)
    output(store, sid, 1, 'Shrapnel', 0.0018)
    before = store.get_session(sid)
    points = _kill_trend_points(store, sid)
    assert len(points) == 1
    assert points[0]['cost'] == pytest.approx(0.7)
    assert points[0]['loot'] == pytest.approx(0.8418)
    assert points[0]['profit'] == pytest.approx(0.1418)
    assert points[0]['label'] == 'C1'
    assert store.get_session(sid) == before  # Projection must not add/rebill old events.


def test_partial_material_returns_are_one_priced_craft_not_four_free_kills(tmp_path):
    store = Store(tmp_path / 'partial.sqlite3')
    sid = store.start_session('hunt')
    plan(store, sid)
    for item, value in [('Animal Muscle Oil', 0.3), ('Animal Eye Oil', 0.1),
                        ('Animal Oil Residue', 0.23), ('Shrapnel', 0.0039)]:
        output(store, sid, 1, item, value)
    points = _kill_trend_points(store, sid)
    assert len(points) == 1
    assert points[0]['cost'] == pytest.approx(0.7)
    assert points[0]['profit'] == pytest.approx(-0.0661)


@pytest.fixture
def hud(tmp_path):
    app = PedHunterApp(store=Store(tmp_path / 'gui.sqlite3'))
    app.reduced_motion.set(True)
    sid = app.store.start_session('hunt')
    app.session_id, app.running = sid, True
    app._open_streamer_window()
    app.update()
    yield app, app.streamer_window, sid
    app.destroy()


def arm_from_real_controls(app, attempts=16):
    app.crafting_blueprint.set(BLUEPRINT)
    app.crafting_attempts.set(str(attempts))
    app._add_crafting_cost_to_session()
    app.update()


def real_lines(second=1):
    return [f'2026-01-01 10:00:{second:02d} [System] [] You received {item} x ({qty}) Value: {value} PED'
            for item, qty, value in [('Simple 1 Conductors', 2, '0.6'),
                                     ('Animal Oil Residue', 24, '0.24'), ('Shrapnel', 18, '0.0018')]]


def test_manual_controls_show_inputs_before_any_result_and_book_cost_once(hud):
    app, window, sid = hud
    arm_from_real_controls(app)
    assert window.input_label.winfo_ismapped()
    assert '0.70 PED' in window.vars['input'].get()
    assert '0/16' in window.vars['input'].get()
    assert BLUEPRINT in window.vars['loadout'].get()
    assert app.store.get_session(sid).hunting_cost == pytest.approx(11.2)
    assert window.cost_title.cget('text') == 'Booked cost'
    assert window.count_title.cget('text') == 'Groups*'


def test_real_ingestion_prices_full_craft_group_and_keeps_session_total(hud):
    app, window, sid = hud
    arm_from_real_controls(app, attempts=1)
    parsed, previous = app._process_lines(real_lines()[:1], sid, None)
    window.update_from_session(app.store.get_session(sid))
    assert window.kill_trend_points[0]['cost'] == pytest.approx(0.7)
    parsed2, _ = app._process_lines(real_lines()[1:], sid, previous)
    assert parsed + parsed2 == 3
    window.update_from_session(app.store.get_session(sid))
    app.update()
    assert len(window.kill_trend_points) == 1
    assert window.kill_trend_points[0]['profit'] == pytest.approx(0.1418)
    assert 'C1' in window.vars['kill_trend_badge'].get()
    assert '1/1' in window.vars['input'].get()
    before = app.store.get_session(sid)
    for currency, cost in [('PED', '0.70 PED'), ('USD', '$0.07'), ('PED', '0.70 PED')]:
        app.currency_mode.set(currency)
        window.update_from_session(app.store.get_session(sid))
        app.update()
        assert window.vars['cost'].get() == cost
        assert window.kill_trend_points[0]['cost'] == pytest.approx(0.7)
    assert app.store.get_session(sid) == before


def test_craft_progress_updates_stay_in_the_existing_fixed_layout(hud):
    app, window, sid = hud
    arm_from_real_controls(app, attempts=100)
    widgets = (window.hero, window.stats, window.activity_strip, window.kill_chart, window.footer)
    def shape():
        return tuple((w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()) for w in widgets)
    before = shape()
    window._layout_values = Mock(wraps=window._layout_values)
    for second in (1, 5, 9, 13):
        app._process_lines(real_lines(second), sid, None)
        window.update_from_session(app.store.get_session(sid))
        app.update()
        assert shape() == before
    window._layout_values.assert_not_called()


def test_unpriced_extra_craft_groups_do_not_show_gross_return_as_profit(hud):
    app, window, sid = hud
    arm_from_real_controls(app, attempts=1)
    app._process_lines(real_lines(1) + real_lines(5), sid, None)
    window.update_from_session(app.store.get_session(sid))
    app.update()
    assert window.kill_trend_points[-1]['cost'] is None
    assert window.kill_trend_points[-1]['profit'] is None
    assert window.vars['net_big'].get() == '—'
    assert window.vars['return'].get() == '—'
    assert 'unknown' in window.vars['kill_trend_badge'].get().lower()


def test_new_priced_setup_does_not_hide_booked_totals_for_an_old_attribution_gap(hud):
    app, window, sid = hud
    arm_from_real_controls(app, attempts=1)
    app._process_lines(real_lines(1) + real_lines(5), sid, None)
    arm_from_real_controls(app, attempts=1)
    app._process_lines(real_lines(9), sid, None)
    window.update_from_session(app.store.get_session(sid))
    app.update()
    assert window.vars['net_big'].get() != '—'
    assert 'BOOKED' in window.net_caption.cget('text')
    assert window.cost_title.cget('text') == 'Booked cost'
    assert '1 historical craft groups' in window._footer_detail


def test_return_to_hunting_restores_weapon_context_without_hiding_last_inputs(hud):
    app, window, sid = hud
    arm_from_real_controls(app)
    app._process_lines(real_lines(1), sid, None)
    app.store.add_event(sid, {'kind': 'combat', 'raw_message': 'test hunting shot',
                             'payload': {'damage': 4, 'shot_cost': 0.3}})
    output(app.store, sid, 5, 'Shrapnel', 0.4)
    window.update_from_session(app.store.get_session(sid))
    app.update()
    assert window.kill_trend_points[-1]['kind'] == 'hunt'
    assert window.kill_trend_points[-1]['cost'] == pytest.approx(0.3)
    assert not window.vars['loadout'].get().startswith('Crafting:')
    assert window.vars['damage'].get() == 'Dmg 4.0'
    assert window.vars['input'].get().startswith('Last TT input 0.70 PED')
    assert window.input_label.winfo_ismapped()
