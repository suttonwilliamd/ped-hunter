from __future__ import annotations

from ped_hunter.storage import LoadoutRecord, Store


def test_add_event_updates_cached_repair_summary_and_resets_on_repair(tmp_path) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    session_id = store.start_session()

    store.add_event(
        session_id,
        {
            "kind": "combat",
            "raw_message": "shot",
            "payload": {"shot_cost": 0.1, "repair_decay": 0.01},
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "combat",
            "raw_message": "shot",
            "payload": {"shot_cost": 0.1, "repair_decay": 0.02},
        },
    )

    session = store.get_session(session_id)
    assert session is not None
    assert session.repair_shots == 2
    assert session.repair_decay == 0.03

    store.add_event(
        session_id,
        {
            "kind": "repair",
            "raw_message": "Item(s) repaired successfully",
            "payload": {"resets_durability": True, "estimated_cost": 0.03},
        },
    )

    session = store.get_session(session_id)
    assert session is not None
    assert session.repair_shots == 0
    assert session.repair_decay == 0.0


def test_recent_sessions_use_cached_repair_summary_instead_of_reconciling_events(tmp_path) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    session_id = store.start_session()

    store.add_event(
        session_id,
        {
            "kind": "combat",
            "raw_message": "shot",
            "payload": {"shot_cost": 0.1, "repair_decay": 0.01},
        },
    )

    with store.connect() as conn:
        conn.execute(
            "UPDATE sessions SET repair_shots = ?, repair_decay = ? WHERE id = ?",
            (7, 0.77, session_id),
        )
        conn.commit()

    recent = store.list_recent_sessions(1)
    assert len(recent) == 1
    assert recent[0].session_id == session_id
    assert recent[0].repair_shots == 7
    assert recent[0].repair_decay == 0.77

    all_sessions = store.list_all_sessions()
    assert len(all_sessions) == 1
    assert all_sessions[0].repair_shots == 7
    assert all_sessions[0].repair_decay == 0.77


def test_session_loadout_names_are_sanitized_to_one_line(tmp_path) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    loadout = LoadoutRecord(
        id=None,
        name="Starter rifle\nline 2 of a deliberately tall streamer loadout",
        weapon="Frontier Hunting Rifle\nmore junk",
    )
    session_id = store.start_session("hunt", loadout)

    session = store.get_session(session_id)
    assert session is not None
    assert session.loadout_name == "Starter rifle"
    assert session.loadout_snapshot is not None
    assert session.loadout_snapshot["name"] == "Starter rifle"
    assert session.loadout_snapshot["weapon"] == "Frontier Hunting Rifle"


# --- saved run totals, incremental durability counters, held connections --------
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from ped_hunter import storage


def _shot(stamp: str, cost: float = 0.1, decay: float = 0.01) -> dict:
    return {"kind": "combat", "timestamp": stamp, "raw_message": f"shot {stamp}",
            "payload": {"shot_cost": cost, "repair_decay": decay, "damage": 5.0}}


def _loot(stamp: str, value: float) -> dict:
    return {"kind": "loot", "timestamp": stamp, "raw_message": f"loot {stamp}",
            "payload": {"item_name": "Shrapnel", "value": value}}


def _replayed_durability(store: Store, session_id: str) -> tuple:
    """What the full replay would store; rolled back, so the run is unchanged."""
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        store._recompute_session_repair_state(conn, session_id)
        row = conn.execute("SELECT repair_shots, repair_decay FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return tuple(row)


def test_saved_totals_follow_appends_exclusions_foreign_edits_and_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "_TOTALS_SAVE_EVERY", 2)  # exercise saved totals plus a delta
    db = tmp_path / "ped-hunter.sqlite3"
    store = Store(db)
    session_id = store.start_session()
    ids = []
    for index, value in enumerate((1.5, 2.25, 4.0)):
        store.add_event(session_id, _shot(f"2026-01-01T00:00:{index:02d}"))
        store.add_event(session_id, _loot(f"2026-01-01T00:00:{index:02d}", value))
        summary = store.get_session(session_id)  # a read per append, as live ticks do
        with store.connect() as conn:
            ids.append(conn.execute("SELECT MAX(id) FROM events").fetchone()[0])
    assert (summary.events, summary.loot_value) == (6, 7.75)
    assert summary.hunting_cost == pytest.approx(0.3)
    assert store.list_recent_sessions(5)[0] == summary
    assert store.get_last_contributed_session().session_id == session_id

    assert store.set_loot_excluded(session_id, ids[1], True)
    excluded = store.get_session(session_id)
    assert (excluded.events, excluded.loot_value, excluded.accounting_revision) == (5, 5.5, 1)
    assert store.set_loot_excluded(session_id, ids[1], False)
    assert store.get_session(session_id).loot_value == 7.75

    # Another writer (an older build, a repair script) edits and deletes stored rows.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE events SET payload = ? WHERE id = ?",
                     (json.dumps({"item_name": "Shrapnel", "value": 10.0}), ids[0]))
    assert store.get_session(session_id).loot_value == 16.25
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (ids[2],))
    edited = store.get_session(session_id)
    assert (edited.events, edited.loot_value) == (5, 12.25)
    assert Store(db).get_session(session_id) == edited  # reopened: saved totals agree
    assert Store(db).lifetime_totals().total_loot == 12.25


