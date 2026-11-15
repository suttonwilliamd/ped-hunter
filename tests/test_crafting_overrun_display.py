"""Saved input + actual-return projection; no extra billing or silent-failure fiction."""
from copy import deepcopy
import json

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder, build_activity_points
from ped_hunter.crafting_display import crafting_cost_display
from ped_hunter.currency import format_money


def row(event_id, kind, second=None, **payload):
    from datetime import datetime, timedelta
    return dict(id=event_id, kind=kind,
                timestamp=None if second is None else (datetime(2026, 1, 1) + timedelta(seconds=second)).isoformat(),
                payload=json.dumps(payload))


def facts(price=.91, product='Widget', explicit_excess=None, boundary=False):
    rows = [row(1, 'manufacturing_offset', blueprint='Widget Blueprint', attempts_total=15,
                cost_per_attempt=price, total_cost=None if price is None else 15 * price)]
    for i in range(17):
        if i == 15 and boundary:
            rows.append(row(len(rows) + 1, 'weapon', 2 * i, name='Changed setup'))
        if i >= 15 and explicit_excess is not None:
            rows.append(row(len(rows) + 1, 'craft', 2 * i, result='success', input_cost=explicit_excess))
        rows.append(row(len(rows) + 1, 'loot', 2 * i, item_name=product, value=20 / 17))
    return rows


def test_15_17_same_recipe_best_input_estimate_is_not_an_additional_batch_charge():
    rows = facts()
    points = build_activity_points(rows)
    before = deepcopy(points)
    view = crafting_cost_display(13.65, 20, points)
    assert points.manufacturing.observed == 17
    assert points.manufacturing.booked_cost == pytest.approx(13.65)
    assert points.manufacturing.returns == pytest.approx(20)
    assert view.cost == pytest.approx(15.47)
    assert view.excess_estimate == pytest.approx(1.82)
    assert view.net == pytest.approx(4.53)
    assert view.return_pct == pytest.approx(129.2824822236587)
    assert view.marker == '≈' and view.unknown_groups == 0
    assert points[-1]['cost'] is None  # No rebooking/retrospective attribution.
    assert points == before
    for _ in range(3):
        assert crafting_cost_display(13.65, 20, build_activity_points(rows)) == view
    assert format_money(view.cost, 'PED') == '15.47 PED'
    assert format_money(view.cost, 'USD') == '$1.55'


@pytest.mark.parametrize('product,boundary', [('Other product', False), ('Widget', True)])
def test_unknown_or_changed_recipe_keeps_known_cost_and_partial_net(product, boundary):
    points = build_activity_points(facts(product=product, boundary=boundary))
    view = crafting_cost_display(13.65, 20, points)
    assert view.cost == pytest.approx(13.65)
    assert view.net == pytest.approx(6.35)
    assert view.unknown_groups == 2 and view.marker == '*'
    assert view.return_pct is None  # Never pass this off as the overall return.
    assert view.excess_estimate == 0


def test_no_price_keeps_actual_returns_and_progress_not_free_profit():
    points = build_activity_points(facts(price=None))
    view = crafting_cost_display(0, 20, points)
    assert points.manufacturing.observed == 17 and points.manufacturing.attempts == 15
    assert points.manufacturing.returns == pytest.approx(20)
    assert view.cost is view.net is view.return_pct is None
    assert view.marker == '*' and view.unknown_groups == 17


def test_different_explicit_input_snapshots_do_not_reprice_old_booking():
    points = build_activity_points(facts(explicit_excess=.95))
    view = crafting_cost_display(13.65, 20, points)
    assert view.cost == pytest.approx(15.55)
    assert view.excess_estimate == pytest.approx(1.9)
    assert points[0]['cost'] == .91
    assert points.manufacturing.booked_cost == pytest.approx(13.65)


def test_incremental_receipt_estimate_needs_distinctive_output_and_does_not_mutate_old_snapshots():
    rows = facts()
    builder = ActivityTrendBuilder()
    builder.extend(rows[:-1])
    previous = builder.snapshot()
    previous_copy = deepcopy(previous)
    builder.extend([row(len(rows), 'loot', 32, item_name='Residue', value=.1)])
    unsupported = builder.snapshot()
    assert unsupported[-1]['excess_input_estimate'] is None
    builder.extend([row(len(rows) + 1, 'loot', 32, item_name='Widget', value=.2)])
    supported = builder.snapshot()
    assert supported[-1]['excess_input_estimate'] == .91
    assert previous == previous_copy
    assert unsupported[-1]['excess_input_estimate'] is None


