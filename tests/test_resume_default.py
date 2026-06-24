from __future__ import annotations

from ped_hunter.app import PedHunterApp
from ped_hunter.storage import SessionSummary


class FakeStore:
    def __init__(self, sessions: list[SessionSummary]) -> None:
        self.sessions = sessions

    def list_recent_sessions(self, limit: int = 5) -> list[SessionSummary]:
        return self.sessions[:limit]

    def get_session(self, session_id: str) -> SessionSummary | None:
        return next((session for session in self.sessions if session.session_id == session_id), None)


class FakeTree:
    def __init__(self, selection: tuple[str, ...] = (), children: tuple[str, ...] = ()) -> None:
        self._selection = selection
        self._children = list(children)
        self.focused: str | None = None
        self.rows: dict[str, tuple[object, ...]] = {}

    def selection(self) -> tuple[str, ...]:
        return self._selection

    def get_children(self) -> tuple[str, ...]:
        return tuple(self._children)

    def delete(self, *children: str) -> None:
        self._children = [child for child in self._children if child not in children]
        for child in children:
            self.rows.pop(child, None)

    def insert(self, _parent: str, _index: str, *, iid: str, tags: tuple[str, ...], values: tuple[object, ...]) -> None:
        self._children.append(iid)
        self.rows[iid] = values

    def selection_set(self, session_id: str) -> None:
        self._selection = (session_id,)

    def focus(self, session_id: str) -> None:
        self.focused = session_id


class FakeVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def session(session_id: str, started_at: str) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        started_at=started_at,
        ended_at="2026-01-01T00:00:00",
        activity="hunt",
        loot_value=0.0,
        combat_damage=0.0,
        hunting_cost=0.0,
        net_value=0.0,
        events=0,
        loadout_name="Test setup",
        repair_shots=0,
        repair_decay=0.0,
        loadout_snapshot=None,
    )


def app_with(tree: FakeTree, sessions: list[SessionSummary]) -> PedHunterApp:
    app = object.__new__(PedHunterApp)
    app.sessions_tree = tree
    app.store = FakeStore(sessions)
    app.selected_session_text = FakeVar()
    return app


def test_resume_session_id_uses_explicit_selection_first() -> None:
    app = app_with(FakeTree(selection=("older",), children=("latest", "older")), [session("latest", "2"), session("older", "1")])

    assert app._resume_session_id() == "older"


def test_resume_session_id_defaults_to_newest_tree_row_without_selection() -> None:
    app = app_with(FakeTree(children=("latest", "older")), [session("latest", "2"), session("older", "1")])

    assert app._resume_session_id() == "latest"


def test_refresh_sessions_selects_newest_when_nothing_is_selected() -> None:
    sessions = [session("latest", "2"), session("older", "1")]
    tree = FakeTree()
    app = app_with(tree, sessions)

    app._refresh_sessions(sessions)

    assert tree.selection() == ("latest",)
    assert tree.focused == "latest"
    assert "Test setup" in app.selected_session_text.value


def test_refresh_sessions_preserves_existing_selection() -> None:
    sessions = [session("latest", "2"), session("older", "1")]
    tree = FakeTree(selection=("older",))
    app = app_with(tree, sessions)

    app._refresh_sessions(sessions)

    assert tree.selection() == ("older",)
    assert tree.focused == "older"
