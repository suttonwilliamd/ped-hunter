"""Approximate neighboring intervals may support a bounded shared cost, not a target identity."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ped_hunter.activity_trend import ActivityPoints
from ped_hunter.hunt_attribution import HuntAttribution
from test_hunt_attribution import HuntStream, MemoryStore, pooled, assert_conserved


def variable_neighbors(stream):
    for count in (8, 2, 1, 6, 4):
        stream.shots(count)
        stream.receipt()


def test_preceding_variable_neighbors_support_conserved_approximate_share_and_reload():
    stream = HuntStream()
    variable_neighbors(stream)
    anchor = stream.shots(5)
    stream.receipt()
    latest = stream.receipt()
    members = pooled(stream.points, [anchor, latest])
    assert [p['cost'] for p in members] == pytest.approx([.25, .25])
    assert all(p['cost_basis'] == 'neighbors' for p in members)
    assert all(p['cost_pool_id'] == anchor and p['cost_pool_total'] == pytest.approx(.5) for p in members)
    support = members[-1]['cost_support']
    assert support['low'] == pytest.approx(.2)
    assert support['high'] == pytest.approx(.6)
    assert len(support['event_ids']) == 5
    assert not {anchor, latest} & set(support['event_ids'])
    rows, points = deepcopy(stream.rows), deepcopy(stream.points)
    stream.engine = HuntAttribution(stream.store)
    stream.refresh()
    assert stream.points == points and stream.rows == rows
    writes = stream.store.writes
    stream.refresh()
    assert stream.store.writes == writes


def test_following_neighbors_revise_recent_unknowns_but_two_samples_are_insufficient():
    stream = HuntStream()
    anchor = stream.shots(5)
    stream.receipt()
    latest = stream.receipt()
    for count in (2, 4):
        stream.shots(count)
        stream.receipt()
    assert all(p['cost'] is None for p in pooled(stream.points, [anchor, latest]))
    stream.shots(3)
    stream.receipt()
    # The third interval becomes independent closed evidence when the next
    # observed use opens a new interval; the pending use is not a reference.
    stream.shots(1)
    members = pooled(stream.points, [anchor, latest])
    assert [p['cost'] for p in members] == pytest.approx([.25, .25])
    assert all(p['cost_basis'] == 'neighbors' for p in members)
    assert {anchor, latest} <= stream.points.attribution_changed_ids
    assert all(event_id > latest for event_id in members[-1]['cost_support']['event_ids'])


def test_neighbor_support_does_not_cross_an_equipment_boundary():
    stream = HuntStream()
    variable_neighbors(stream)
    stream.loadout = 'Other selected equipment'
    anchor = stream.shots(5)
    stream.receipt()
    latest = stream.receipt()
    assert all(p['cost'] is None for p in pooled(stream.points, [anchor, latest]))


def test_neighbor_band_does_not_spend_input_that_is_not_in_the_pool():
    stream = HuntStream()
    variable_neighbors(stream)
    anchor = stream.shots(1)
    stream.receipt()
    latest = stream.receipt()
    assert all(p['cost'] is None for p in pooled(stream.points, [anchor, latest]))
    assert stream.points.attribution_pending == pytest.approx(.1)


@pytest.mark.parametrize('batched', [False, True])
def test_current_k995_interval_copy_uses_actual_five_use_budget_not_an_invented_neighbor_charge(batched):
    fixture = json.loads((Path(__file__).parent / 'fixtures/hunt_attribution_current_k995.json').read_text(encoding='utf-8'))
    points = fixture['points']
    original = deepcopy(points)
    store = MemoryStore()
    engine = HuntAttribution(store)
    engine.update('current-copy', ActivityPoints(), live=True)
    for end in ([len(points)] if batched else range(1, len(points) + 1)):
        raw = ActivityPoints()
        raw.extend(deepcopy(points[:end]))
        raw.last_event_id = raw[-1]['last_event_id']
        projected = engine.update('current-copy', raw, live=True)
        assert_conserved(raw, projected)
    pair = [next(p for p in projected if p['event_id'] == event_id) for event_id in (304795, 304816)]
    assert [p['cost'] for p in pair] == pytest.approx([.0455, .0455])
    assert all(p['cost_basis'] == 'neighbors' for p in pair)
    assert pair[0]['raw_cost'] == pytest.approx(.091)
    assert pair[1]['raw_cost'] == 0
    assert sum(p['cost'] for p in pair) == pytest.approx(.091)
    assert points == original
    snapshot = deepcopy(projected)
    assert HuntAttribution(store).update('current-copy', raw, live=True) == snapshot


def test_neighbor_revision_commits_raw_provenance_and_existing_fan_together(tmp_path):
    from ped_hunter.chart_trails import TrailRecorder
    from test_hunt_attribution_storage import DatabaseHunt

    hunt = DatabaseHunt(tmp_path / 'neighbors.sqlite3')
    variable_neighbors(hunt)
    anchor = hunt.shots(5)
    recorder = TrailRecorder(hunt.store)
    recorder.update(hunt.sid, hunt.points, live=True)
    hunt.receipt()
    old_fan = deepcopy(hunt.store.load_chart_trails(hunt.sid, [anchor])[anchor])
    latest = hunt.event('loot', advance=2, refresh=False, value=1.5,
                        item_name='Animal Muscle Oil', is_personal=True)
    originals = hunt.originals()
    hunt.refresh()
    assert [p['cost'] for p in pooled(hunt.points, [anchor, latest])] == pytest.approx([.25, .25])
    fans = hunt.store.load_chart_trails(hunt.sid, [anchor, latest])
    assert set(fans) == {anchor}
    assert fans[anchor][:len(old_fan)] == old_fan
    assert fans[anchor][-1][0] == pytest.approx(.25)
    state, decisions = hunt.store.load_hunt_attribution(hunt.sid)
    assert decisions[latest]['cost_basis'] == 'neighbors'
    assert len(decisions[latest]['cost_support']['event_ids']) == 5
    assert hunt.originals() == originals
    recorder.update(hunt.sid, hunt.points, live=True,
                    revised_event_ids=hunt.points.attribution_changed_ids)
    observed_fans = hunt.store.load_chart_trails(hunt.sid, [anchor, latest])
    assert observed_fans[anchor] == fans[anchor]
    # The latest event is now genuinely observed; this is not an invented
    # historical trail for the previously unseen second receipt.
    assert observed_fans[latest] == ((.25, hunt.points[-1]['avg_cost'], 1.5),)
    restored = HuntAttribution(hunt.store).update(hunt.sid, hunt.builder.snapshot(), live=True)
    assert restored == hunt.points
    assert hunt.store.load_hunt_attribution(hunt.sid) == (state, decisions)
    assert hunt.originals() == originals
