"""Live summaries must never borrow money from historical sessions."""
from ped_hunter.app import PedHunterApp
from ped_hunter.storage import Store


def test_startup_does_not_display_saved_session(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    sid = store.start_session()
    store.add_event(sid, {"kind": "loot", "raw_message": "saved loot", "payload": {"value": 4.0}})
    app = object.__new__(PedHunterApp)
    app.store = store
    app.session_id = None

    assert app._display_session(store.get_current_session(), store.list_recent_sessions()) is None
    # History remains available for explicit resumption.
    assert app._preferred_session().session_id == sid
    assert store.get_session(sid).loot_value == 4.0


def test_explicit_run_remains_visible_after_stop(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    sid = store.start_session()
    store.end_session(sid)
    app = object.__new__(PedHunterApp)
    app.store = store
    app.session_id = sid

    assert app._display_session(None, []).session_id == sid
