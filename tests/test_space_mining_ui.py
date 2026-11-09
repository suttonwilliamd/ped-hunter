"""Native widgets plus real saved setup and source-bound Resume behavior."""
from dataclasses import asdict
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

import pytest

from ped_hunter.app import PedHunterApp, calculate_loadout_cost
from ped_hunter.catalog import Catalog
from ped_hunter.resume_handoff import stage_resume_handoff
from ped_hunter.storage import LoadoutRecord, Store


NAME = "Basic Mining Laser, Type M (L)"
OLD = "2026-11-09 01:00:00 [System] [] You inflicted 4.6 points of damage"
OLD2 = "2026-11-09 01:00:01 [System] [] You received Iron Stone x (1) Value: 0.13 PED"
GAP = "2026-11-09 01:01:00 [System] [] You missed"
LOOT = "2026-11-09 01:01:01 [System] [] You received Tailings x (318) Value: 0.0318 PED"


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(app, text):
    return next(w for w in descendants(app) if isinstance(w, ttk.Button) and w.cget("text") == text)


def native_app(store, path, monkeypatch):
    monkeypatch.setattr("ped_hunter.app._default_chat_log_path", lambda: path)
    try:
        app = PedHunterApp(store=store)
    except tk.TclError as exc:
        pytest.skip(f"Native Tk unavailable: {exc}")
    app.title("PED-012 ISOLATED PYTEST")
    app.currency_mode.set("PED")
    app.update()
    return app


def test_native_type_m_is_selectable_saved_and_activated_without_overwriting(tmp_path, monkeypatch):
    store = Store(tmp_path / "native.sqlite3")
    old_id = store.save_loadout(LoadoutRecord(id=None, name="Existing rifle", weapon="Frontier Rifle",
                                            ammo_burn=100, decay=0.0002, cost_per_shot=0.0102), make_active=True)
    original = asdict(store.get_loadout(old_id))
    path = tmp_path / "chat.log"; path.write_bytes(b"")
    app = native_app(store, path, monkeypatch)
    try:
        combos = [w for w in descendants(app) if isinstance(w, ttk.Combobox) and NAME in w.cget("values")]
        assert combos
        app._new_loadout_draft()
        app.loadout_name.set("Space Type M")
        app.loadout_weapon.set(NAME)
        app._refresh_loadout_preview()
        assert app._loadout_from_form().cost_per_shot == pytest.approx(0.0223)
        button(app, "Save").invoke(); app.update()
        saved = next(r for r in store.list_loadouts() if r.name == "Space Type M")
        assert saved.id != old_id and store.get_active_loadout().id == old_id
        assert asdict(store.get_loadout(old_id)) == original
        button(app, "Save and activate").invoke(); app.update()
        assert store.get_active_loadout().id == saved.id
        assert store.get_active_loadout().weapon == NAME
        assert (saved.ammo_burn, saved.decay, saved.cost_per_shot) == pytest.approx((212, 0.0011, 0.0223))
        app._search_catalog()
        app.catalog_query.set(NAME); app._search_catalog()
        assert any(NAME in str(app.catalog_tree.item(row, "values")) for row in app.catalog_tree.get_children())
    finally:
        app._stop_log_worker(); app.destroy()


def test_native_resume_uses_handoff_not_eof_and_preserves_current_setup_metadata(tmp_path, monkeypatch):
    store = Store(tmp_path / "native-resume.sqlite3")
    ammo, decay = calculate_loadout_cost(catalog=Catalog.load(), weapon_name=NAME)
    setup = LoadoutRecord(id=None, name="Type M", weapon=NAME, ammo_burn=ammo,
                          decay=decay, cost_per_shot=0.0223)
    setup.id = store.save_loadout(setup, make_active=True)
    sid = store.start_session("hunt", setup)
    headless = object.__new__(PedHunterApp); headless.store = store; headless.catalog = Catalog.load()
    headless._process_lines([OLD, OLD2], sid, None)
    path = tmp_path / "chat.log"; path.write_bytes((OLD + "\r\n" + OLD2 + "\r\n").encode())
    original_setup = asdict(store.get_active_loadout())
    with store.connect() as conn:
        original_rows = [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
    stage_resume_handoff(store.db_path, sid, path, [OLD, OLD2], "native-isolated-cutover-1", original_setup)
    with path.open("ab") as stream:
        stream.write((GAP + "\r\n" + LOOT + "\r\n").encode())
    app = native_app(store, path, monkeypatch)
    errors = []; app.report_callback_exception = lambda *args: errors.append(args)
    try:
        assert asdict(store.get_active_loadout()) == original_setup
        app.resume_button.invoke()
        deadline = time.monotonic() + 5
        while store.get_session(sid).events != 4 and time.monotonic() < deadline:
            app.update(); time.sleep(0.01)
        app.update()
        current = store.get_session(sid)
        assert current.events == 4
        assert current.hunting_cost == pytest.approx(0.0446)
        assert current.loot_value == pytest.approx(0.1618)
        assert asdict(store.get_active_loadout()) == dict(
            original_setup, repair_shots=original_setup["repair_shots"] + 1,
        )  # The real new miss advances tracked wear, not the saved gear definition.
        with store.connect() as conn:
            assert [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id LIMIT 2")] == original_rows
        app._open_streamer_window(); app.update()
        assert app.streamer_window is not None and app.streamer_window.winfo_ismapped()
        assert app.streamer_window.vars["cost"].get() == "0.04 PED"
        assert not errors
    finally:
        app._stop_log_worker()
        if app.streamer_window and app.streamer_window.winfo_exists():
            app.streamer_window.destroy()
        app.destroy()
