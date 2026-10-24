"""Read-only attribution of booked inputs to observed craft groups."""
import json

import pytest

from ped_hunter.activity_trend import build_activity_points


def row(kind, second=None, **payload):
    return {'kind': kind, 'timestamp': None if second is None else f'2026-01-01T10:00:{second:02d}',
            'payload': json.dumps(payload)}


def setup(attempts=3, price=0.7, blueprint='Widget Blueprint'):
    return row('manufacturing_offset', attempts_total=attempts, cost_per_attempt=price,
               total_cost=attempts * price, blueprint=blueprint)


def loot(second, value=0.6, item='Widget'):
    return row('loot', second, item_name=item, value=value, quantity=2)


def project(*rows):
    return build_activity_points(dict(r, id=i) for i, r in enumerate(rows, 1))


def test_setup_is_available_immediately_without_fabricated_attempts():
    points = project(setup(16))
    assert not points
    assert points.has_crafting
    assert points.manufacturing.per_attempt == 0.7
    assert points.manufacturing.booked_cost == pytest.approx(11.2)
    assert points.manufacturing.observed == 0


def test_receipt_lines_and_skills_share_a_single_input_charge():
    points = project(setup(), loot(1), row('skill', 1, skill='Engineering', xp=0.1),
                     loot(1, 0.24, 'Residue'), row('chat', 1, message='hi'), loot(1, 0.0018, 'Shrapnel'),
                     loot(5, 0.1, 'Returned materials'))
    assert [p['cost'] for p in points] == [0.7, 0.7]
    assert [p['loot_events'] for p in points] == [3, 1]
    assert [p['label'] for p in points] == ['C1', 'C2']
    assert points[0]['profit'] == pytest.approx(0.1418)
    assert points[1]['profit'] == pytest.approx(-0.6)
    assert points.manufacturing.observed == 2


def test_components_crossing_a_clock_second_do_not_consume_a_second_input():
    points = project(setup(), loot(1), loot(2, 0.24, 'Residue'), loot(2, 0.0018, 'Shrapnel'), loot(5))
    assert len(points) == 2
    assert points[0]['loot'] == pytest.approx(0.8418)
    assert points[0]['cost'] == 0.7
    assert points.manufacturing.observed == 2


def test_silent_failures_remain_unobserved_but_the_full_plan_stays_booked():
    points = project(setup(8), loot(1), loot(9))
    assert len(points) == 2
    assert points.manufacturing.observed == 2
    assert points.manufacturing.attempts == 8
    assert points.manufacturing.booked_cost == pytest.approx(5.6)
    assert sum(p['cost'] for p in points) == pytest.approx(1.4)


def test_do_not_apply_a_plan_to_receipts_before_its_saved_position():
    points = project(loot(1), setup(2), loot(5))
    assert points[0]['kind'] == 'hunt'
    assert points[0]['cost'] == 0
    assert points[1]['kind'] == 'craft'
    assert points[1]['cost'] == 0.7


def test_new_setup_resets_batch_price_average_and_delta_even_at_same_timestamp():
    points = project(setup(20, 0.7), loot(1), setup(1, 1.2, 'Other Blueprint'), loot(1, 1.4))
    assert len(points) == 2
    assert points[0]['cost'] == points[0]['avg_cost'] == 0.7
    assert points[1]['cost'] == points[1]['avg_cost'] == 1.2
    assert points[1]['first_point']
    assert points[1]['delta_profit'] == 0
    assert points.manufacturing.blueprint == 'Other Blueprint'
    assert points.manufacturing.observed == 1


def test_outgoing_hunting_stops_craft_attribution_and_keeps_hunting_average_separate():
    points = project(row('combat', 0, damage=3, shot_cost=0.2), loot(1, 0.3),
                     setup(10), loot(5, 0.9), row('combat', 9, damage=4, shot_cost=0.3), loot(10, 0.4))
    assert [p['label'] for p in points] == ['K1', 'C1', 'K2']
    assert [p['cost'] for p in points] == [0.2, 0.7, 0.3]
    assert points[-1]['avg_cost'] == pytest.approx(0.25)
    assert points[-1]['delta_profit'] == pytest.approx(0)
    assert not points.manufacturing.active
    assert points.manufacturing.observed == 1


def test_incoming_damage_and_healing_do_not_consume_or_end_a_crafting_plan():
    points = project(setup(), row('combat', 0, damage_taken=4), row('combat', 0, healed=2), loot(1))
    assert len(points) == 1
    assert points[0]['kind'] == 'craft'
    assert points[0]['cost'] == 0.7
    assert points.manufacturing.active


def test_explicit_craft_notices_and_output_are_not_counted_twice():
    points = project(setup(), row('manufacturing_allocation', input_cost=0.7),
                     row('craft', 1, result='success', input_cost=0.7), loot(2), loot(2, 0.1, 'Residue'),
                     row('craft', 5, result='fail', input_cost=0.7))
    assert len(points) == 2
    assert points.manufacturing.observed == 2
    assert [p['cost'] for p in points] == [0.7, 0.7]
    assert points[-1]['loot'] == 0
    assert points[-1]['profit'] == -0.7
    assert not points[0]['inferred']


def test_excess_groups_have_unknown_cost_not_free_profit():
    points = project(setup(1), loot(1), loot(5, 0.9))
    assert points[0]['cost'] == 0.7
    assert points[1]['cost'] is None
    assert points[1]['profit'] is None
    assert points[1]['avg_cost'] is None
    assert points.unknown_craft_groups == 1
    assert points.manufacturing.observed == 2


def test_explicit_crafting_without_a_setup_is_unpriced():
    points = project(row('craft', 1, result='success'), loot(1))
    assert points.has_crafting
    assert points[0]['profit'] is None


def test_conversion_outputs_do_not_use_up_crafting_inputs():
    points = project(setup(1), loot(1, 4, 'Universal Ammo'), loot(2, 4, 'Oil'),
                     loot(3, 4, 'Lysterium Ingot'), loot(5))
    assert len(points) == 1
    assert points.manufacturing.observed == 1
    assert points[0]['loot'] == 0.6


@pytest.mark.parametrize('bad', ['garbage', 'null', '[]', '17'])
def test_malformed_payloads_do_not_drop_later_valid_groups(bad):
    points = project(dict(row('manufacturing_offset'), payload=bad), setup(), loot(1))
    assert len(points) == 1
    assert points[0]['cost'] == 0.7


@pytest.mark.parametrize('bad', [-1, 'NaN', 'Infinity'])
def test_invalid_price_does_not_become_zero_cost_profit(bad):
    invalid = row('manufacturing_offset', attempts_total=1, cost_per_attempt=bad, total_cost=bad)
    points = project(invalid, loot(1))
    assert points[0]['cost'] is None
    assert points[0]['profit'] is None


def test_legacy_total_and_attempt_count_can_supply_per_attempt_price():
    points = project(row('manufacturing_offset', attempts=4, total_cost=2.8, blueprint='Old setup'), loot(1))
    assert points[0]['cost'] == pytest.approx(0.7)
    assert points.manufacturing.attempts == 4
