"""Actual native dual healing controls and conservative primary-cost projection."""
from copy import deepcopy

import pytest

from ped_hunter import app as appmod
from ped_hunter.storage import LoadoutRecord, Store

FAP = "Frontier First Aid Pack, Adjusted"


@pytest.fixture
def app(tmp_path, monkeypatch, request):
    chat = tmp_path / "chat.log"
    chat.write_text("")
    monkeypatch.setattr(appmod, "_default_chat_log_path", lambda: chat)
    store = Store(tmp_path / "native-healing.sqlite3")
    choice = getattr(request, "param", None)
    heal, hot = choice if isinstance(choice, tuple) else (choice, None)
    store.set_selected_healing_tool(heal)
    store.set_selected_hot_tool(hot)
    application = appmod.PedHunterApp(store=store)
    application.title("PED Hunter isolated healing integration")
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

def select_hot(app, name):
    app.hot_selector.set(name)
    app.hot_selector.event_generate("<<ComboboxSelected>>")
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
    for widget in (app.healing_selector, app.hot_selector, app.healing_detail_label, app.healing_totals_label):
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
    assert app._activate_saved_loadout(second, confirm=False)
    app.update()
    assert app.store.get_active_loadout().id == second
    assert app.healing_tool.get() == FAP
    assert app.store.get_selected_healing_tool() == FAP
    select(app, "None")
    assert Store(app.store.db_path).get_selected_healing_tool() is None
    assert app.store.get_loadout(first).name == "First synthetic setup"
    assert app.store.get_loadout(second).name == "Second synthetic setup"


def test_native_heals_book_immediately_and_allocate_only_after_supported_loot_without_rebasing_fans(app):
    gear = LoadoutRecord(None, "Synthetic blade", "SET Combat Blade (L)",
                         ammo_burn=65, decay=0.00001, cost_per_shot=0.00651)
    app.store.save_loadout(gear, make_active=True)
    sid = app.store.start_session("hunt", gear)
    app.session_id, app.running = sid, True
    select(app, FAP)
    app._open_streamer_window()
    hud = app.streamer_window
    hud.title("PED Hunter isolated healing integration HUD")

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
    assert hud.kill_trend_points.healing_pending == pytest.approx(.04)
    ingest(15, "You received Shrapnel x (400) Value: 0.04 PED")
    assert [point["cost"] for point in hud.kill_trend_points] == pytest.approx([.01302, .03302])
    assert hud.kill_trend_points.healing_pending == pytest.approx(.02)
    second = hud.kill_trend_points[-1]["event_id"]
    assert hud.kill_trail_histories[second][:len(histories[second])] == histories[second]
    assert hud.kill_trail_histories[second][-1][0] == pytest.approx(.03302)


@pytest.mark.parametrize("app", [FAP, "Unavailable future tool"], indirect=True)
def test_native_startup_restores_known_or_missing_preference_without_repricing(app):
    name = app.store.get_selected_healing_tool()
    assert app.healing_tool.get() == name
    if name == FAP:
        assert "0.02000 PED/use" in app.healing_detail.get()
    else:
        assert "unpriced" in app.healing_detail.get()
        assert app.catalog.healing_tools.get(name) is None
    assert app.store.list_all_sessions() == []
    select(app, "None")
    assert Store(app.store.db_path).get_selected_healing_tool() is None


def test_native_hot_filter_exact_labels_and_independent_persistent_choices(app):
    values = tuple(app.hot_selector.cget("values"))
    assert app.hot_selector.instate(["readonly"])
    assert FAP not in values and "Regeneration Chip 8" not in values
    assert "Restoration Chip 3 (L)" in values
    assert all(app.catalog.healing_tools[name].has_hot for name in values if name != "None")
    labels = {str(widget.cget("text")) for widget in app.hero_healing.winfo_children()
              if widget.winfo_class() == "TLabel"}
    assert {"Heal:", "HoT:"}.issubset(labels)
    select(app, FAP)
    select_hot(app, "EMT Kit Ek-2600, Modified")
    reopened = Store(app.store.db_path)
    assert reopened.get_selected_healing_tool() == FAP
    assert reopened.get_selected_hot_tool() == "EMT Kit Ek-2600, Modified"
    select(app, "None")
    assert Store(app.store.db_path).get_selected_hot_tool() == "EMT Kit Ek-2600, Modified"


@pytest.mark.parametrize("app", [(FAP, "EMT Kit Ek-2600, Modified"), (None, "Unavailable future HoT")], indirect=True)
def test_native_startup_restores_both_slots_without_substituting_missing_hot(app):
    assert app.healing_tool.get() == (app.store.get_selected_healing_tool() or "None")
    assert app.hot_tool.get() == app.store.get_selected_hot_tool()
    if app.hot_tool.get() == "Unavailable future HoT":
        assert "unpriced" in app.healing_detail.get()
    assert app.store.list_all_sessions() == []