def test_durability_counters_match_a_full_replay_for_out_of_order_and_edited_history(tmp_path) -> None:
    db = tmp_path / "ped-hunter.sqlite3"
    store = Store(db)
    session_id = store.start_session()

    def check(expected_shots: int) -> None:
        with store.connect() as conn:
            stored = tuple(conn.execute(
                "SELECT repair_shots, repair_decay FROM sessions WHERE id = ?", (session_id,)).fetchone())
        assert stored == _replayed_durability(store, session_id)
        assert stored[0] == expected_shots

    for second in range(3):
        store.add_event(session_id, _shot(f"2026-01-01T00:00:{second:02d}"))
    store.add_event(session_id, {"kind": "chat", "timestamp": "2026-01-01T00:00:03", "raw_message": "hi", "payload": {}})
    check(3)
    store.add_event(session_id, {"kind": "repair", "timestamp": "2026-01-01T00:01:00",
                                 "raw_message": "Item(s) repaired successfully", "payload": {"estimated_cost": 0.03}})
    check(0)
    store.add_event(session_id, _shot("2026-01-01T00:00:30"))  # written late: sorts before the repair
    check(0)
    store.add_event(session_id, _shot("2026-01-01T00:02:00"))
    store.add_event(session_id, _shot("2026-01-01T00:02:01"))
    check(2)
    with sqlite3.connect(db) as conn:  # a foreign delete of a counted shot
        conn.execute("DELETE FROM events WHERE session_id = ? AND timestamp = '2026-01-01T00:02:00'", (session_id,))
    store.add_event(session_id, _shot("2026-01-01T00:02:02"))
    check(2)


def test_held_connection_keeps_per_call_isolation_and_closes_with_the_block(tmp_path) -> None:
    db = tmp_path / "ped-hunter.sqlite3"
    store = Store(db)
    with store.hold():
        with store.connect() as first:
            first.execute("BEGIN IMMEDIATE")
            first.execute("INSERT INTO app_settings (key, value) VALUES ('abandoned', '1')")
        with store.connect() as second:  # the same connection, without the abandoned write
            assert second is first
            assert second.execute("SELECT COUNT(*) FROM app_settings WHERE key = 'abandoned'").fetchone()[0] == 0
        session_id = store.start_session()
        store.add_event(session_id, _shot("2026-01-01T00:00:00"))
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")  # closed with the block
    wal = db.with_name(db.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0  # the file alone is complete, as before
    assert Store(db).get_session(session_id).events == 1


def test_deep_integrity_check_is_skipped_only_while_a_recent_one_stands(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ped-hunter.sqlite3"
    Store(db)
    checks = []
    original = sqlite3.Connection.execute

    class Counting(sqlite3.Connection):
        def execute(self, sql, *args):
            if "integrity_check" in sql:
                checks.append(sql)
            return original(self, sql, *args)

    real_connect = sqlite3.connect
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Counting, **k))
    Store(db)
    assert checks == []  # verified moments ago and closed cleanly
    stale = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    with real_connect(db) as conn:
        conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", (stale, storage._INTEGRITY_KEY))
    Store(db)
    assert len(checks) == 1  # a week old: verified again
    holder = real_connect(db)  # an unfinished writer leaves its write-ahead log behind
    holder.execute("INSERT INTO app_settings (key, value) VALUES ('pending', '1')")
    holder.commit()
    Store(db)
    assert len(checks) == 2
    holder.close()
