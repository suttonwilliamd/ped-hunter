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


@pytest.mark.parametrize('indices', [(2, 8, 13), (7, 8, 9)])
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


@pytest.mark.parametrize('field', ['loot', 'cost'])
def test_prevalent_elevated_cohort_is_ordinary_but_its_jackpot_is_not(field):
    data = points()
    for index in range(5, 16):
        data[index][field] = 1 + .03 * (index - 5)
    original = deepcopy(data)
    common = plan_chart_scales(data, {})
    assert not common.local_indices
    assert common.ordinary.high == pytest.approx(1.3)
    assert all(common.connected(index, index + 1) for index in range(15))
    data[15][field] = 600
    jackpot = plan_chart_scales(data, {}, previous=common)
    assert jackpot.local_indices == {15}
    assert jackpot.ordinary.high == pytest.approx(1.27)
    assert jackpot.events[15].high == 600
    assert not jackpot.connected(14, 15)
    assert data[:15] == original[:15]
    assert data[15] == dict(original[15], **{field: 600})


def test_mixed_prevalent_cohorts_do_not_hide_the_separate_jackpot_cluster():
    data = points()
    for index, value in zip(range(4, 16), [1.11, 600, 2.25] * 4):
        data[index]['loot'] = value
    plan = plan_chart_scales(data, {})
    assert plan.local_indices == {5, 8, 11, 14}
    assert plan.ordinary.high == 2.25
    for index in plan.local_indices:
        assert plan.events[index].high == 600
        assert not plan.connected(index - 1, index)
        assert not plan.connected(index, index + 1)


def test_fan_envelopes_vote_once_per_event_not_once_per_recorded_sample():
    data = points()
    one = {8: ((.05, .19, .03), (1.11, .19, .03))}
    many = {8: one[8] * 100}
    assert plan_chart_scales(data, one) == plan_chart_scales(data, many)
    assert plan_chart_scales(data, many).local_indices == {7}
    histories = {point['event_id']: one[8] for point in data[5:]}
    original = deepcopy(histories)
    common = plan_chart_scales(data, histories)
    assert not common.local_indices
    assert common.ordinary.high == 1.11
    histories[16] = (*histories[16], (600, .19, .03))
    exceptional = plan_chart_scales(data, histories, previous=common)
    assert exceptional.local_indices == {15}
    assert exceptional.ordinary.high == 1.11
    assert histories[8] == original[8]
    assert histories[16][:-1] == original[16]


def test_evolving_window_adopts_a_cohort_and_readapts_as_it_leaves():
    window = points()
    plan = plan_chart_scales(window, {})
    entered = []
    for event_id in range(17, 28):
        window = [*window[1:], dict(event_id=event_id, cost=.05, avg_cost=.19, loot=1.11)]
        plan = plan_chart_scales(window, {}, previous=plan)
        entered.append(len(plan.local_indices))
    assert entered[:4] == [1, 2, 3, 4]
    assert entered[4:] == [0] * 7
    left = []
    for event_id in range(28, 44):
        window = [*window[1:], dict(event_id=event_id, cost=.05, avg_cost=.19, loot=.04)]
        plan = plan_chart_scales(window, {}, previous=plan)
        left.append(len(plan.local_indices))
    # Four remaining elevated observations do not chatter back into local axes
    # immediately; genuinely sparse remnants regain their independent scales.
    assert left[11] == 0
    assert left[12:15] == [3, 2, 1]
    assert not plan.local_indices
    assert plan.ordinary.high == .19


def test_small_live_changes_do_not_chatter_at_the_relative_gap_boundary():
    data = points()
    data[7]['loot'] = .58
    plan = plan_chart_scales(data, {})
    assert plan.local_indices == {7}
    for value in (.56, .58, .55, .57, .56):
        data[7]['loot'] = value
        plan = plan_chart_scales(data, {}, previous=plan)
        assert plan.local_indices == {7}
    data[7]['loot'] = .37
    plan = plan_chart_scales(data, {}, previous=plan)
    assert not plan.local_indices
    data[7]['loot'] = .55
    plan = plan_chart_scales(data, {}, previous=plan)
    assert not plan.local_indices
    data[7]['loot'] = .58
    assert plan_chart_scales(data, {}, previous=plan).local_indices == {7}


def test_previous_classification_is_bound_to_event_identity_not_chronological_slot():
    data = points()
    data[7]['loot'] = .58
    previous = plan_chart_scales(data, {})
    shifted = [*data[1:], dict(event_id=17, cost=.05, avg_cost=.19, loot=.04)]
    shifted[6]['loot'] = .56
    moved = plan_chart_scales(shifted, {}, previous=previous)
    assert moved.local_indices == {6}
    unrelated = [dict(point, event_id=point['event_id'] + 100) for point in shifted]
    assert not plan_chart_scales(unrelated, {}, previous=moved).local_indices


@pytest.mark.parametrize('factor', [1e-100, 1, 1e100])
def test_distribution_detection_is_unit_invariant_and_keeps_signed_fans(factor):
    data = points()
    for index in range(5, 16):
        data[index]['loot'] = -1.11
    data[15]['loot'] = -600
    for point in data:
        for key in ('cost', 'avg_cost', 'loot'):
            point[key] *= factor
    histories = {16: ((.05 * factor, .19 * factor, -600 * factor),
                      (.05 * factor, .19 * factor, -1.11 * factor))}
    original = deepcopy((data, histories))
    plan = plan_chart_scales(data, histories)
    assert plan.local_indices == {15}
    assert plan.ordinary.low == -1.11 * factor
    assert plan.events[15].low == -600 * factor
    assert plan.events[15].fraction(-600 * factor) == 0
    assert plan.ordinary.fraction(-1.11 * factor) < plan.ordinary.fraction(.05 * factor)
    assert (data, histories) == original
