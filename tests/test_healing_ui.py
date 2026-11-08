"""Actual native healing controls and unchanged loot-fan projection."""
from copy import deepcopy

import pytest

from ped_hunter import app as appmod
from ped_hunter.storage import LoadoutRecord, Store

FAP = "Frontier First Aid Pack, Adjusted"


@pytest.fixture
def app(tmp_path, monkeypatch):
    chat = tmp_path / "chat.log"
    chat.write_text("")
    monkeypatch.setattr(appmod, "_default_chat_log_path", lambda: chat)
    application = appmod.PedHunterApp(store=Store(tmp_path / "native-healing.sqlite3"))
    application.title("PED-004 isolated healing test")
    application.reduced_motion.set(True)
    application.currency_mode.set("PED")
    errors = []
    application.report_callback_exception = lambda *args: errors.append(args)
    try:
        application.update()
        yield application
        assert not errors
    finally:
        application._stop_log_worker()
        application.destroy()


def select(app, name):
    app.healing_selector.set(name)
    app.healing_selector.event_generate("<<ComboboxSelected>>")
    app.update()


def inside(widget, parent):
    assert widget.winfo_ismapped()
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    assert x >= parent.winfo_rootx() and y >= parent.winfo_rooty()
    assert x + widget.winfo_width() <= parent.winfo_rootx() + parent.winfo_width()
    assert y + widget.winfo_height() <= parent.winfo_rooty() + parent.winfo_height()


@pytest.mark.parametrize("size", [(900, 600), (760, 500)])
@pytest.mark.parametrize("name", ["None", FAP, "EMT Kit Ek-2600, Modified"])
def test_selector_values_warnings_and_run_actions_fit_ordinary_sizes(app, size, name):
    app.geometry(f"{round(size[0] * app.display_scale)}x{round(size[1] * app.display_scale)}")
    select(app, name)
    for widget in (app.healing_selector, app.healing_detail_label, app.healing_totals_label):
        inside(widget, app.hero_healing)
        inside(widget, app)
    for label in (app.healing_detail_label, app.healing_totals_label):
        assert label.winfo_reqheight() <= label.winfo_height()
        assert label.winfo_reqwidth() <= label.winfo_width()
    for widget in (app.start_button, app.resume_button, app.stop_button):
        inside(widget, app)
    assert app.healing_selector.instate(["readonly"])
    values = tuple(app.healing_selector.cget("values"))
    assert "None" in values and FAP in values and "Regeneration Chip 8" in values
    assert "SET Combat Blade (L)" not in values
    app.healing_selector.focus_force()
    app.update()
    assert app.focus_get() == app.healing_selector
    app.healing_selector.event_generate("<Tab>", when="tail")
    app.update()
    assert app.focus_get() is not None and app.focus_get() != app.healing_selector


def test_native_change_clear_and_setup_activation_leave_global_preference_separate(app):
    original = LoadoutRecord(None, "First synthetic setup", "SET Combat Blade (L)",
                             ammo_burn=65, decay=0.00001, cost_per_shot=0.00651)
    first = app.store.save_loadout(original, make_active=True)
    second = app.store.save_loadout(LoadoutRecord(None, "Second synthetic setup", "Frontier Rifle",
                                                 ammo_burn=100, decay=0.0002, cost_per_shot=0.0102))
    app._refresh_loadouts()
    with app.store.connect() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM loadouts ORDER BY id")]
    select(app, FAP)
    assert Store(app.store.db_path).get_selected_healing_tool() == FAP
    with app.store.connect() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM loadouts ORDER BY id")] == before
    app.loadouts_tree.selection_set(str(second))
    app._load_selected_loadout()
    app.update()
    assert app.store.get_active_loadout().id == second
    assert app.healing_tool.get() == FAP
    assert app.store.get_selected_healing_tool() == FAP
    select(app, "None")
    assert Store(app.store.db_path).get_selected_healing_tool() is None
    assert app.store.get_loadout(first).name == "First synthetic setup"
    assert app.store.get_loadout(second).name == "Second synthetic setup"


def test_native_heal_updates_run_and_hud_totals_without_rewriting_loot_fans(app):
    gear = LoadoutRecord(None, "Synthetic blade", "SET Combat Blade (L)",
                         ammo_burn=65, decay=0.00001, cost_per_shot=0.00651)
    app.store.save_loadout(gear, make_active=True)
    sid = app.store.start_session("hunt", gear)
    app.session_id, app.running = sid, True
    select(app, FAP)
    app._open_streamer_window()
    hud = app.streamer_window
    hud.title("PED-004 isolated healing HUD")

    def ingest(second, text):
        app._process_lines([f"2026-11-08 10:00:{second:02d} [System] [] {text}"], sid, None)
        app._refresh_all()
        app.update()
        hud._chart_frame(1.0)

    ingest(0, "You inflicted 6.0 points of damage")
    ingest(1, "You inflicted 6.0 points of damage")
    ingest(2, "You received Shrapnel x (200) Value: 0.02 PED")
    ingest(5, "You inflicted 6.0 points of damage")
    ingest(6, "You inflicted 6.0 points of damage")
    before = deepcopy(hud.kill_trend_points)
    histories = deepcopy(hud.kill_trail_histories)
    assert [point["cost"] for point in before] == pytest.approx([0.01302, 0.01302])
    assert any(sample[0] == pytest.approx(0.00651) for samples in histories.values() for sample in samples)
    assert any(sample[0] == pytest.approx(0.01302) for samples in histories.values() for sample in samples)
    ingest(9, "You healed yourself 23.0 points")
    ingest(12, "You healed Test Avatar with 23.0 points")
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == pytest.approx(0.02604 + 0.04)
    assert summary.estimated_healing_uses == 2
    assert app.hero_cost.get() == app._money(summary.hunting_cost)
    assert hud.vars["cost"].get() == app._money(summary.hunting_cost)
    assert hud.vars["net_big"].get() == app._money(0.02 - 0.02604 - 0.04, signed=True)
    assert hud.kill_trend_points == before
    assert hud.kill_trail_histories == histories