def test_observed_groups_are_not_evidence_of_unused_planned_inputs_or_total_clicks():
    rows = facts()[:7]  # Fewer observed returns never refunds the original plan.
    points = build_activity_points(rows)
    view = crafting_cost_display(13.65, points.manufacturing.returns, points)
    assert points.manufacturing.observed < 15
    assert view.cost == pytest.approx(13.65)
    assert view.excess_estimate == 0


def test_mixed_hunt_and_old_batches_conserve_supported_excess_without_ignoring_old_gaps():
    points = build_activity_points(facts(product='Other product'))
    view = crafting_cost_display(13.95, 20.4, points)
    assert view.cost == pytest.approx(13.95) and view.net == pytest.approx(6.45)
    assert view.marker == '*'
    points.extend([dict(kind='craft', cost=None, excess_input_estimate=.95),
                   dict(kind='hunt', cost=.3, excess_input_estimate=999)])
    view = crafting_cost_display(14.90, 20.4, points)
    assert view.cost == pytest.approx(15.85)
    assert view.marker == '*' and view.unknown_groups == 2


def binding_hud(store, sid, monkeypatch):
    from types import MethodType, SimpleNamespace
    from unittest.mock import Mock
    from ped_hunter import app as module
    from ped_hunter.hunt_attribution import HuntAttribution
    from test_streamer_readability import BindingWidget, BindingValue, Font

    for name in ('Frame', 'Button', 'Label', 'Canvas'):
        monkeypatch.setattr(module.tk, name, BindingWidget)
    monkeypatch.setattr(module.tk, 'Toplevel', Mock(side_effect=AssertionError('No automatic popup allowed')))
    hud = BindingWidget()
    hud.ui_scale = hud._content_scale = 1
    hud.tk = SimpleNamespace(call=lambda *_: 'win32', splitlist=lambda text: text.split())
    hud.app = SimpleNamespace(store=store, currency_mode=BindingValue('PED'), session_id=sid,
                              running=True, reduced_motion=BindingValue(True), _activate_saved_loadout=Mock())
    hud.vars = {key: BindingValue() for key in (
        'state', 'net_big', 'return', 'loot', 'cost', 'kills', 'input', 'damage',
        'loadout', 'durability', 'kill_trend', 'kill_trend_badge')}

    class CopyFont(Font):
        def copy(self):
            return CopyFont(self.size)

    def font(size=9, bold=False):
        hud._fonts.setdefault((size, bold), (CopyFont(size), size))
        return hud._fonts[size, bold][0]

    hud._font = font
    for name in ('refresh_setup_slots', '_fit_setup_labels', '_activate_setup_slot',
                 '_show_setup_slot', '_show_loadout', '_hide_loadout', '_show_context_details'):
        setattr(hud, name, MethodType(getattr(module.StreamerWindow, name), hud))
    for name in ('_build_resize_edges', '_start_resize', '_resize', '_stop_resize',
                 '_on_overlay_configure', '_draw_kill_trend_chart', '_draw_durability_bar',
                 '_fit_hero_captions', '_schedule_fit_to_contents'):
        setattr(hud, name, Mock())
    module.StreamerWindow._build(hud)
    hud._hunt_attribution = HuntAttribution(store)
    hud._trail_recorder = SimpleNamespace(update=lambda *_a, **_kw: {})
    hud.animator = SimpleNamespace(number=lambda _key, value, render, _animate: render(value), cancel=Mock())
    hud._feedback_context, hud.demo = None, False
    return hud


