"""Setup-controller transitions against real SQLite, without creating a Tk window."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.catalog import Catalog
from ped_hunter.storage import LoadoutRecord, Store


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class SelectionTree:
    """Only the selection/list surface; no replacement for native layout checks."""
    def __init__(self):
        self.rows = {}
        self.selected = ()

    def get_children(self):
        return tuple(self.rows)

    def delete(self, *rows):
        for row in rows:
            del self.rows[row]
        self.selected = ()

    def insert(self, _parent, _position, *, iid, values, tags):
        assert iid not in self.rows
        self.rows[iid] = values

    def exists(self, row):
        return row in self.rows

    def selection(self):
        return self.selected

    def selection_set(self, row):
        assert row in self.rows
        self.selected = (row,)

    def focus(self, row):
        assert row in self.rows


@pytest.fixture
def editor(tmp_path, monkeypatch):
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / "setups.sqlite3")
    app.catalog = Catalog.load()
    app.store.save_loadout(LoadoutRecord(
        id=None, name="Existing rifle", weapon="Frontier Rifle", ammo_burn=100,
        decay=0.0002, cost_per_shot=0.0102, repair_shots=17), make_active=True)
    app.selected_loadout_id = None
    app.running = False
    app.session_id = None
    app._loadout_clean_state = ()
    for field in (
        "loadout_name", "loadout_weapon", "loadout_amp", "loadout_scope",
        "loadout_sight_1", "loadout_sight_2", "damage_enhancers",
        "accuracy_enhancers", "economy_enhancers", "loadout_draft_text",
        "loadout_preview", "status_text", "active_loadout_title",
        "active_loadout_details", "active_loadout_text",
    ):
        setattr(app, field, Value())
    app.currency_mode = Value("PED")
    app.loadouts_tree = SelectionTree()
    app.delete_loadout_button = SimpleNamespace(configure=lambda **_kwargs: None)
    # Rendering/querying unrelated tabs requires Tk. The controller's actual
    # saved-list refresh still executes, including stable identities/selection.
    app._refresh_all = app._refresh_loadouts
    app._refresh_live_views = lambda: None  # Native tests cover session/header rendering.
    app._new_loadout_draft()
    app._refresh_loadouts()
    errors = []
    monkeypatch.setattr("ped_hunter.app.messagebox.showerror", lambda _title, message, **_kwargs: errors.append(message))
    monkeypatch.setattr("ped_hunter.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: pytest.fail("Unexpected discard prompt"))
    app.errors = errors
    return app


def select(app, row, *, explicit=False):
    app.loadouts_tree.selection_set(str(row))
    app._load_selected_loadout(explicit=explicit)


def fill(app, name, weapon="SET Combat Blade (L)"):
    app.loadout_name.set(name)
    app.loadout_weapon.set(weapon)


def records(app):
    return {item.id: asdict(item) for item in app.store.list_loadouts()}


def test_new_entry_repeatedly_creates_distinct_setups_without_changing_existing(editor):
    original = records(editor)
    created = []
    for name in ("Scout blade", "Backup blade"):
        select(editor, "new", explicit=True)
        assert editor.selected_loadout_id is None
        assert editor.loadout_name.get() == editor.loadout_weapon.get() == ""
        fill(editor, name)
        assert editor._save_loadout(make_active=False)
        created.append(editor.selected_loadout_id)
        assert editor.loadouts_tree.get_children()[0] == "new"
        assert editor.store.get_loadout(created[-1]).name == name
    assert len(set(created)) == 2
    assert all(item not in original for item in created)
    assert len(records(editor)) == len(original) + 2
    assert records(editor)[1] == original[1]
    assert editor.store.get_active_loadout().id == 1
    saved = editor.store.get_loadout(created[0])
    assert (saved.ammo_burn, saved.decay, saved.cost_per_shot) == pytest.approx((65, 0.00001, 0.00651))


@pytest.mark.parametrize("name", ["Existing rifle", "  Existing rifle  "])
def test_new_duplicate_name_cannot_upsert_existing_setup(editor, name):
    original = records(editor)
    fill(editor, name)
    assert not editor._save_loadout(make_active=True)
    assert records(editor) == original
    assert editor.selected_loadout_id is None
    assert editor.loadout_name.get() == name
    assert len(editor.errors) == 1


def test_rename_collision_preserves_both_records_and_active_setup(editor):
    fill(editor, "Second setup")
    assert editor._save_loadout(make_active=False)
    second = editor.selected_loadout_id
    original = records(editor)
    editor.loadout_name.set("Existing rifle")
    assert not editor._save_loadout(make_active=True)
    assert records(editor) == original
    assert editor.selected_loadout_id == second
    assert editor.store.get_active_loadout().id == 1


@pytest.mark.parametrize("name,weapon", [(" ", "Frontier Rifle"), ("New", "not a weapon"), ("New", "")])
def test_invalid_new_draft_stays_editable_without_writing(editor, name, weapon):
    original = records(editor)
    fill(editor, name, weapon)
    assert not editor._save_loadout(make_active=False)
    assert records(editor) == original
    assert editor.selected_loadout_id is None
    assert (editor.loadout_name.get(), editor.loadout_weapon.get()) == (name, weapon)
    assert len(editor.errors) == 1


def test_save_existing_active_setup_keeps_activation_and_repair_history(editor):
    session = editor.store.start_session("hunt", editor.store.get_loadout(1))
    editor.store.add_event(session, {"kind": "combat", "raw_message": "previous shot", "payload": {"damage": 6, "shot_cost": 0.0102}})
    editor.running, editor.session_id = True, session
    select(editor, 1)
    editor.loadout_name.set("Renamed rifle")
    editor.damage_enhancers.set("2")
    assert editor._save_loadout(make_active=False)
    saved = editor.store.get_loadout(1)
    assert saved.name == "Renamed rifle"
    assert saved.damage_enhancers == 2
    assert saved.repair_shots == 18
    assert saved.active
    assert saved.cost_per_shot > 0.0102
    assert len(records(editor)) == 1
    current = editor.store.get_session(session)
    assert current.loadout_snapshot["id"] == 1
    assert current.loadout_snapshot["name"] == saved.name
    assert current.loadout_snapshot["cost_per_shot"] == saved.cost_per_shot
    assert current.hunting_cost == pytest.approx(0.0102)


def test_save_and_activate_new_setup_links_real_id_without_repricing_events(editor):
    session = editor.store.start_session("hunt", editor.store.get_loadout(1))
    editor.store.add_event(session, {"kind": "combat", "raw_message": "original shot", "payload": {"damage": 6, "shot_cost": 0.0102}})
    with editor.store.connect() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
    editor.running, editor.session_id = True, session
    fill(editor, "New blade")
    assert editor._save_loadout(make_active=True)
    summary = editor.store.get_session(session)
    assert summary.session_id == session
    assert summary.loadout_snapshot["id"] == editor.selected_loadout_id
    assert summary.loadout_snapshot["weapon"] == "SET Combat Blade (L)"
    assert summary.hunting_cost == pytest.approx(0.0102)
    assert editor.store.get_active_loadout().id == editor.selected_loadout_id
    assert not editor.store.get_loadout(1).active
    with editor.store.connect() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == before


def test_new_draft_and_plain_save_do_not_change_live_session_setup(editor):
    session = editor.store.start_session("hunt", editor.store.get_loadout(1))
    editor.running, editor.session_id = True, session
    select(editor, 1)
    original_snapshot = editor.store.get_session(session).loadout_snapshot
    select(editor, "new")
    assert editor.store.get_active_loadout().id == 1
    assert editor.store.get_session(session).loadout_snapshot == original_snapshot
    fill(editor, "Inactive blade")
    assert editor._save_loadout(make_active=False)
    saved_id = editor.selected_loadout_id
    editor._refresh_loadouts()
    editor._load_selected_loadout()  # queued selection notification after refresh
    assert editor.selected_loadout_id == saved_id
    assert editor.store.get_active_loadout().id == 1
    assert editor.store.get_session(session).loadout_snapshot == original_snapshot
    select(editor, "new")
    select(editor, saved_id)
    assert editor.store.get_active_loadout().id == saved_id
    assert editor.store.get_session(session).loadout_snapshot["id"] == saved_id


@pytest.mark.parametrize("answer", [None, False, True], ids=["cancel", "discard", "save"])
@pytest.mark.parametrize("target", ["new", "other"])
def test_unsaved_existing_edits_respect_save_discard_cancel(editor, monkeypatch, answer, target):
    other = editor.store.save_loadout(LoadoutRecord(id=None, name="Other", weapon="Frontier Rifle"))
    editor._refresh_loadouts()
    select(editor, 1)
    original = records(editor)[1]
    editor.loadout_name.set("Edited rifle")
    editor.economy_enhancers.set("1")
    monkeypatch.setattr("ped_hunter.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: answer)
    select(editor, "new" if target == "new" else other)
    if answer is None:
        assert editor.selected_loadout_id == 1
        assert editor.loadouts_tree.selection() == ("1",)
        assert editor.loadout_name.get() == "Edited rifle"
        assert records(editor)[1] == original
        assert editor.store.get_active_loadout().id == 1
    else:
        assert editor.selected_loadout_id == (None if target == "new" else other)
        assert editor.loadout_name.get() == ("" if target == "new" else "Other")
        if answer:
            assert records(editor)[1]["name"] == "Edited rifle"
            assert records(editor)[1]["economy_enhancers"] == 1
        else:
            assert records(editor)[1] == {**original, "active": target == "new"}
        assert editor.store.get_active_loadout().id == (1 if target == "new" else other)


def test_unsaved_new_draft_can_be_saved_before_selecting_existing(editor, monkeypatch):
    original = records(editor)[1]
    fill(editor, "Saved before leaving")
    monkeypatch.setattr("ped_hunter.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: True)
    select(editor, 1)
    assert len(records(editor)) == 2
    assert records(editor)[1] == original
    assert editor.selected_loadout_id == editor.store.get_active_loadout().id == 1
    assert any(item.name == "Saved before leaving" for item in editor.store.list_loadouts())


def test_failed_save_from_unsaved_prompt_does_not_leave_draft_or_activate_target(editor, monkeypatch):
    other = editor.store.save_loadout(LoadoutRecord(id=None, name="Other", weapon="Frontier Rifle"))
    editor._refresh_loadouts()
    fill(editor, "Incomplete draft", "")
    original = records(editor)
    monkeypatch.setattr("ped_hunter.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: True)
    select(editor, other)
    assert editor.selected_loadout_id is None
    assert editor.loadouts_tree.selection() == ("new",)
    assert editor.loadout_name.get() == "Incomplete draft"
    assert records(editor) == original


@pytest.mark.parametrize("existing", [False, True])
def test_refresh_does_not_discard_dirty_form_or_activate_another_setup(editor, existing):
    if existing:
        select(editor, 1)
    editor.loadout_name.set("Still editing")
    before_form = editor._loadout_form_state()
    before_data = records(editor)
    editor._refresh_loadouts()
    editor._load_selected_loadout()
    assert editor._loadout_form_state() == before_form
    assert records(editor) == before_data


def test_reselecting_new_explicitly_can_discard_current_new_draft(editor, monkeypatch):
    fill(editor, "Discard me")
    original = records(editor)
    monkeypatch.setattr("ped_hunter.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: False)
    select(editor, "new", explicit=True)
    assert editor.selected_loadout_id is None
    assert editor.loadout_name.get() == editor.loadout_weapon.get() == ""
    assert records(editor) == original


def test_delete_new_is_noop_and_delete_saved_only_removes_selected_record(editor):
    original = records(editor)
    editor._delete_selected_loadout()
    assert records(editor) == original
    fill(editor, "Remove this blade")
    assert editor._save_loadout(make_active=False)
    removed = editor.selected_loadout_id
    editor._delete_selected_loadout()
    assert editor.store.get_loadout(removed) is None
    assert records(editor) == original
    assert editor.selected_loadout_id is None
    assert editor.loadout_name.get() == ""
    assert editor.loadouts_tree.get_children()[0] == "new"


@pytest.mark.parametrize("dirty", [False, True])
def test_explicit_selection_can_activate_clean_saved_draft_but_keeps_pending_edits(editor, dirty):
    fill(editor, "Saved inactive")
    assert editor._save_loadout(make_active=False)
    saved = editor.selected_loadout_id
    if dirty:
        editor.loadout_name.set("Pending rename")
    before = records(editor)
    editor._load_selected_loadout()
    assert editor.store.get_active_loadout().id == 1
    editor._load_selected_loadout(explicit=True)
    assert editor.store.get_active_loadout().id == (1 if dirty else saved)
    if dirty:
        assert records(editor) == before
        assert editor.loadout_name.get() == "Pending rename"
    else:
        assert editor.loadout_name.get() == "Saved inactive"
