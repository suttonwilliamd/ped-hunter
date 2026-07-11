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

