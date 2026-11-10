"""Personal booked inputs/receipts stay immutable; team markers are not kill IDs."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
from pathlib import Path

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder, build_activity_points
from ped_hunter.chart_trails import TrailRecorder
from ped_hunter.parser import parse_line
from ped_hunter.storage import Store
from ped_hunter.team_encounters import (
    MAX_MARKERS, NO_LOOT, project_team_encounters, visible_chart_points,
)

START = datetime(2026, 1, 1, 10)


def row(event_id, kind, second, **payload):
    return {'id': event_id, 'kind': kind,
            'timestamp': (START + timedelta(seconds=second)).isoformat(),
            'raw_message': f'isolated {event_id} {kind}', 'payload': json.dumps(payload)}


def use(event_id, second, cost=0.1):
    return row(event_id, 'combat', second, shot_cost=cost, ammo_cost=cost,
               repair_decay=0.0, loadout='Owned setup')


def notice(event_id, second, channel='Team', speaker='', message=NO_LOOT):
    return row(event_id, 'chat', second, channel=channel, speaker=speaker, message=message)


def opening(event_id, second, message='Teammate opened a Sacred Idol'):
    return notice(event_id, second, 'Force', message=message)


def receipt(event_id, second, value):
    return row(event_id, 'loot', second, item_name='Shrapnel', quantity=100,
               value=value, is_personal=True)


def projected(rows):
    return project_team_encounters(build_activity_points(rows))


def test_incremental_costs_pending_returns_then_whole_budget_weighted_personal_payout():
    builder = ActivityTrendBuilder()
    rows = [use(1, 0), use(2, 1), notice(3, 2), use(4, 3, .3), use(5, 4, .3), notice(6, 5)]
    builder.extend(rows)
    pending = project_team_encounters(builder.snapshot())
    assert [p['cost'] for p in pending] == pytest.approx([.2, .6])
    assert [p['loot'] for p in pending] == [None, None]
    assert [p['profit'] for p in pending] == [None, None]
    builder.extend([opening(7, 6), receipt(8, 6, 1.2)])
    partial = project_team_encounters(builder.snapshot())
    assert [p['loot'] for p in partial] == pytest.approx([.3, .9])
    builder.extend([receipt(9, 6, .8)])
    completed = project_team_encounters(builder.snapshot())
    assert [p['loot'] for p in completed] == pytest.approx([.5, 1.5])
    assert [p['profit'] for p in completed] == [None, None]
    assert completed[-1]['loot'] > completed[0]['loot']  # Greater observed cost, not boss multiplier.
    assert math.fsum(p['recorded_own_input'] for p in completed) == pytest.approx(.8)
    assert math.fsum(p['allocated_return'] for p in completed) == pytest.approx(2)
    assert completed[0]['encounter_receipt_ids'] == (8, 9)
    assert completed[0]['encounter_opener_id'] == 7
    assert [p['loot'] for p in pending] == [None, None]  # Old snapshot cannot mutate.
    builder.extend([notice(10, 8, 'Rookie', 'Observer', 'Unrelated recorded source activity')])
    settled = project_team_encounters(builder.snapshot())
    assert [point['profit'] for point in settled] == pytest.approx([.3, .9])
    assert all(point['encounter_batch_closed'] for point in settled)
    assert [point['profit'] for point in completed] == [None, None]


@pytest.mark.parametrize('channel,speaker,message', [
    ('Rookie', '', NO_LOOT), ('Team', 'Someone', NO_LOOT),
    ('System', '', NO_LOOT), ('Team', '', 'Diese Kreatur hatte keine Beute'),
])
def test_non_dungeon_channels_quotes_and_unverified_localization_leave_ordinary_hunt_unchanged(channel, speaker, message):
    rows = [use(1, 0), notice(2, 1, channel, speaker, message), receipt(3, 2, .5)]
    before = build_activity_points(rows)
    assert projected(rows) == before
    assert projected(rows)[0]['cost'] == .1
    assert projected(rows)[0]['loot'] == .5


def test_real_direct_team_spacing_forms_are_not_other_player_speech():
    for body in ('[Team]This creature didn\'t carry any loot',
                 '[Team] [] This creature didn\'t carry any loot'):
        event = parse_line('2026-01-01 10:00:01 ' + body)
        assert event is not None
        parsed = event.to_row() | {'id': 2}
        parsed['payload'] = json.dumps(parsed['payload'])
        points = projected([use(1, 0), parsed])
        assert points[0]['kind'] == 'encounter'
        assert points[0]['loot'] is None and points[0]['cost'] == .1


def test_normal_receipt_without_container_is_not_silently_assigned_to_team_notices():
    rows = [use(1, 0), notice(2, 1), use(3, 2), notice(4, 3), receipt(5, 4, .7)]
    assert projected(rows) == build_activity_points(rows)
    assert len(projected(rows)) == 1
    assert projected(rows)[0]['loot'] == .7


@pytest.mark.parametrize('boundary', [
    opening(3, 2, 'Teammate opened a Cash Register'),
    notice(3, 2, 'Force', 'Player', 'Teammate opened a Sacred Idol'),
    opening(3, 90),
])
def test_unsupported_or_stale_container_link_does_not_capture_personal_loot(boundary):
    rows = [use(1, 0), notice(2, 1), boundary, receipt(4, 91, .9)]
    assert projected(rows) == build_activity_points(rows)


def test_delayed_receipt_without_supported_container_window_keeps_personal_booking_separate():
    rows = [use(1, 0), notice(2, 1), opening(3, 2), receipt(4, 10, .9)]
    points = projected(rows)
    assert math.fsum(p['loot'] or 0 for p in points) == pytest.approx(.9)
    assert not any(p.get('allocated_return') is not None for p in points)


def test_team_payouts_and_incoming_combat_never_become_own_input_or_personal_return():
    rows = [use(1, 0), row(2, 'combat', .2, damage_taken=40),
            row(3, 'combat', .3, healed=20), notice(4, 1), opening(5, 2),
            notice(6, 2, 'Force', message='Other player received Shrapnel (999999)'),
            receipt(7, 2, .4)]
    points = projected(rows)
    assert len(points) == 1
    assert points[0]['cost'] == .1
    assert points[0]['loot'] == .4
    assert points[0]['shot_count'] == 1


def test_same_raw_duplicate_does_not_create_an_enemy_or_consume_budget_twice():
    marker = notice(2, 1)
    repeated = dict(marker)
    rows = [use(1, 0), marker, repeated, use(4, 2), notice(5, 3), opening(6, 4), receipt(7, 4, .8)]
    points = projected(rows)
    assert len(points) == 2
    assert [p['encounter_markers'] for p in points] == [2, 2]
    assert math.fsum(p['recorded_own_input'] for p in points) == pytest.approx(.2)
    assert math.fsum(p['allocated_return'] for p in points) == pytest.approx(.8)


def test_different_timestamp_notice_with_no_own_use_stays_uncertain_not_a_free_confirmed_kill():
    rows = [use(1, 0), notice(2, 1), notice(3, 2), opening(4, 3), receipt(5, 3, .8)]
    points = projected(rows)
    assert len(points) == 2
    assert points[1]['recorded_own_input'] == 0  # Known logged budget, not exact creature cost.
    assert points[1]['cost'] is None
    assert points[1]['loot'] is None and points[1]['profit'] is None
    assert math.fsum(p['allocated_return'] for p in points) == pytest.approx(.8)


def test_distinct_recorded_identical_notices_are_ambiguous_markers_not_extra_charges():
    rows = [use(1, 0), notice(2, 1), notice(3, 1),
            opening(4, 2), receipt(5, 2, .8)]
    points = projected(rows)
    assert len(points) == 2
    assert [point['event_id'] for point in points] == [2, 3]
    assert [point['cost'] for point in points] == [.1, None]
    assert points[0]['loot'] == pytest.approx(.8) and points[1]['loot'] is None
    assert all(point['allocated_return'] >= 0 for point in points)
    assert math.fsum(point['allocated_return'] for point in points) == .8
    assert math.fsum(point['booked_input'] for point in points) == .1


@pytest.mark.parametrize('boundary', [
    row(3, 'craft', 2, result='fail'),
    notice(3, 2, 'Team', message='Your team was disbanded'),
    notice(3, 7202, 'Rookie', 'Other', 'Time advanced'),
])
def test_interruption_and_limits_never_fabricate_completion_or_zero_profit(boundary):
    points = projected([use(1, 0), notice(2, 1), boundary])
    team = [p for p in points if p.get('kind') == 'encounter']
    assert team[0]['cost'] == .1
    assert team[0]['loot'] is None and team[0]['profit'] is None
    assert team[0]['encounter_opener_id'] is None
    assert team[0]['encounter_status'].startswith('interrupted_')


def test_captured_44_marker_timeline_costs_loot_and_reload_are_conserved_without_a_44_rule():
    fixture = json.loads((Path(__file__).parent / 'fixtures/team_encounter_sacred_idol.json').read_text())
    rows = [dict(id=i, timestamp=t, kind=k, payload=json.dumps(p), raw_message=f'captured semantic event {i}')
            for i, t, k, p in fixture['events']]
    original = deepcopy(rows)
    ordinary = build_activity_points(rows)
    points = projected(rows)
    assert len(ordinary) == 1 and len(points) == 44
    assert math.fsum(p['recorded_own_input'] for p in points) == pytest.approx(22.6188)
    assert math.fsum(p['loot'] or 0 for p in points) == pytest.approx(8.45)
    assert points[-1]['recorded_own_input'] == pytest.approx(2.12592)
    assert points[-1]['loot'] > points[0]['loot']
    assert len(visible_chart_points(points)) == 44
    assert points == projected(rows) and rows == original
    assert points[-1]['encounter_receipt_ids'] == (237227, 237228)


def test_all_markers_are_visible_then_new_ordinary_activity_replaces_the_encounter():
    rows = []
    for index in range(37):
        rows += [use(index * 2 + 1, index * 2), notice(index * 2 + 2, index * 2 + 1)]
    rows += [opening(75, 74), receipt(76, 74, 10)]
    assert len(visible_chart_points(projected(rows))) == 37
    for index in range(16):
        rows += [use(77 + index * 2, 80 + index * 3), receipt(78 + index * 2, 81 + index * 3, .1)]
    visible = visible_chart_points(projected(rows))
    assert len(visible) == 16 and not any(p.get('kind') == 'encounter' for p in visible)


def test_recording_and_reloading_real_store_extends_only_genuine_live_cost_and_return_fans(tmp_path):
    store = Store(tmp_path / 'owned.sqlite3')
    sid = store.start_session('hunt')
    recorder = TrailRecorder(store)
    rows = []
    def append(item):
        payload = json.loads(item['payload'])
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} | {'payload': payload})
        with store.connect() as conn:
            rows[:] = [dict(r) for r in conn.execute('SELECT * FROM events WHERE session_id=? ORDER BY id', (sid,))]
        points = projected(rows)
        return points, recorder.update(sid, points, True, revised_event_ids=getattr(points, 'encounter_changed_ids', ()))
    append(use(1, 0))
    append(use(2, 1))
    first, histories = append(notice(3, 2))
    first_id = first[0]['event_id']
    assert [s[0] for s in histories[first_id]][:2] == pytest.approx([.1, .2])
    assert all(s[2] is None for s in histories[first_id])
    append(use(4, 3, .3))
    append(use(5, 4, .3))
    append(notice(6, 5))
    append(opening(7, 6))
    append(receipt(8, 6, 2))
    points = projected(rows)
    with store.connect() as conn:
        before = [tuple(r) for r in conn.execute('SELECT * FROM events ORDER BY id')]
    session_before = store.get_session(sid)
    histories = recorder.update(sid, points, True, revised_event_ids=points.encounter_changed_ids)
    assert histories[first_id][-1][2] == pytest.approx(.5)
    assert histories[points[-1]['event_id']][-1][2] == pytest.approx(1.5)
    assert TrailRecorder(store).update(sid, projected(rows), False) == histories
    with store.connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM events ORDER BY id')] == before
    assert store.get_session(sid) == session_before
    assert store.get_session(sid).hunting_cost == pytest.approx(.8)
    assert store.get_session(sid).loot_value == 2


def test_completed_old_encounter_with_no_history_never_acquires_replayed_fans(tmp_path):
    store = Store(tmp_path / 'legacy.sqlite3')
    sid = store.start_session('hunt')
    for item in [use(1, 0), notice(2, 1), use(3, 2), notice(4, 3), opening(5, 4), receipt(6, 4, 1)]:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} | {'payload': json.loads(item['payload'])})
    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM events WHERE session_id=? ORDER BY id', (sid,))]
    points = projected(rows)
    assert TrailRecorder(store).update(sid, points, True, revised_event_ids=points.encounter_changed_ids) == {}
    assert store.load_chart_trails(sid, [p['event_id'] for p in points]) == {}


def test_unknown_prices_stay_unknown_and_observed_personal_receipts_remain_unallocated():
    rows = [row(1, 'combat', 0, damage=4), notice(2, 1)]
    pending = projected(rows)
    assert pending[0]['cost'] is None
    assert pending[0]['loot'] is None and pending[0]['profit'] is None
    rows += [opening(3, 2), receipt(4, 2, 2)]
    points = projected(rows)
    assert len(points) == 2
    assert points[0]['cost'] is None and points[0]['loot'] is None
    assert points[1]['kind'] == 'encounter_receipt'
    assert points[1]['loot'] == 2 and points[1]['profit'] is None
    assert all(p.get('allocated_return') is None for p in points)
    assert points == projected(rows)


def test_last_marker_is_not_given_a_boss_multiplier_without_greater_logged_cost():
    points = projected([use(1, 0, 1), notice(2, 1), use(3, 2, .1),
                        notice(4, 3), opening(5, 4), receipt(6, 4, 11)])
    assert [p['loot'] for p in points] == pytest.approx([10, 1])


def test_existing_shared_pool_budget_is_never_truncated_to_one_encounter_member():
    rows = [use(1, 0), notice(2, 1), opening(3, 2), receipt(4, 2, 1)]
    raw = build_activity_points(rows)
    raw[0] = raw[0] | {'cost': .05, 'cost_status': 'shared_estimated',
                       'cost_pool_total': .1, 'cost_pool_id': 1}
    original = deepcopy(raw)
    points = project_team_encounters(raw)
    assert points == original


def test_marker_transfer_sqlite_failure_preserves_actual_source_cost_observations_for_retry(tmp_path):
    import sqlite3
    store = Store(tmp_path / 'failure.sqlite3')
    sid = store.start_session('hunt')
    recorder = TrailRecorder(store)
    for item in [use(1, 0), use(2, 1)]:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'}
                        | {'payload': json.loads(item['payload'])})
        with store.connect() as conn:
            rows = [dict(r) for r in conn.execute('SELECT * FROM events WHERE session_id=? ORDER BY id', (sid,))]
        recorder.update(sid, projected(rows), True)
    before = store.load_chart_trails(sid, [rows[0]['id']])
    item = notice(3, 2)
    store.add_event(sid, {key: value for key, value in item.items() if key != 'id'}
                    | {'payload': json.loads(item['payload'])})
    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM events WHERE session_id=? ORDER BY id', (sid,))]
        marker = rows[-1]['id']
        conn.execute(f"CREATE TRIGGER fail_owned_marker BEFORE INSERT ON chart_trails WHEN NEW.event_id={marker} BEGIN SELECT RAISE(ABORT,'owned failure'); END")
    points = projected(rows)
    with pytest.raises(sqlite3.IntegrityError):
        recorder.update(sid, points, True, revised_event_ids=points.encounter_changed_ids)
    assert store.load_chart_trails(sid, [rows[0]['id']]) == before
    assert store.load_chart_trails(sid, [marker]) == {}
    with store.connect() as conn:
        conn.execute('DROP TRIGGER fail_owned_marker')
    after = recorder.update(sid, points, True, revised_event_ids=points.encounter_changed_ids)
    assert [sample[0] for sample in after[marker]][:2] == pytest.approx([.1, .2])
    assert all(sample[2] is None for sample in after[marker])


def test_candidate_retention_is_bounded_without_inventing_completion_or_losing_booked_inputs():
    rows = []
    for index in range(MAX_MARKERS + 5):
        rows += [use(index * 2 + 1, index * 2), notice(index * 2 + 2, index * 2 + 1)]
    raw = build_activity_points(rows)
    assert all(len(candidate['members']) <= MAX_MARKERS + 1 for candidate in raw.team_encounters)
    assert any(candidate['status'] == 'interrupted_limit' for candidate in raw.team_encounters)
    assert all(not candidate['receipt_ids'] for candidate in raw.team_encounters)
    points = project_team_encounters(raw)
    assert math.fsum(p['cost'] or 0 for p in points) == pytest.approx((MAX_MARKERS + 5) * .1)
    assert not any(p.get('allocated_return') is not None for p in points)


@pytest.mark.parametrize('known_input', [0, .1])
def test_sqlite_live_and_resume_feed_retains_team_markers_without_inventing_missing_prices(tmp_path, known_input):
    from ped_hunter.app import _kill_trend_points
    from ped_hunter.hunt_attribution import HuntAttribution
    store = Store(tmp_path / 'real-feed.sqlite3')
    sid = store.start_session('hunt')
    rows = ([use(1, 0, known_input)] if known_input else [])
    rows += [row(2, 'combat', 1, damage=4), notice(3, 2),
             opening(4, 3), receipt(5, 3, 2)]
    for item in rows:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} |
                        {'payload': json.loads(item['payload'])})
    with store.connect() as conn:
        original = [tuple(item) for item in conn.execute('SELECT * FROM events ORDER BY id')]
    builder = ActivityTrendBuilder()
    raw = _kill_trend_points(store, sid, builder=builder)
    points = project_team_encounters(HuntAttribution(store).update(sid, raw, live=True))
    assert [point['kind'] for point in points] == ['encounter', 'encounter_receipt']
    assert [point['cost'] for point in points] == [None, None]
    assert [point['loot'] for point in points] == [None, 2]
    assert points[0]['booked_input'] == pytest.approx(known_input)
    assert all(point['profit'] is None for point in points)
    assert store.get_session(sid).hunting_cost == pytest.approx(known_input)
    assert store.get_session(sid).loot_value == 2
    reopened = Store(store.db_path)
    restored = project_team_encounters(HuntAttribution(reopened).update(
        sid, _kill_trend_points(reopened, sid), live=False))
    assert restored == points
    with reopened.connect() as conn:
        assert [tuple(item) for item in conn.execute('SELECT * FROM events ORDER BY id')] == original


def test_legacy_aggregate_fan_is_preserved_but_never_relabelled_as_enemy_history(tmp_path):
    store = Store(tmp_path / 'legacy-aggregate.sqlite3')
    sid = store.start_session('hunt')
    rows = [use(1, 0), notice(2, 1), use(3, 2), notice(4, 3),
            opening(5, 4), receipt(6, 4, 1)]
    for item in rows:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} |
                        {'payload': json.loads(item['payload'])})
    old = ((.1, .1, 0), (.2, .2, 1))
    store.save_chart_trails(sid, {1: old})
    points = projected(rows)
    assert TrailRecorder(store).update(sid, points, True, revised_event_ids=points.encounter_changed_ids) == {}
    assert store.load_chart_trails(sid, [1]) == {1: old}
    assert store.load_chart_trails(sid, [2, 4]) == {}
    assert store.get_session(sid).hunting_cost == pytest.approx(.2)
    assert store.get_session(sid).loot_value == 1


def test_no_own_use_notices_are_unknown_markers_not_free_kills_or_fabricated_fans(tmp_path):
    store = Store(tmp_path / 'no-own-input.sqlite3')
    sid = store.start_session('hunt')
    rows = [notice(1, 0), notice(2, 1)]
    for item in rows:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} |
                        {'payload': json.loads(item['payload'])})
    points = projected(rows)
    assert [point['event_id'] for point in points] == [1, 2]
    assert [point['cost'] for point in points] == [None, None]
    assert [point['avg_cost'] for point in points] == [0, 0]  # Booked input is known zero, not enemy cost.
    assert [point['loot'] for point in points] == [None, None]
    recorder = TrailRecorder(store)
    assert recorder.update(sid, points, True) == {}
    assert recorder.update(sid, points, True, revised_event_ids={1, 2}) == {}
    with pytest.raises(ValueError):
        store.save_chart_trails(sid, {1: [(None, None, None)]})
    assert store.load_chart_trails(sid, [1, 2]) == {}
    paid = projected(rows + [opening(3, 2), receipt(4, 2, 1.2)])
    assert [point['loot'] for point in paid] == [None, None, 1.2]
    assert all(point['profit'] is None for point in paid)
    assert 'cost_status' not in paid[-1]  # Actual unallocated receipt, not an inferred share.
    assert store.get_session(sid).hunting_cost == 0


def test_first_own_use_after_team_notices_binds_its_actual_budget_without_charging_other_kills():
    points = projected([notice(1, 0), use(2, 1, .2), notice(3, 2),
                        opening(4, 3), receipt(5, 3, .8)])
    assert [point['event_id'] for point in points] == [1, 3]
    assert [point['cost'] for point in points] == [None, .2]
    assert [point['loot'] for point in points] == [None, .8]
    assert [point['allocated_return'] for point in points] == [0, .8]
    assert math.fsum(point['booked_input'] for point in points) == .2


def test_missing_receipt_price_keeps_profit_pending_and_known_subtotal_separate():
    rows = [use(1, 0), notice(2, 1), opening(3, 2),
            row(4, 'loot', 2, item_name='Unknown item', quantity=1, is_personal=True),
            receipt(5, 2, .4)]
    points = projected(rows)
    assert [point['loot'] for point in points] == [None, .4]
    assert all(point['profit'] is None and point['encounter_return_pending'] for point in points)
    assert all(point.get('allocated_return') is None for point in points)
    assert 'cost_status' not in points[-1]
    entirely_unknown = projected(rows[:-1])
    assert len(entirely_unknown) == 1 and entirely_unknown[0]['loot'] is None


def test_partially_priced_use_preserves_booked_ammo_without_guessing_missing_decay():
    points = projected([row(1, 'combat', 0, damage=4, ammo_cost=.1),
                        notice(2, 1), opening(3, 2), receipt(4, 2, .5)])
    assert [point['cost'] for point in points] == [None, None]
    assert points[0]['booked_input'] == .1
    assert points[0]['recorded_own_input'] is None
    assert [point['loot'] for point in points] == [None, .5]
    assert all(point.get('allocated_return') is None for point in points)


def test_marker_timestamps_keep_original_source_text_not_the_later_container_time():
    rows = [use(1, 0), notice(2, 1), use(3, 2), notice(4, 3),
            opening(5, 4), receipt(6, 4, 1)]
    assert [point['timestamp'] for point in projected(rows)] == [rows[1]['timestamp'], rows[3]['timestamp']]


def test_unmarked_final_input_gets_its_supported_weight_without_fabricated_boss_notice():
    points = projected([use(1, 0), notice(2, 1), use(3, 2, .9),
                        opening(4, 3), receipt(5, 3, 1)])
    assert [point['cost'] for point in points] == [.1, .9]
    assert [point['loot'] for point in points] == pytest.approx([.1, .9])
    assert [point['is_observed_no_loot_marker'] for point in points] == [True, False]
    assert [point['encounter_markers'] for point in points] == [1, 1]


def test_legacy_component_price_that_exceeds_recorded_money_is_not_reallocated_as_new_cost():
    rows = [row(1, 'combat', 0, ammo_cost=.1, repair_decay=.01),
            notice(2, 1), opening(3, 2), receipt(4, 2, .5)]
    original = build_activity_points(rows)
    points = project_team_encounters(original)
    assert points == original


def test_repair_context_does_not_loan_old_wear_charges_to_inferred_enemies(tmp_path):
    store = Store(tmp_path / 'repair-boundary.sqlite3')
    sid = store.start_session('hunt')
    rows = [use(1, 0), notice(2, 1),
            row(3, 'repair', 2, estimated_cost=.07, resets_durability=True),
            opening(4, 3), receipt(5, 3, .5)]
    for item in rows:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} |
                        {'payload': json.loads(item['payload'])})
    points = projected(rows)
    assert points == build_activity_points(rows)
    assert not any(point.get('allocated_return') is not None for point in points)
    assert store.get_session(sid).hunting_cost == pytest.approx(.17)
    assert store.get_session(sid).loot_value == .5


def test_conversion_outputs_do_not_poison_supported_personal_container_linkage():
    points = projected([use(1, 0), notice(2, 1), opening(3, 2),
                        row(4, 'loot', 2, item_name='Universal Ammo', quantity=10000,
                            value=1, is_personal=True), receipt(5, 2, .5)])
    assert len(points) == 1
    assert points[0]['loot'] == .5
    assert points[0]['encounter_receipt_ids'] == (5,)


def test_receipt_admission_cannot_bypass_the_source_event_span_bound():
    from ped_hunter.team_encounters import MAX_EVENT_SPAN
    raw = build_activity_points([use(1, 0), notice(2, 1), opening(3, 2),
                                 receipt(MAX_EVENT_SPAN + 2, 2, .5)])
    assert raw.team_encounters[0]['status'] == 'interrupted_limit'
    assert not raw.team_encounters[0]['receipt_ids']
    points = project_team_encounters(raw)
    assert math.fsum(point['loot'] or 0 for point in points) == .5
    assert not any(point.get('allocated_return') is not None for point in points)


def test_real_latest_nonmonetary_source_row_settles_batch_without_a_fabricated_completion(tmp_path):
    from ped_hunter.app import _kill_trend_points
    store = Store(tmp_path / 'source-progress.sqlite3')
    sid = store.start_session('hunt')
    for item in [use(1, 0), notice(2, 1), opening(3, 2), receipt(4, 2, .5)]:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} |
                        {'payload': json.loads(item['payload'])})
    builder = ActivityTrendBuilder()
    partial = project_team_encounters(_kill_trend_points(store, sid, builder=builder))
    assert partial[0]['loot'] == .5 and partial[0]['profit'] is None
    assert partial[0]['encounter_return_pending']
    progress = row(5, 'equipment', 4, item_damaged=True)
    store.add_event(sid, {key: value for key, value in progress.items() if key != 'id'} |
                    {'payload': json.loads(progress['payload'])})
    with store.connect() as conn:
        original = [tuple(item) for item in conn.execute('SELECT * FROM events ORDER BY id')]
    settled = project_team_encounters(_kill_trend_points(store, sid, builder=builder))
    assert settled[0]['profit'] == .4
    assert settled[0]['encounter_batch_closed'] and not settled[0]['encounter_return_pending']
    assert project_team_encounters(_kill_trend_points(store, sid, builder=builder)) == settled
    assert store.get_session(sid).hunting_cost == .1
    assert store.get_session(sid).loot_value == .5
    with store.connect() as conn:
        assert [tuple(item) for item in conn.execute('SELECT * FROM events ORDER BY id')] == original


def test_closed_recording_can_finalize_observed_batch_but_not_invent_missing_payout():
    raw = build_activity_points([use(1, 0), notice(2, 1), opening(3, 2), receipt(4, 2, .5)])
    assert project_team_encounters(raw)[0]['profit'] is None
    assert project_team_encounters(raw, session_closed=True)[0]['profit'] == .4
    no_receipt = build_activity_points([use(1, 0), notice(2, 1), opening(3, 2)])
    assert project_team_encounters(no_receipt, session_closed=True)[0]['profit'] is None


def test_later_booked_average_and_genuine_fans_follow_expanded_groups_without_repricing(tmp_path):
    from ped_hunter.app import _kill_trend_points
    store = Store(tmp_path / 'average-fans.sqlite3')
    sid = store.start_session('hunt')
    rows = [use(1, 0), notice(2, 1), use(3, 2), notice(4, 3), use(5, 4), notice(6, 5),
            opening(7, 6), receipt(8, 6, .3), use(9, 10), receipt(10, 11, .2),
            use(11, 14), receipt(12, 15, .1)]
    for item in rows:
        store.add_event(sid, {key: value for key, value in item.items() if key != 'id'} |
                        {'payload': json.loads(item['payload'])})
    with store.connect() as conn:
        original = [tuple(item) for item in conn.execute('SELECT * FROM events ORDER BY id')]
    old = ((.1, .2, .2),)
    store.save_chart_trails(sid, {9: old})  # Genuine prior raw-group interpretation.
    raw = _kill_trend_points(store, sid)
    points = project_team_encounters(raw)
    assert len(points) == 5
    assert [point['avg_cost'] for point in points] == pytest.approx([.1] * 5)
    assert 9 in points.encounter_changed_ids
    histories = TrailRecorder(store).update(sid, points, True, revised_event_ids=points.encounter_changed_ids)
    assert histories[9] == (*old, (.1, pytest.approx(.1), .2))
    assert store.load_chart_trails(sid, [2, 4, 6]) == {}  # No missing past is invented.
    assert store.get_session(sid).hunting_cost == pytest.approx(.5)
    assert store.get_session(sid).loot_value == pytest.approx(.6)
    assert raw[-2]['avg_cost'] == pytest.approx(.2)  # Original projection was not mutated.
    with store.connect() as conn:
        assert [tuple(item) for item in conn.execute('SELECT * FROM events ORDER BY id')] == original
