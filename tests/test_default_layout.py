"""Native Tk checks for the single, space-efficient main layout."""
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.dpi import enable_high_dpi
from ped_hunter.storage import Store


@pytest.fixture(scope="session")
def native_display():
    enable_high_dpi()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Native Tk display unavailable: {error}")
    root.destroy()


@pytest.fixture
def app(tmp_path, native_display):
    window = PedHunterApp(store=Store(tmp_path / "layout.sqlite3"))
    window.update()
    yield window
    window.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_single_density_default(app):
    for widget in (app.start_button, app.resume_button, app.stop_button,
                   app.hero_net_label, app.hero_return_label, app.sessions_tree):
        assert_inside(widget, app)
    assert app.hero_panel.winfo_height() < 190 * app.display_scale
    row_height = int(ttk.Style(app).lookup("Treeview", "rowheight"))
    assert app.sessions_tree.winfo_height() >= max(3 * row_height, round(140 * app.display_scale))



@pytest.mark.parametrize("size", ["1040x640", "980x580", "1180x720", "900x600", "820x540", "760x500"])
def test_primary_controls_fit_and_top_toggle_restores(app, size):
    width, height = map(int, size.split('x'))
    app.geometry(f'{round(width * app.display_scale)}x{round(height * app.display_scale)}')
    app.update()
    for widget in (app.start_button, app.resume_button, app.stop_button, app.status_label, app.hero_return_label):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() >= app.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    for label in (app.hero_session_label, app.hero_net_label, app.hero_return_label, app.hero_repair_label):
        assert_inside(label, app.hero_left)
        assert label.winfo_reqwidth() <= label.winfo_width()
    app.events_tab.master.select(app.events_tab)
    app.update()
    assert_inside(app.loot_chart_canvas, app.events_tab)
    assert app.loot_chart_canvas.winfo_height() >= 120
    app.events_tab.master.select(app.dashboard_tab)
    app.update()
    app._toggle_top_area()
    app.update()
    assert not app.hero_panel.winfo_ismapped()
    app._toggle_top_area()
    app.update()
    assert app.hero_panel.winfo_ismapped()
    assert app.controls_panel.winfo_ismapped()


@pytest.mark.parametrize('scale', [1.0, 1.25, 1.5, 2.0])
def test_run_history_rows_fit_native_dpi_font(app, scale):
    original_scaling = float(app.tk.call('tk', 'scaling'))
    original_scale = app.display_scale
    try:
        app.tk.call('tk', 'scaling', scale * 96 / 72)
        app.display_scale = scale
        app._configure_theme()
        tree = app.sessions_tree
        tree.delete(*tree.get_children())
        rows = [tree.insert('', 'end', values=(
            '2026-10-28 18:30', 'Frontier Rifle · Gypsy', '98.25%',
            '123.45', '125.65', '-2.20', '1234', 'Stopped')) for _ in range(3)]
        app.update_idletasks()
        style = ttk.Style(app)
        row_height = int(style.lookup('Treeview', 'rowheight'))
        line_height = tkfont.Font(app, font=style.lookup('Treeview', 'font')).metrics('linespace')
        assert row_height >= line_height + round(8 * scale)
        assert row_height >= round(23 * scale)
        for row in rows:
            bounds = tree.bbox(row)
            assert bounds and bounds[3] == row_height
        assert tree.bbox(rows[1])[1] - tree.bbox(rows[0])[1] == row_height
    finally:
        app.tk.call('tk', 'scaling', original_scaling)
        app.display_scale = original_scale
        app._configure_theme()


def assert_inside(widget, viewport):
    assert widget.winfo_ismapped()
    assert widget.winfo_width() > 1 and widget.winfo_height() > 1
    assert widget.winfo_rootx() >= viewport.winfo_rootx()
    assert widget.winfo_rooty() >= viewport.winfo_rooty()
    assert widget.winfo_rootx() + widget.winfo_width() <= viewport.winfo_rootx() + viewport.winfo_width()
    assert widget.winfo_rooty() + widget.winfo_height() <= viewport.winfo_rooty() + viewport.winfo_height()


@pytest.mark.parametrize("size", [(900, 600), (820, 540), (760, 500)])
def test_setup_fields_scroll_but_actions_remain_visible(app, size):
    app.geometry(f"{round(size[0] * app.display_scale)}x{round(size[1] * app.display_scale)}")
    app.loadouts_tab.master.select(app.loadouts_tab)
    app.update()
    for widget in (app.save_activate_button, app.save_loadout_button, app.delete_loadout_button):
        assert_inside(widget, app.loadouts_tab)
    assert app.delete_loadout_button.instate(["disabled"])
    for widget in app.loadout_fields.values():
        app.loadout_scroll.reveal(widget)
        app.update()
        assert_inside(widget, app.loadout_scroll.canvas)
        for action in (app.save_activate_button, app.save_loadout_button, app.delete_loadout_button):
            assert_inside(action, app.loadouts_tab)


