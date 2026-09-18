from __future__ import annotations

from ped_hunter.storage import Store


def test_end_other_active_sessions_closes_stale_workers(tmp_path) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    stale_id = store.start_session()
    current_id = store.start_session()

    assert store.end_other_active_sessions(current_id) == 1
    assert store.get_session(stale_id).ended_at is not None
    assert store.get_session(current_id).ended_at is None
