from types import SimpleNamespace

import pytest

from ped_hunter import app as appmod
from ped_hunter.storage import Store


@pytest.fixture
def app(tmp_path):
    application = appmod.PedHunterApp(store=Store(tmp_path / "placement.sqlite3"))
    yield application
    application.destroy()


def test_streamer_opens_at_bottom_right_after_content_fit(app, monkeypatch):
    bounds = (0, 0, 1920, 1032)  # taskbar excluded
    monkeypatch.setattr(appmod, "_streamer_work_area", lambda _: bounds, raising=False)
    app._open_streamer_window()
    app.update_idletasks()
    window = app.streamer_window
    assert window.winfo_x() + window.winfo_width() == bounds[2] - 16
    assert window.winfo_y() + window.winfo_height() == bounds[3] - 16
    assert window.state() == "normal"
    assert window.overrideredirect()


def test_content_refit_keeps_untouched_streamer_bottom_right(app, monkeypatch):
    bounds = (0, 0, 1920, 1032)
    monkeypatch.setattr(appmod, "_streamer_work_area", lambda _: bounds, raising=False)
    app._open_streamer_window()
    app.update_idletasks()
    window = app.streamer_window
    window.vars["loadout"].set("Tall setup\n" * 8)
    window._schedule_fit_to_contents()
    app.update_idletasks()
    assert window.winfo_y() + window.winfo_height() == bounds[3] - 16


def test_dragged_streamer_does_not_snap_back_during_refresh(app, monkeypatch):
    monkeypatch.setattr(appmod, "_streamer_work_area", lambda _: (0, 0, 1920, 1032), raising=False)
    app._open_streamer_window()
    app.update_idletasks()
    window = app.streamer_window
    window._start_drag(SimpleNamespace(x=10, y=10))
    window._drag(SimpleNamespace(x_root=210, y_root=160))
    app.update_idletasks()
    window._fit_to_contents()
    app.update_idletasks()
    assert (window.winfo_x(), window.winfo_y()) == (200, 150)


def test_manually_resized_streamer_keeps_its_position(app, monkeypatch):
    monkeypatch.setattr(appmod, "_streamer_work_area", lambda _: (0, 0, 1920, 1032), raising=False)
    app._open_streamer_window()
    app.update_idletasks()
    window = app.streamer_window
    original = window.winfo_x(), window.winfo_y()
    window._start_resize(SimpleNamespace(x_root=100, y_root=100))
    window._resize(SimpleNamespace(x_root=150, y_root=150))
    app.update_idletasks()
    window._fit_to_contents()
    app.update_idletasks()
    assert (window.winfo_x(), window.winfo_y()) == original


def test_actual_gui_uses_native_work_area(app):
    bounds = appmod._streamer_work_area(app)
    app._open_streamer_window()
    app.update_idletasks()
    window = app.streamer_window
    expected = appmod._bottom_right_position(bounds, window.winfo_width(), window.winfo_height())
    assert (window.winfo_x(), window.winfo_y()) == expected
    assert window.winfo_ismapped()


def test_reopening_streamer_uses_bottom_right_again(app, monkeypatch):
    monkeypatch.setattr(appmod, "_streamer_work_area", lambda _: (0, 0, 1920, 1032), raising=False)
    app._open_streamer_window()
    app.update_idletasks()
    previous = app.streamer_window
    previous.geometry("+200+150")
    previous.destroy()
    app._open_streamer_window()
    app.update_idletasks()
    window = app.streamer_window
    assert window is not previous
    assert window.winfo_y() + window.winfo_height() == 1032 - 16


@pytest.mark.parametrize("bounds,width,height,expected", [
    ((0, 0, 1920, 1032), 420, 300, (1484, 716)),
    ((1920, 40, 3840, 1080), 420, 300, (3404, 764)),
    ((-1920, -200, 0, 832), 420, 300, (-436, 516)),
    ((0, -1080, 1920, -48), 420, 300, (1484, -364)),
    ((50, 80, 350, 280), 420, 300, (50, 80)),
])
def test_bottom_right_coordinates_cover_taskbars_negative_monitors_and_small_screens(bounds, width, height, expected):
    assert appmod._bottom_right_position(bounds, width, height) == expected


def test_work_area_falls_back_to_tk_screen_without_native_backend():
    window = SimpleNamespace(tk=SimpleNamespace(call=lambda *_: "x11"),
                             winfo_screenwidth=lambda: 1600, winfo_screenheight=lambda: 900)
    assert appmod._streamer_work_area(window) == (0, 0, 1600, 900)