def test_native_new_setup_row_survives_multiple_saves_and_existing_selection(app):
    app.loadouts_tab.master.select(app.loadouts_tab)
    app.update()
    ids = []
    for name in ("First blade", "Second blade"):
        app.loadouts_tree.selection_set("new")
        app._load_selected_loadout(explicit=True)
        assert app.loadout_name.get() == app.loadout_weapon.get() == ""
        app.loadout_name.set(name)
        app.loadout_weapon.set("SET Combat Blade (L)")
        app.save_loadout_button.invoke()
        app.update()
        ids.append(app.selected_loadout_id)
        assert app.loadouts_tree.get_children()[0] == "new"
    assert ids[0] != ids[1]
    assert [app.store.get_loadout(item).name for item in ids] == ["First blade", "Second blade"]
    app.loadouts_tree.selection_set(str(ids[0]))
    app._load_selected_loadout()
    app.update()
    assert app.loadout_name.get() == "First blade"
    assert app.store.get_active_loadout().id == ids[0]
    assert not app.delete_loadout_button.instate(["disabled"])


@pytest.mark.parametrize("size", [(900, 600), (820, 540), (760, 500)])
def test_help_lower_controls_are_keyboard_reachable_and_local_settings_work(app, size):
    app.geometry(f"{round(size[0] * app.display_scale)}x{round(size[1] * app.display_scale)}")
    app.setup_tab.master.select(app.setup_tab)
    app.update()
    controls = (app.reduced_motion_button, app.refresh_data_button,
                app.feedback_sounds_button, app.preview_feedback_button)
    # Drive the real Tab binding (which lazily loads Tk's focus procedures),
    # not an early low-level Tcl query that may precede that autoload.
    controls[0].focus_force()
    app.update()
    reachable = set()
    for _ in range(sum(1 for _ in descendants(app)) + 1):
        current = app.focus_get()
        assert current is not None
        if current in reachable:
            break
        reachable.add(current)
        current.event_generate("<Tab>", when="tail")
        app.update()
    assert set(controls) <= reachable
    for widget in controls:
        app.help_scroll.canvas.yview_moveto(0)
        widget.event_generate("<FocusIn>")  # The scroll pane's real focus binding.
        app.update()
        assert_inside(widget, app.help_scroll.canvas)
        assert not widget.instate(["disabled"])
    app.reduced_motion_button.invoke()
    app.feedback_sounds_button.invoke()
    app.refresh_data_button.invoke()
    assert app.reduced_motion.get() and app.feedback_sounds.get()
    assert not app.running and app.session_id is None
    # Launching the actual demo child belongs to separately cleared visual
    # smoke, not a unit test that can unexpectedly open windows on another monitor.


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_main_fonts_and_small_form_ignore_process_startup_point_scaling(app, monkeypatch, scale):
    monkeypatch.setattr("ped_hunter.app.window_ui_scale", lambda _window: scale)
    original = float(app.tk.call("tk", "scaling"))
    try:
        app.tk.call("tk", "scaling", 2 * 96 / 72)
        app.display_scale = scale
        app.minsize(round(760 * scale), round(500 * scale))
        app._configure_theme()
        app.geometry(f"{round(820 * scale)}x{round(540 * scale)}")
        app.loadouts_tab.master.select(app.loadouts_tab)
        app.update()
        assert_inside(app.save_activate_button, app.loadouts_tab)
        rendered_metrics = {}
        for widget in app.loadout_fields.values():
            app.loadout_scroll.reveal(widget)
            app.update()
            assert_inside(widget, app.loadout_scroll.canvas)
            font_name = widget.cget("font")
            line_height = int(app.tk.call("font", "metrics", font_name, "-linespace"))
            rendered_metrics[widget] = (line_height, int(app.tk.call("font", "measure", font_name, "Hunting setup 012345")))
            assert widget.winfo_height() >= line_height
        assert float(app.tk.call("tk", "scaling")) == pytest.approx(2 * 96 / 72)
        app.tk.call("tk", "scaling", 96 / 72)
        app._configure_theme()
        app.update_idletasks()
        for widget, expected in rendered_metrics.items():
            font_name = widget.cget("font")
            assert (int(app.tk.call("font", "metrics", font_name, "-linespace")),
                    int(app.tk.call("font", "measure", font_name, "Hunting setup 012345"))) == expected
    finally:
        app.tk.call("tk", "scaling", original)


@pytest.mark.parametrize("size,largest", [((760, 500), 0.14), ((900, 600), 600.0)])
def test_event_chart_text_fits_without_covering_other_labels(app, size, largest):
    sid = app.store.start_session("hunt")
    for index, value in enumerate([0.04, 0.09, 0.025, 0.14, 0.06, largest]):
        timestamp = f"2026-11-08T12:{index:02d}:00"
        app.store.add_event(sid, {"kind": "combat", "timestamp": timestamp,
            "raw_message": "Synthetic shot", "payload": {"shot_cost": 0.051}})
        app.store.add_event(sid, {"kind": "loot", "timestamp": timestamp,
            "raw_message": "Synthetic loot", "payload": {"value": value, "item_name": "Animal Muscle Oil"}})
    app.session_id = sid
    app.currency_mode.set("PED")
    app._refresh_all()
    app.events_tab.master.select(app.events_tab)
    app.geometry(f"{round(size[0] * app.display_scale)}x{round(size[1] * app.display_scale)}")
    app.update()
    canvas = app.loot_chart_canvas
    boxes = [canvas.bbox(item) for item in canvas.find_all() if canvas.type(item) == "text"]
    for box in boxes:
        assert 0 <= box[0] < box[2] <= canvas.winfo_width()
        assert 0 <= box[1] < box[3] <= canvas.winfo_height()
    for index, box in enumerate(boxes):
        for other in boxes[index + 1:]:
            assert max(box[0], other[0]) >= min(box[2], other[2]) or max(box[1], other[1]) >= min(box[3], other[3])