@pytest.mark.parametrize('price,product,expected_cost,expected_net,expected_rate', [
    (.91, 'Widget', '≈15.47 PED', '≈+4.53 PED', '≈129.28%'),
    (.91, 'Other product', '13.65 PED*', '+6.35 PED*', '—'),
    (None, 'Widget', '—', '—', '—'),
])
def test_actual_build_and_update_show_cost_returns_and_progress_without_automatic_popups(
        tmp_path, monkeypatch, price, product, expected_cost, expected_net, expected_rate):
    from ped_hunter import app as module
    from ped_hunter.storage import Store
    from test_event_exclusion import originals

    store = Store(tmp_path / 'overrun.sqlite3')
    sid = store.start_session('hunt')
    for event in facts(price=price, product=product):
        store.add_event(sid, dict(kind=event['kind'], timestamp=event['timestamp'],
                                 raw_message=f"Synthetic receipt {event['id']}", payload=json.loads(event['payload'])))
    saved, summary = originals(store), store.get_session(sid)
    hud = binding_hud(store, sid, monkeypatch)
    for mode in ('PED', 'USD', 'PED'):
        hud.app.currency_mode.set(mode)
        module.StreamerWindow.update_from_session(hud, summary)
        assert hud.cost_title.cget('text') == 'Cost'
        assert hud.vars['loot'].get() == ('20.00 PED' if mode == 'PED' else '$2.00')
        assert '17/15 observed' in hud.vars['input'].get()
        assert hud.vars['return'].get() == expected_rate
        if mode == 'PED':
            assert hud.vars['cost'].get() == expected_cost
            assert hud.vars['net_big'].get() == expected_net
        else:
            view = hud._cost_display
            def usd(value, signed=False):
                if value is None:
                    return '—'
                return ('≈' if view.marker == '≈' else '') + format_money(value, 'USD', signed=signed) + ('*' if view.marker == '*' else '')
            assert hud.vars['cost'].get() == usd(view.cost)
            assert hud.vars['net_big'].get() == usd(view.net, signed=True)
        for widget in (hud.kill_chart, hud.chart_note, hud.loadout_label, *hud.setup_buttons):
            for event in ('<Enter>', '<FocusIn>'):
                widget.dispatch(event)
            assert hud._loadout_tip is None
        assert originals(store) == saved and store.get_session(sid) == summary


@pytest.mark.parametrize('incremental', [False, True])
def test_clock_rollback_does_not_reset_excess_context_certainty(incremental):
    rows = facts()
    rows[-1]['timestamp'] = '2026-01-01T00:00:29'
    if incremental:
        builder = ActivityTrendBuilder()
        builder.extend(rows[:-1])
        builder.snapshot()
        builder.extend(rows[-1:])
        points = builder.snapshot()
    else:
        points = build_activity_points(rows)
    assert points[-2]['excess_input_estimate'] == .91
    assert points[-1]['excess_input_estimate'] is None
    assert crafting_cost_display(13.65, 20, points).marker == '*'


@pytest.mark.parametrize('kind,payload', [('combat', {'damage': 1, 'shot_cost': .1}),
                                        ('weapon', {'name': 'Other setup'}), ('repair', {})])
def test_later_boundary_keeps_preceding_excess_estimate_but_new_span_is_uncertain(kind, payload):
    rows = facts()
    builder = ActivityTrendBuilder()
    builder.extend(rows)
    before = builder.snapshot()
    preserved = deepcopy(before)
    builder.extend([row(len(rows) + 1, kind, 35, **payload)])
    after = builder.snapshot()
    craft = [p for p in after if p['kind'] == 'craft']
    assert craft[-1]['excess_input_estimate'] == .91
    assert crafting_cost_display(13.65, 20, after).excess_estimate == pytest.approx(1.82)
    assert before == preserved
    if kind != 'combat':
        # The established grouping is unchanged, but a receipt that actually
        # spans an equipment boundary cannot keep claiming same-context input.
        builder.extend([row(len(rows) + 2, 'loot', 33, item_name='Widget', value=.1)])
        assert builder.snapshot()[-1]['excess_input_estimate'] is None


@pytest.mark.parametrize('booked,returns', [(0, 0), (.3, .6)])
def test_missing_price_is_incomplete_even_before_any_receipt(booked, returns):
    points = build_activity_points(facts(price=None)[:1])
    view = crafting_cost_display(booked, returns, points)
    assert not points and points.unpriced_craft_plans == 1
    assert view.marker == '*' and view.incomplete
    assert view.return_pct is None
    assert view.cost == (booked if booked else None)
    assert view.unknown_groups == 0  # Do not invent observations.
    assert 'missing input prices' in '; '.join(view.incomplete_reasons)


