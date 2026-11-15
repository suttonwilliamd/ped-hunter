"""Private synthetic regressions for the interrupted Springs batch; no native Tk."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder, build_activity_points
from ped_hunter.app import PedHunterApp, _kill_trend_points, _context_details
from ped_hunter.catalog import Catalog
from ped_hunter.storage import Store

BLUEPRINT = 'Simple 1 Plastic Springs Blueprint'


def row(kind, second=None, **payload):
    return dict(kind=kind, timestamp=None if second is None else f'2026-01-01T10:{second // 60:02d}:{second % 60:02d}',
                payload=json.dumps(payload))


def receipt(second, item, value):
    return row('loot', second, item_name=item, value=value)


def history():
    rows = [row('manufacturing_offset', attempts_total=46, cost_per_attempt=.91,
                total_cost=41.86, blueprint=BLUEPRINT)]
    rows += [receipt(i * 4, 'Animal Oil Residue', .4) for i in range(1, 41)]
    rows += [row('combat', 170, damage=3, shot_cost=.1), receipt(171, 'Animal Muscle Oil', .05)]
    rows += [receipt(190, 'Animal Eye Oil', .21), receipt(190, 'Animal Muscle Oil', .1),
             receipt(190, 'Animal Oil Residue', .11), receipt(190, 'Shrapnel', .0027),
             receipt(206, 'Simple 1 Plastic Springs', .8), receipt(206, 'Animal Oil Residue', .17),
             receipt(206, 'Shrapnel', .0069), receipt(211, 'Animal Oil Residue', .48),
             receipt(211, 'Shrapnel', .0018)]
    return [dict(r, id=i) for i, r in enumerate(rows, 1)]


def test_interrupted_batch_resumes_only_observable_groups_not_reported_six_clicks():
    rows = history()
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 43  # NOT 46: silent clicks have no receipts.
    assert points.manufacturing.attempts == 46
    assert points.manufacturing.booked_cost == pytest.approx(41.86)
    assert points.manufacturing.active
    assert [p['kind'] for p in points[-4:]] == ['hunt', 'craft', 'craft', 'craft']
    assert [p['cost'] for p in points[-3:]] == pytest.approx([.91] * 3)
    assert [p['loot'] for p in points[-3:]] == pytest.approx([.4227, .9769, .4818])
    assert [p['attempt_number'] for p in points[-3:]] == [41, 42, 43]
    assert [p['loot_events'] for p in points[-3:]] == [4, 3, 2]
    assert [p['event_id'] for p in points[-3:]] == [44, 48, 51]
    assert all(p['inferred'] and p['shot_count'] == 0 for p in points[-3:])
    assert points[-4]['cost'] == pytest.approx(.1)


@pytest.mark.parametrize('batch_size', [1, 2, 3, 7, 53])
def test_every_incremental_prefix_and_snapshot_matches_rebuild(batch_size):
    rows = history()
    builder = ActivityTrendBuilder()
    snapshots = []
    for start in range(0, len(rows), batch_size):
        end = start + batch_size
        builder.extend(rows[start:end])
        snapshot = builder.snapshot()
        snapshots.append((end, snapshot, deepcopy(snapshot.__dict__)))
        full = build_activity_points(rows[:end])
        assert snapshot == full
        assert snapshot.manufacturing == full.manufacturing
    assert builder.snapshot().manufacturing.observed == 43
    for end, snapshot, metadata in snapshots:
        assert snapshot == build_activity_points(rows[:end])
        assert snapshot.__dict__ == metadata


@pytest.mark.parametrize('signal', ['Animal Oil Residue', 'Simple 1 Plastic Springs'])
def test_cross_second_pending_inputs_promote_as_one_receipt(signal):
    rows = [row('manufacturing_offset', attempts_total=2, cost_per_attempt=.91, total_cost=1.82, blueprint=BLUEPRINT),
            row('combat', 1, damage=1, shot_cost=.1), receipt(2, 'Shrapnel', .1),
            receipt(10, 'Animal Eye Oil', .21), receipt(11, signal, .1)]
    points = build_activity_points(dict(r, id=i) for i, r in enumerate(rows, 1))
    assert [p['kind'] for p in points] == ['hunt', 'craft']
    assert points[-1]['event_id'] == 4
    assert points[-1]['loot'] == pytest.approx(.31)
    assert points[-1]['loot_events'] == 2
    assert points.manufacturing.observed == 1


@pytest.mark.parametrize('item', ['Animal Eye Oil', 'Animal Muscle Oil', 'Shrapnel', 'Animal Oil Residue fragment'])
def test_generic_hunting_returns_do_not_resume_springs(item):
    rows = history()[:43] + [dict(receipt(190, item, .2), id=44)]
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 40
    assert not points.manufacturing.active
    assert points[-1]['kind'] == 'hunt'


@pytest.mark.parametrize('blueprint', ['Other Blueprint', 'Simple 2 Plastic Springs Blueprint'])
def test_other_blueprints_are_outside_this_narrow_resume_rule(blueprint):
    rows = history()
    p = json.loads(rows[0]['payload']); p['blueprint'] = blueprint; rows[0]['payload'] = json.dumps(p)
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 40
    assert not points.manufacturing.active


@pytest.mark.parametrize('second', [None, 169])
def test_missing_or_backward_signal_timestamp_cannot_resume(second):
    rows = history()[:43] + [dict(receipt(second, 'Animal Oil Residue', .2), id=44)]
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 40
    assert not points.manufacturing.active


def test_signal_does_not_steal_a_pending_shot_bearing_hunt():
    rows = history()[:42] + [dict(receipt(170, 'Animal Oil Residue', .2), id=43)]
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 40
    assert points[-1]['kind'] == 'hunt'
    assert points[-1]['shot_count'] == 1
    assert points[-1]['cost'] == pytest.approx(.1)


def test_excess_observed_receipts_are_unknown_cost_not_new_bookings():
    rows = history()
    rows += [dict(receipt(220 + i * 4, 'Animal Oil Residue', .2), id=54+i) for i in range(4)]
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 47
    assert points[-1]['cost'] is None
    assert points.manufacturing.booked_cost == pytest.approx(41.86)


def test_private_persisted_ingestion_refresh_replay_and_booking_conservation(tmp_path):
    store = Store(tmp_path / 'synthetic.sqlite3')
    sid = store.start_session('hunt')
    app = SimpleNamespace(store=store, catalog=Catalog.load(), _restore_session_loadout=Mock(return_value=None))
    previous = None
    for r in history():
        payload = json.loads(r['payload'])
        if r['kind'] == 'manufacturing_offset':
            store.add_event(sid, dict(r, raw_message='Synthetic plan', payload=payload))
            continue
        second = r['timestamp'].replace('T', ' ')
        if r['kind'] == 'combat':
            raw = f'{second} [System] [] You inflicted 3 points of damage'
        else:
            raw = f"{second} [System] [] You received {payload['item_name']} x (1) Value: {payload['value']} PED"
        parsed, previous = PedHunterApp._process_lines(app, [raw], sid, previous)
        assert parsed == 1
        # Only the established immediate-last-line replay contract is asserted.
        assert PedHunterApp._process_lines(app, [raw], sid, previous) == (0, previous)
    before = store.get_session(sid)
    with store.connect() as c:
        original = c.execute('SELECT * FROM events ORDER BY id').fetchall()
    builder = ActivityTrendBuilder()
    for _ in range(3):
        points = _kill_trend_points(store, sid, builder=builder)
        assert points.manufacturing.observed == 43
        assert points.manufacturing.booked_cost == pytest.approx(41.86)
    assert points == _kill_trend_points(store, sid)
    assert before.hunting_cost == pytest.approx(41.86)
    assert store.get_session(sid) == before
    with store.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM events ORDER BY id')] == [tuple(r) for r in original]
        assert c.execute("SELECT count(*) FROM events WHERE kind='manufacturing_allocation'").fetchone()[0] == 0
    # Existing display helper must expose the same saved price/ordinal in both currencies.
    ped = _context_details('Synthetic', points, 'PED', plan=points.manufacturing)
    usd = _context_details('Synthetic', points, 'USD', plan=points.manufacturing)
    assert '43/46' in ped and '0.91 PED' in ped
    assert '43/46' in usd and '$0.091' in usd


def test_same_second_explicit_notice_and_receipt_consume_only_one_observed_group():
    rows = history()[:43]
    rows += [dict(row('craft', 190, result='fail', input_cost=.91), id=44),
             dict(receipt(190, 'Animal Oil Residue', .2), id=45),
             dict(receipt(190, 'Shrapnel', .01), id=46)]
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 41
    assert points[-1]['loot_events'] == 2
    assert not points[-1]['inferred']
    assert points[-1]['cost'] == pytest.approx(.91)
    assert points.manufacturing.booked_cost == pytest.approx(41.86)


def test_healing_during_resumed_crafting_is_unassigned_and_never_added_to_craft_input():
    rows = history()
    rows += [dict(row('combat', 212, healing_selection={'version': 2}, healing_action='self',
                      healing_activation_event_id=53, healing_cost=.04,
                      healing_allocation_eligible=True), id=53)]
    points = build_activity_points(rows)
    assert points.healing_contexts[53][0] is None
    assert points.healing_pending == pytest.approx(.04)
    assert points.healing_allocated == 0
    assert [p['healing_cost'] for p in points[-3:]] == [0, 0, 0]
    assert points.manufacturing.observed == 43
    assert points[-1]['cost'] == pytest.approx(.91)


def test_runtime_currency_display_uses_resumed_ordinal_and_existing_batch(tmp_path, monkeypatch):
    """Execute real HUD construction/update using nonnative widgets only."""
    from types import MethodType
    from ped_hunter import app as module
    from ped_hunter.hunt_attribution import HuntAttribution
    from test_streamer_readability import BindingWidget, BindingValue, Font

    store = Store(tmp_path / 'display.sqlite3')
    sid = store.start_session('hunt')
    for r in history():
        store.add_event(sid, dict(r, payload=json.loads(r['payload']), raw_message=f"Synthetic {r['id']}"))
    summary = store.get_session(sid)
    with store.connect() as c:
        original = [tuple(r) for r in c.execute('SELECT * FROM events ORDER BY id')]
    for name in ('Frame', 'Button', 'Label', 'Canvas', 'Toplevel'):
        monkeypatch.setattr(module.tk, name, BindingWidget)
    hud = BindingWidget()
    hud.ui_scale = hud._content_scale = 1
    hud.tk = SimpleNamespace(call=lambda *_: 'win32', splitlist=lambda text: text.split())
    mode = BindingValue('PED')
    hud.app = SimpleNamespace(store=store, currency_mode=mode, session_id=sid, running=False,
                              reduced_motion=BindingValue(True), _activate_saved_loadout=Mock())
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
    for currency, price, batch in [('PED', '0.91 PED', '41.86 PED'), ('USD', '$0.091', '$4.186'),
                                   ('PED', '0.91 PED', '41.86 PED')]:
        mode.set(currency)
        module.StreamerWindow.update_from_session(hud, store.get_session(sid))
        assert not hud._trend_error and not hud._attribution_error and not hud._trail_error
        assert '43/46 observed*' in hud.vars['input'].get()
        assert price in hud.vars['input'].get()
        assert batch in hud._footer_detail and 'TT once' in hud._footer_detail
        assert hud.manufacturing_plan.observed == 43
        assert store.get_session(sid) == summary
        with store.connect() as c:
            assert [tuple(r) for r in c.execute('SELECT * FROM events ORDER BY id')] == original


def test_intermediate_generic_input_does_not_orphan_cross_second_receipt():
    rows = history()[:43]
    rows += [dict(receipt(190, 'Animal Eye Oil', .21), id=44),
             dict(receipt(191, 'Animal Muscle Oil', .1), id=45),
             dict(receipt(191, 'Animal Oil Residue', .11), id=46),
             dict(row('combat', 200, damage=2, shot_cost=.2), id=47),
             dict(receipt(201, 'Shrapnel', .3), id=48)]
    builder = ActivityTrendBuilder()
    snapshots = []
    for end, r in enumerate(rows, 1):
        builder.extend([r])
        snapshot = builder.snapshot()
        snapshots.append((snapshot, deepcopy(snapshot), deepcopy(snapshot.__dict__)))
        assert snapshot == build_activity_points(rows[:end])
        assert snapshot.manufacturing == build_activity_points(rows[:end]).manufacturing
    points = builder.snapshot()
    assert points.manufacturing.observed == 41
    assert [p['kind'] for p in points[-3:]] == ['hunt', 'craft', 'hunt']
    assert points[-2]['event_id'] == 44
    assert points[-2]['loot_events'] == 3
    assert points[-2]['loot'] == pytest.approx(.42)
    assert points[-2]['cost'] == pytest.approx(.91)
    assert points[-1]['label'] == 'K2'
    assert points[-1]['avg_cost'] == pytest.approx(.15)
    assert points[-1]['delta_profit'] == pytest.approx(.15)
    for original, values, metadata in snapshots:
        assert original == values and original.__dict__ == metadata
    # Before the distinctive marker, retain ordinary hunting semantics.
    prefix = build_activity_points(rows[:45])
    assert [p['kind'] for p in prefix[-2:]] == ['hunt', 'hunt']
    assert prefix.manufacturing.observed == 40


def test_receipt_promotion_window_does_not_chain_or_cross_equipment_boundary():
    rows = history()[:43]
    rows += [dict(receipt(190, 'Animal Eye Oil', .21), id=44),
             dict(receipt(191, 'Animal Muscle Oil', .1), id=45),
             dict(receipt(192, 'Animal Oil Residue', .11), id=46)]
    points = build_activity_points(rows)
    # The 190s input cannot be chained into the signal's 1.25s window.
    assert points[-2]['kind'] == 'hunt' and points[-2]['loot'] == pytest.approx(.21)
    assert points[-1]['loot'] == pytest.approx(.21)
    boundary_rows = rows[:44] + [dict(row('weapon', 190), id=45),
                                dict(receipt(191, 'Animal Muscle Oil', .1), id=46),
                                dict(receipt(191, 'Animal Oil Residue', .11), id=47)]
    boundary = build_activity_points(boundary_rows)
    assert boundary[-2]['kind'] == 'hunt' and boundary[-2]['loot'] == pytest.approx(.21)
    assert boundary[-1]['kind'] == 'craft' and boundary[-1]['loot'] == pytest.approx(.21)


@pytest.mark.parametrize('kind', ['weapon', 'repair'])
def test_pending_same_second_inputs_cannot_be_promoted_across_equipment_boundary(kind):
    rows = history()[:43]
    rows += [dict(receipt(190, 'Animal Eye Oil', .21), id=44),
             dict(row(kind, 190), id=45),
             dict(receipt(190, 'Animal Muscle Oil', .1), id=46),
             dict(receipt(191, 'Animal Oil Residue', .11), id=47)]
    points = build_activity_points(rows)
    assert points.manufacturing.observed == 40
    assert not points.manufacturing.active
    assert points[-1]['kind'] == 'hunt'
    # A later, separate distinctive receipt can still resume without borrowing
    # the ambiguous pre-boundary input group.
    points = build_activity_points(rows + [dict(receipt(200, 'Animal Oil Residue', .2), id=48)])
    assert points.manufacturing.observed == 41
    assert points[-1]['event_id'] == 48
    assert points[-1]['loot'] == pytest.approx(.2)
    assert points[-1]['cost'] == pytest.approx(.91)
