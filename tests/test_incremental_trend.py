"""Live refresh must process only the append, retaining partial receipt state."""
import json

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder, build_activity_points
from ped_hunter.app import _kill_trend_points
from ped_hunter.storage import Store


def history():
    events = [
        ('combat', 0, {'damage': 4, 'shot_cost': 0.2}),
        ('loot', 1, {'value': 0.4}),
        ('manufacturing_offset', None, {'attempts_total': 1, 'cost_per_attempt': 0.7, 'total_cost': 0.7}),
        ('loot', 5, {'value': 0.6}),
        ('loot', 6, {'value': 0.24}),
        ('loot', 6, {'value': 0.0018}),
        ('loot', 9, {'value': 0.3}),  # Unpriced excess group.
        ('manufacturing_offset', None, {'attempts_total': 2, 'cost_per_attempt': 1.2, 'total_cost': 2.4}),
        ('craft', 10, {'result': 'fail'}),
        ('combat', 11, {'damage_taken': 3}),
        ('loot', 11, {'value': 0.2}),
        ('combat', 15, {'damage': 2, 'shot_cost': 0.1}),
        ('combat', 16, {'damage': 3, 'shot_cost': 0.2}),
        ('loot', 17, {'value': 0.5}),
        ('loot', 17, {'value': 0.1, 'item_name': 'Shrapnel'}),
    ]
    return [dict(id=i, kind=kind, timestamp=None if sec is None else f'2026-01-01T10:00:{sec:02d}',
                 payload=json.dumps(payload))
            for i, (kind, sec, payload) in enumerate(events, 1)]


def assert_snapshot(actual, expected):
    assert actual == expected
    assert actual.manufacturing == expected.manufacturing
    assert actual.unknown_craft_groups == expected.unknown_craft_groups
    assert actual.has_crafting == expected.has_crafting


@pytest.mark.parametrize('batch_size', [1, 2, 3, 7, 15])
def test_every_prefix_matches_full_replay_and_old_snapshots_are_stable(batch_size):
    rows = history()
    builder = ActivityTrendBuilder()
    snapshots = []
    for start in range(0, len(rows), batch_size):
        end = min(start + batch_size, len(rows))
        builder.extend(rows[start:end])
        snapshot = builder.snapshot()
        snapshots.append((end, snapshot))
        assert_snapshot(snapshot, build_activity_points(rows[:end]))
        assert_snapshot(builder.snapshot(), snapshot)  # Idle polls cannot finalize a group.
    for end, snapshot in snapshots:
        assert_snapshot(snapshot, build_activity_points(rows[:end]))


def test_database_refresh_reads_only_new_rows_and_merges_split_receipt(tmp_path):
    store = Store(tmp_path / 'incremental.sqlite3')
    sid = store.start_session('hunt')
    for row in history()[:4]:
        store.add_event(sid, dict(row, payload=json.loads(row['payload']), raw_message=str(row['id'])))
    builder = ActivityTrendBuilder()
    first = _kill_trend_points(store, sid, 16, builder)
    assert first[-1]['loot'] == pytest.approx(0.6)
    assert builder.last_event_id == 4
    for row in history()[4:6]:
        store.add_event(sid, dict(row, payload=json.loads(row['payload']), raw_message=str(row['id'])))
    observed_rows = []
    extend = builder.extend
    def observe(rows):
        rows = list(rows)
        observed_rows.extend(row['id'] for row in rows)
        extend(rows)
    builder.extend = observe
    second = _kill_trend_points(store, sid, 16, builder)
    assert observed_rows == [5, 6]
    assert second[-1]['loot'] == pytest.approx(0.8418)
    assert second[-1]['cost'] == pytest.approx(0.7)
    assert first[-1]['loot'] == pytest.approx(0.6)
    assert_snapshot(second, _kill_trend_points(store, sid))
    observed_rows.clear()
    assert_snapshot(_kill_trend_points(store, sid, 16, builder), second)
    assert observed_rows == []