@pytest.mark.parametrize('product', ['Widget', 'Other product'])
def test_projection_failure_keeps_actual_return_and_partial_booked_values(tmp_path, monkeypatch, product):
    from ped_hunter import app as module
    from ped_hunter.storage import Store
    store = Store(tmp_path / 'failed-projection.sqlite3')
    sid = store.start_session('hunt')
    for event in facts(product=product):
        store.add_event(sid, dict(kind=event['kind'], timestamp=event['timestamp'],
                                 raw_message=f"Synthetic receipt {event['id']}", payload=json.loads(event['payload'])))
    hud = binding_hud(store, sid, monkeypatch)
    summary = store.get_session(sid)
    module.StreamerWindow.update_from_session(hud, summary)
    hud._kill_trend_key = None
    def broken(*args, **kwargs):
        raise ValueError('Synthetic unreadable projection')
    monkeypatch.setattr(module, '_kill_trend_points', broken)
    module.StreamerWindow.update_from_session(hud, summary)
    assert hud._trend_error and hud.cost_title.cget('text') == 'Cost'
    assert hud.vars['loot'].get() == '20.00 PED'
    assert hud.vars['cost'].get() == '13.65 PED*'
    assert hud.vars['net_big'].get() == '+6.35 PED*'
    assert hud.vars['return'].get() == '—'
    assert 'input projection unavailable' in hud._footer_detail


def test_marker_change_rerenders_same_number_with_real_animator(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ped_hunter import app as module
    from ped_hunter.feedback import Animator
    from ped_hunter.storage import Store
    from test_event_exclusion import book
    store = Store(tmp_path / 'marker.sqlite3')
    sid = store.start_session('hunt')
    book(store, sid, 'combat', 0, damage=1, shot_cost=.3)
    book(store, sid, 'loot', 1, value=.6, item_name='Widget')
    hud = binding_hud(store, sid, monkeypatch)
    hud.app.reduced_motion.set(False)
    hud.animator = Animator(SimpleNamespace(bind=Mock(), after=Mock(), after_cancel=Mock()))
    module.StreamerWindow.update_from_session(hud, store.get_session(sid))
    assert hud.vars['cost'].get() == '0.30 PED'
    book(store, sid, 'manufacturing_offset', 3, blueprint='Unpriced Blueprint',
         attempts_total=15, total_cost=None, cost_per_attempt=None)
    module.StreamerWindow.update_from_session(hud, store.get_session(sid))
    assert hud.vars['cost'].get() == '0.30 PED*'
    assert hud.vars['return'].get() == '—'


@pytest.mark.parametrize('first_notice', [False, True])
def test_explicit_notice_rollback_cannot_reset_saved_context(first_notice):
    rows = [row(1, 'manufacturing_offset', 0, blueprint='Widget Blueprint',
                attempts_total=1, cost_per_attempt=.91, total_cost=.91)]
    if first_notice:
        rows.append(row(2, 'craft', 10, result='fail'))
    else:
        rows.append(row(2, 'loot', 10, item_name='Widget', value=1))
    rows += [row(3, 'craft', 9, result='success'),
             row(4, 'loot', 12, item_name='Widget', value=1)]
    builder = ActivityTrendBuilder()
    for event in rows:
        builder.extend([event])
        builder.snapshot()
    for points in (builder.snapshot(), build_activity_points(rows)):
        assert points.manufacturing.observed == 2
        assert points[-1]['excess_input_estimate'] is None
        assert crafting_cost_display(.91, 2, points).marker == '*'


@pytest.mark.parametrize('overrun', [False, True])
def test_unpriced_heal_keeps_whole_session_costs_partial_with_or_without_overrun(overrun):
    rows = facts() if overrun else facts()[:5]
    rows.append(row(len(rows) + 1, 'combat', 40, healed=2, healing_action='self', healing_cost_status='unknown_cost'))
    points = build_activity_points(rows)
    view = crafting_cost_display(13.65, 20, points)
    assert points.unpriced_heals == 1
    assert view.marker == '*' and view.return_pct is None
    assert view.cost == pytest.approx(15.47 if overrun else 13.65)
    assert 'healing observations have unknown inputs' in '; '.join(view.incomplete_reasons)
