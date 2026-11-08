"""Local axes preserve ordinary variation without hiding any event or fan."""
from copy import deepcopy

import pytest

from ped_hunter.chart_scales import plan_chart_scales


def points():
    returns = [0.01, 0.025, 0.04, 0.07, 0.015, 0.06, 0.035, 0.08,
               0.02, 0.055, 0.03, 0.065, 0.012, 0.075, 0.045, 0.025]
    return [{'event_id': i + 1, 'cost': 0.05, 'avg_cost': 0.19, 'loot': value}
            for i, value in enumerate(returns)]


@pytest.mark.parametrize('value', [1.11, 600, 0.00111])
def test_relative_compression_separates_the_event_while_it_is_visible(value):
    data = points()
    factor = 0.001 if value < 0.01 else 1
    for point in data:
        for key in ('cost', 'avg_cost', 'loot'):
            point[key] *= factor
    data[3]['loot'] = value
    original = deepcopy(data)
    plan = plan_chart_scales(data, {})
    assert plan.local_indices == {3}
    assert plan.ordinary.low == 0
    assert plan.ordinary.high == pytest.approx(0.19 * factor)
    assert plan.events[3].high == value
    assert plan.events[3].fraction(value) == 1
    # Ordinary return variation uses over a third of the vertical plot rather
    # than being flattened by the still-visible exceptional event.
    assert plan.ordinary.fraction(0.08 * factor) - plan.ordinary.fraction(0.01 * factor) > 0.35
    assert len(plan.events) == len(data)
    assert data == original


def test_no_outlier_keeps_one_shared_range():
    data = points()
    plan = plan_chart_scales(data, {})
    assert not plan.local_indices
    assert all(axis is plan.ordinary for axis in plan.events)
    assert plan.ordinary.high == 0.19
    assert all(plan.connected(i, i + 1) for i in range(15))


@pytest.mark.parametrize('indices', [(2, 8, 13), (7, 8, 9), tuple(range(4, 16))])
def test_multiple_and_adjacent_outliers_each_retain_their_own_scale(indices):
    data = points()
    for index, value in zip(indices, [1.11, 600, 2.25] * 4):
        data[index]['loot'] = value
    plan = plan_chart_scales(data, {})
    assert plan.local_indices == set(indices)
    assert plan.ordinary.high == 0.19
    for index in indices:
        assert plan.events[index].high == data[index]['loot']
        if index:
            assert not plan.connected(index - 1, index)
        if index < 15:
            assert not plan.connected(index, index + 1)


def test_loss_outlier_does_not_turn_cost_into_return_or_remove_negative_profit():
    data = points()
    data[7]['cost'] = 1.11
    plan = plan_chart_scales(data, {})
    assert plan.local_indices == {7}
    assert plan.events[7].high == 1.11
    assert data[7]['loot'] - data[7]['cost'] < 0
    assert plan.events[7].fraction(data[7]['loot']) < plan.events[7].fraction(data[7]['cost'])


def test_retained_fan_extreme_keeps_its_event_local_even_after_reassignment():
    data = points()
    history = {8: ((0.01, 0.19, 0), (1.11, 0.19, 0.03), (0.05, 0.19, 0.03)),
               999: ((600, 600, 600),)}
    original = deepcopy(history)
    plan = plan_chart_scales(data, history)
    assert plan.local_indices == {7}
    assert plan.events[7].high == 1.11
    assert plan.ordinary.high == 0.19
    assert history == original


def test_equal_high_values_do_not_fabricate_an_ordinary_cohort():
    data = points()
    for point in data:
        point['loot'] = 600
    plan = plan_chart_scales(data, {})
    assert not plan.local_indices
    assert plan.ordinary.high == 600


def test_empty_unknown_and_zero_data_have_a_nonzero_range():
    assert plan_chart_scales([], {}).ordinary.high == 1
    data = [{'event_id': 1, 'cost': None, 'avg_cost': None, 'loot': 0}]
    plan = plan_chart_scales(data, {})
    assert plan.ordinary.low == 0 and plan.ordinary.high == 1
    assert not plan.local_indices


def test_signed_extents_and_finite_extreme_values_remain_projectable():
    data = points()
    data[3]['loot'] = -1.11
    plan = plan_chart_scales(data, {})
    assert plan.events[3].low == -1.11
    assert plan.events[3].fraction(-1.11) == 0
    assert 0 < plan.events[3].fraction(0) < 1
    assert plan.events[3].fraction(0.19) == 1
    data[3].update(cost=1e308, loot=-1e308)
    axis = plan_chart_scales(data, {}).events[3]
    assert axis.fraction(-1e308) == 0
    assert axis.fraction(0) == 0.5
    assert axis.fraction(1e308) == 1