@pytest.mark.parametrize("path", ["variable", "selector"])
def test_native_dual_quotes_and_new_primary_cost_follow_repeated_currency_toggles(app, path):
    from tkinter import ttk
    select(app, FAP)
    select_hot(app, "EMT Kit Ek-2600, Modified")
    gear = LoadoutRecord(None, "Synthetic blade", "SET Combat Blade (L)",
                         ammo_burn=65, decay=.00001, cost_per_shot=.00651)
    app.store.save_loadout(gear, make_active=True)
    sid = app.store.start_session("hunt", gear)
    app.session_id, app.running = sid, True
    app._process_lines([
        "2026-11-08 10:00:00 [System] [] You inflicted 6.0 points of damage",
        "2026-11-08 10:00:01 [System] [] Received Effect Over Time: Heal",
        "2026-11-08 10:00:01 [System] [] You healed yourself 54.0 points",
        "2026-11-08 10:00:02 [System] [] You received Shrapnel x (100) Value: 1.0 PED",
    ], sid, None)
    app._refresh_all()
    app._open_streamer_window()
    app.update()
    hud = app.streamer_window
    picker = next(widget for section in app.root_frame.winfo_children() for widget in section.winfo_children()
                  if isinstance(widget, ttk.Combobox) and str(widget.cget("textvariable")) == str(app.currency_mode))
    with app.store.connect() as conn:
        original = [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
    for mode in ("USD", "PED", "USD", "PED"):
        if path == "variable":
            app.currency_mode.set(mode)
        else:
            picker.current(0 if mode == "USD" else 1)
            picker.event_generate("<<ComboboxSelected>>")
        app.update()
        hud._chart_frame(1.0)
        assert ("$0.00420/cast" if mode == "USD" else "0.04200 PED/cast") in app.healing_detail.get()
        assert hud.kill_trend_points[0]["healing_cost"] == pytest.approx(.042)
        assert hud.kill_trend_points[0]["cost"] == pytest.approx(.04851)
        assert mode in hud.chart_note.cget("text")
        with app.store.connect() as conn:
            assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == original


@pytest.mark.parametrize('size', [(260, 240), (360, 330), (640, 480)])
@pytest.mark.parametrize('state', ['empty', 'priced', 'shared'])
def test_native_legacy_warning_complete_and_fits_existing_summary_after_resize(app, size, state):
    """Requires authorized native execution; offline models are not rendered proof."""
    sid = app.store.start_session('hunt')
    app.store.add_event(sid, {
        'kind': 'combat', 'raw_message': 'Synthetic retained legacy healing',
        'payload': {'healing_action': 'self', 'healing_cost': .04, 'healing_cost_status': 'estimated'},
    })
    app.session_id = sid
    app._refresh_all()
    app._open_streamer_window()
    hud = app.streamer_window
    hud.geometry(f'{round(size[0] * hud.ui_scale)}x{round(size[1] * hud.ui_scale)}')
    points = appmod._kill_trend_points(app.store, sid)
    points.unpriced_heals = 3
    if state != 'empty':
        points.append(dict(kill=1, label='K123', kind='hunt', cost=123.45, profit=234.56,
                           loot=358.01, avg_cost=123.45, delta_profit=None,
                           cost_status='shared_ambiguous' if state == 'shared' else None))
    hud.vars['kill_trend_badge'].set(appmod._streamer_trend_detail(points, 'PED'))
    app.update()
    label = hud._fixed_labels['kill_trend_badge']
    text = hud.vars['kill_trend_badge'].get()
    assert 'no saved allocation context; not backfilled' in text
    assert 'already in totals' in text and 'No new charge or lost data.' in text
    assert '…' not in text
    assert label.winfo_reqheight() <= label.winfo_height()
    inside(label, hud)
    for widget in (hud.durability_section, hud.footer, hud.resize_handle):
        inside(widget, hud)
        assert widget.winfo_height() >= widget.winfo_reqheight()
    import tkinter.font as tkfont
    size_value = tkfont.Font(hud, font=label.cget('font')).cget('size')
    # Tk resolves a copied negative-pixel font to positive points on Windows.
    # Compare rendered pixels, not point and pixel magnitudes as if identical.
    pixels = -size_value if size_value < 0 else size_value * hud.winfo_fpixels('1p')
    assert pixels >= 10 * hud._content_scale - 1
    assert not hud.kill_chart.bind('<Enter>') and not hud.chart_note.bind('<Enter>')
    assert not hasattr(hud, 'damage_slot')
