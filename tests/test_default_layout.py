"""Native Tk checks for the single, space-efficient main layout."""
import tkinter as tk
from tkinter import ttk

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.storage import Store


@pytest.fixture
def app(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Native Tk display unavailable")
    root.destroy()
    window = PedHunterApp(store=Store(tmp_path / "layout.sqlite3"))
    window.update()
    yield window
    window.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_single_density_default(app):
    assert "compact_density" not in vars(app)
    assert not any(
        isinstance(widget, ttk.Checkbutton) and widget.cget("text") == "Compact"
        for widget in descendants(app)
    )
    assert app.winfo_width() == 1040
    assert app.winfo_height() == 640
    assert app.hero_panel.winfo_height() < 190
    assert app.hero_net_label.grid_info()["row"] == app.hero_return_label.grid_info()["row"]
    assert app.sessions_tree.winfo_height() >= 140


@pytest.mark.parametrize("size", ["1040x640", "980x580", "1180x720"])
def test_primary_controls_fit_and_top_toggle_restores(app, size):
    app.geometry(size)
    app.update()
    for widget in (app.start_button, app.resume_button, app.stop_button, app.status_label, app.hero_return_label):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() >= app.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    app._toggle_top_area()
    app.update()
    assert not app.hero_panel.winfo_ismapped()
    app._toggle_top_area()
    app.update()
    assert app.hero_panel.winfo_ismapped()
    assert app.controls_panel.winfo_ismapped()
