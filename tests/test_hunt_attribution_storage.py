"""Hunting projection persistence is additive, atomic, and session-owned."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math

import pytest

from ped_hunter.activity_trend import ActivityTrendBuilder
from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.storage import LoadoutRecord, Store


class DatabaseHunt:
    def __init__(self, path):
        self.store = Store(path)
        loadout = LoadoutRecord(None, 'Persistence carbine', 'Test carbine', cost_per_shot=0.1)
        loadout_id = self.store.save_loadout(loadout, make_active=True)
        self.loadout = self.store.get_loadout(loadout_id)
        self.sid = self.store.start_session('hunt', loadout=self.loadout)
        self.builder = ActivityTrendBuilder()
        self.engine = HuntAttribution(self.store)
        self.clock = 0.0
        self.points = self.engine.update(self.sid, self.builder.snapshot(), live=True)

    def event(self, kind, *, advance=0.01, refresh=True, **payload):
        self.clock += advance
        timestamp = (datetime(2026, 1, 1, 10) + timedelta(seconds=self.clock)).isoformat(timespec='microseconds')
        self.store.add_event(self.sid, {'kind': kind, 'timestamp': timestamp,
                                      'raw_message': 'isolated hunting attribution regression',
                                      'payload': payload})
        with self.store.connect() as conn:
            row = conn.execute('SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT 1',
                               (self.sid,)).fetchone()
        self.builder.extend([row])
        if refresh:
            self.refresh()
        return row['id']

    def refresh(self, *, live=True):
        raw = self.builder.snapshot()
        self.points = self.engine.update(self.sid, raw, live=live)
        assert sum(p['cost'] or 0 for p in self.points if p['kind'] == 'hunt') + self.points.attribution_pending == pytest.approx(
            sum(p['cost'] or 0 for p in raw if p['kind'] == 'hunt'), abs=1e-10)
        assert [p['event_id'] for p in self.points] == [p['event_id'] for p in raw]
        return self.points

    def shots(self, count):
        first_id = None
        for _ in range(count):
            event_id = self.event('combat', refresh=False, damage=4, shot_cost=0.1,
                                 ammo_cost=0.09, repair_decay=0.01, loadout=self.loadout.name)
            if first_id is None:
                first_id = event_id
        self.refresh()
        return first_id

    def receipt(self):
        return self.event('loot', advance=2, value=1.5, item_name='Animal Muscle Oil', is_personal=True)

    def pool(self):
        for _ in range(6):
            self.shots(10)
            self.receipt()
        first_id = self.shots(90)
        self.receipt()
        second_id = self.receipt()
        return first_id, second_id

    def originals(self):
        with self.store.connect() as conn:
            events = [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
            loadouts = [tuple(row) for row in conn.execute('SELECT * FROM loadouts ORDER BY id')]
        return self.store.get_session(self.sid), events, loadouts


@pytest.fixture
def hunt(tmp_path):
    return DatabaseHunt(tmp_path / 'hunt-attribution.sqlite3')


def test_sqlite_roundtrip_keeps_pending_stable_ids_raw_totals_and_loadout(hunt):
    first_id, second_id = hunt.pool()
    before = hunt.originals()
    snapshot = deepcopy(hunt.points)
    state, decisions = hunt.store.load_hunt_attribution(hunt.sid)
    assert state is not None
    assert {first_id, second_id} <= decisions.keys()
    assert hunt.points.attribution_pending == pytest.approx(7)
    reopened = Store(hunt.store.db_path)
    loaded = HuntAttribution(reopened).update(hunt.sid, hunt.builder.snapshot(), live=False)
    assert loaded == snapshot
    assert loaded.attribution_pending == snapshot.attribution_pending
    assert loaded.attribution_active_pending == snapshot.attribution_active_pending
    assert reopened.load_hunt_attribution(hunt.sid) == (state, decisions)
    assert hunt.originals() == before
    assert before[0].hunting_cost == pytest.approx(15)
    assert before[0].loadout_snapshot['name'] == 'Persistence carbine'
    # Resume from saved boundary and finish the same nine observed receipt groups.
    hunt.store = reopened
    hunt.engine = HuntAttribution(reopened)
    ids = [first_id, second_id]
    for _ in range(7):
        ids.append(hunt.receipt())
    members = [p for p in hunt.points if p['event_id'] in ids]
    assert [p['event_id'] for p in members] == ids
    assert [p['cost'] for p in members] == pytest.approx([1] * 9)
    assert hunt.points.attribution_pending == pytest.approx(0)
    assert snapshot[-2]['cost_pool_total'] == pytest.approx(9)
    assert snapshot.attribution_pending == pytest.approx(7)


def test_frozen_past_and_pending_survive_reload_and_future_evidence(hunt):
    ids = hunt.pool()
    hunt.event('weapon', weapon='Another weapon')
    hunt.shots(10)
    hunt.receipt()
    frozen = deepcopy([p for p in hunt.points if p['event_id'] in ids])
    assert [p['cost'] for p in frozen] == pytest.approx([1, 1])
    assert hunt.points.attribution_pending == pytest.approx(7)
    assert hunt.points.attribution_active_pending == pytest.approx(0)
    persisted = hunt.store.load_hunt_attribution(hunt.sid)[1]
    hunt.store = Store(hunt.store.db_path)
    hunt.engine = HuntAttribution(hunt.store)
    hunt.refresh(live=False)
    assert [p for p in hunt.points if p['event_id'] in ids] == frozen
    for _ in range(6):
        hunt.shots(10)
        hunt.receipt()
    assert [p for p in hunt.points if p['event_id'] in ids] == frozen
    assert hunt.points.attribution_pending == pytest.approx(7)
    after = hunt.store.load_hunt_attribution(hunt.sid)[1]
    assert {event_id: after[event_id] for event_id in ids} == {event_id: persisted[event_id] for event_id in ids}


def test_ended_and_legacy_nonlive_sessions_do_not_seed_or_rewrite_attribution(hunt):
    hunt.shots(10)
    hunt.receipt()
    hunt.store.end_session(hunt.sid)
    saved = deepcopy(hunt.store.load_hunt_attribution(hunt.sid))
    before = hunt.originals()
    for _ in range(2):
        HuntAttribution(Store(hunt.store.db_path)).update(hunt.sid, hunt.builder.snapshot(), live=False)
    assert hunt.store.load_hunt_attribution(hunt.sid) == saved
    assert hunt.originals() == before
    legacy = hunt.store.start_session('hunt')
    hunt.store.add_event(legacy, {'kind': 'loot', 'timestamp': '2026-01-01T10:00:00',
                                 'raw_message': 'legacy receipt', 'payload': {'value': 2}})
    with hunt.store.connect() as conn:
        rows = conn.execute('SELECT * FROM events WHERE session_id = ? ORDER BY id', (legacy,)).fetchall()
    builder = ActivityTrendBuilder()
    builder.extend(rows)
    raw = builder.snapshot()
    assert HuntAttribution(hunt.store).update(legacy, raw, live=False) == raw
    assert hunt.store.load_hunt_attribution(legacy) == (None, {})


@pytest.mark.parametrize('field', ['cost', 'raw_cost', 'cost_pool_total', 'cost_unassigned'])
@pytest.mark.parametrize('invalid', [-1.0, math.nan, math.inf, -math.inf])
def test_storage_invalid_amounts_reject_the_entire_write_atomically(hunt, field, invalid):
    ids = hunt.pool()
    state, decisions = hunt.store.load_hunt_attribution(hunt.sid)
    before = deepcopy((state, decisions))
    new_watermark = hunt.event('combat', refresh=False, damage_taken=1)
    originals = hunt.originals()
    changed = deepcopy(decisions[ids[0]])
    changed[field] = invalid
    changed['attribution_revision'] += 1
    changed_state = deepcopy(state)
    # A valid state update must not sneak through when one decision is invalid.
    changed_state['watermark'] = new_watermark
    with pytest.raises(ValueError):
        hunt.store.save_hunt_attribution(hunt.sid, changed_state, {ids[0]: changed})
    assert hunt.store.load_hunt_attribution(hunt.sid) == before
    assert hunt.originals() == originals


@pytest.mark.parametrize('foreign', [True, False])
def test_foreign_or_missing_event_rejects_state_and_decisions_without_accounting_changes(hunt, foreign):
    ids = hunt.pool()
    other_sid = hunt.store.start_session('hunt')
    hunt.store.add_event(other_sid, {'kind': 'loot', 'timestamp': '2026-01-01T10:01:00',
                                    'raw_message': 'other session receipt', 'payload': {'value': 3}})
    with hunt.store.connect() as conn:
        foreign_id = conn.execute('SELECT MAX(id) FROM events WHERE session_id = ?', (other_sid,)).fetchone()[0]
    invalid_id = foreign_id if foreign else foreign_id + 100000
    state, decisions = hunt.store.load_hunt_attribution(hunt.sid)
    saved = deepcopy((state, decisions))
    originals = hunt.originals()
    invalid = deepcopy(decisions[ids[0]])
    if 'event_id' in invalid:
        invalid['event_id'] = invalid_id
    with pytest.raises(ValueError):
        hunt.store.save_hunt_attribution(hunt.sid, state, {ids[0]: decisions[ids[0]], invalid_id: invalid})
    assert hunt.store.load_hunt_attribution(hunt.sid) == saved
    assert hunt.store.load_hunt_attribution(other_sid) == (None, {})
    assert hunt.originals() == originals


@pytest.mark.parametrize('table,column', [('hunt_attribution_state', 'state'),
                                         ('hunt_attribution_decisions', 'decision')])
@pytest.mark.parametrize('corrupt', ['{not-json', '[]', 'null'])
def test_corrupt_persistence_json_is_safely_ignored_without_rewriting_originals(hunt, table, column, corrupt):
    ids = hunt.pool()
    originals = hunt.originals()
    with hunt.store.connect() as conn:
        if table == 'hunt_attribution_state':
            conn.execute(f'UPDATE {table} SET {column} = ? WHERE session_id = ?', (corrupt, hunt.sid))
        else:
            conn.execute(f'UPDATE {table} SET {column} = ? WHERE session_id = ? AND event_id = ?',
                         (corrupt, hunt.sid, ids[0]))
        conn.commit()
    state, decisions = Store(hunt.store.db_path).load_hunt_attribution(hunt.sid)
    if table == 'hunt_attribution_state':
        assert state is None
    else:
        assert ids[0] not in decisions
    assert hunt.originals() == originals


def test_incremental_decision_save_retains_untouched_frozen_records(hunt):
    first_id, second_id = hunt.pool()
    hunt.event('repair', estimated_cost=0)
    hunt.shots(10)
    hunt.receipt()
    state, decisions = hunt.store.load_hunt_attribution(hunt.sid)
    before = deepcopy(decisions)
    hunt.store.save_hunt_attribution(hunt.sid, state, {second_id: decisions[second_id]})
    assert hunt.store.load_hunt_attribution(hunt.sid) == (state, before)
    assert hunt.store.load_hunt_attribution(hunt.sid)[1][first_id] == before[first_id]


def test_additive_schema_upgrade_preserves_original_events_sessions_and_loadouts(hunt):
    hunt.shots(10)
    hunt.receipt()
    before = hunt.originals()
    with hunt.store.connect() as conn:
        conn.execute('DROP TABLE hunt_attribution_decisions')
        conn.execute('DROP TABLE hunt_attribution_state')
        conn.commit()
    reopened = Store(hunt.store.db_path)
    assert reopened.load_hunt_attribution(hunt.sid) == (None, {})
    assert hunt.originals() == before


def test_deleted_events_and_sessions_do_not_leave_applicable_attribution(hunt):
    ids = hunt.pool()
    with hunt.store.connect() as conn:
        conn.execute('DELETE FROM events WHERE id = ?', (ids[0],))
        conn.commit()
    assert ids[0] not in hunt.store.load_hunt_attribution(hunt.sid)[1]
    with hunt.store.connect() as conn:
        conn.execute('DELETE FROM events WHERE session_id = ?', (hunt.sid,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (hunt.sid,))
        conn.commit()
    assert Store(hunt.store.db_path).load_hunt_attribution(hunt.sid) == (None, {})


def test_direct_save_cannot_revise_a_frozen_past_decision(hunt):
    ids = hunt.pool()
    hunt.event('weapon', weapon='Different carbine')
    hunt.shots(10)
    hunt.receipt()
    state, decisions = hunt.store.load_hunt_attribution(hunt.sid)
    before = deepcopy((state, decisions))
    changed = deepcopy(decisions[ids[0]])
    assert changed['cost_status'].startswith('frozen_')
    changed['cost_reason'] = 'Attempted replacement of frozen evidence'
    changed['attribution_revision'] += 1
    originals = hunt.originals()
    with pytest.raises(ValueError):
        hunt.store.save_hunt_attribution(hunt.sid, state, {ids[0]: changed})
    assert hunt.store.load_hunt_attribution(hunt.sid) == before
    assert hunt.originals() == originals
