from __future__ import annotations

import pytest
from tkinter import ttk

from ped_hunter import app as appmod
from ped_hunter.storage import Store


@pytest.fixture
def app(tmp_path, monkeypatch):
    store = Store(tmp_path / "toggle.sqlite3")
    monkeypatch.setattr(appmod, "Store", lambda: store)
    application = appmod.PedHunterApp()
    yield application
    application.destroy()


def streamer_button(app):
    pending = list(app.winfo_children())
    while pending:
        widget = pending.pop()
        if isinstance(widget, ttk.Button) and widget.cget("text") == "Streamer UI":
            return widget
        pending.extend(widget.winfo_children())
    raise AssertionError("The real Streamer UI button was not found")


def live_overlays(app):
    return [widget for widget in app.winfo_children()
            if isinstance(widget, appmod.StreamerWindow) and widget.winfo_exists()]


def test_streamer_button_toggles_open_closed_and_reopens(app):
    button = streamer_button(app)
    assert app.streamer_window is None
    for _ in range(3):
        button.invoke()
        app.update_idletasks()
        window = app.streamer_window
        assert window is not None and window.winfo_exists()
        assert live_overlays(app) == [window]

        button.invoke()
        app.update_idletasks()
        assert not window.winfo_exists()
        assert app.streamer_window is None
        assert not live_overlays(app)
        assert app.winfo_exists()


def test_streamer_button_reopens_after_window_manager_close(app):
    button = streamer_button(app)
    button.invoke()
    app.update_idletasks()
    previous = app.streamer_window
    app.tk.call(previous.protocol("WM_DELETE_WINDOW"))
    app.update_idletasks()
    assert not previous.winfo_exists()

    button.invoke()
    app.update_idletasks()
    assert app.streamer_window is not previous
    assert live_overlays(app) == [app.streamer_window]
    button.invoke()
    assert not live_overlays(app)


def test_streamer_button_replaces_destroyed_window_reference(app):
    button = streamer_button(app)
    button.invoke()
    app.update_idletasks()
    previous = app.streamer_window
    previous.destroy()
    app.streamer_window = previous  # Simulate a destroyed window with stale state.

    button.invoke()
    app.update_idletasks()
    assert app.streamer_window is not previous
    assert live_overlays(app) == [app.streamer_window]
    button.invoke()
    assert not live_overlays(app)
