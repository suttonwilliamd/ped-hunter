"""Resize real Tk widgets through their event bindings, not just private geometry methods."""
import tkinter.font as tkfont

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.storage import Store


@pytest.fixture
def hud(tmp_path):
    app = PedHunterApp(store=Store(tmp_path / 'resize.sqlite3'))
    app.reduced_motion.set(True)
    app._open_streamer_window()
    app.update()  # Deliver Map events before generating real widget input.
    yield app, app.streamer_window
    app.destroy()


def gesture(app, window, width, height):
    grip = window.resize_handle
    old_width, old_height = window.winfo_width(), window.winfo_height()
    x, y = grip.winfo_rootx() + 2, grip.winfo_rooty() + 2
    grip.event_generate('<ButtonPress-1>', x=2, y=2, rootx=x, rooty=y)
    grip.event_generate('<B1-Motion>', rootx=x + width - old_width, rooty=y + height - old_height)
    app.update()  # Deliver native Configure events before checking responsive layout.
    grip.event_generate('<ButtonRelease-1>', rootx=x + width - old_width, rooty=y + height - old_height)
    app.update()


def test_default_hud_is_compact_at_current_display_scale(hud):
    app, window = hud
    assert window.winfo_width() / window.ui_scale <= 360
    assert window.winfo_height() / window.ui_scale <= 340


def test_long_wrapped_live_text_does_not_lock_in_enlarged_minimum(hud):
    app, window = hud
    window.vars['kill_trend_badge'].set(
        'K12345 · Profit +123.45 PED · ↑ +123.45 vs prev\n'
        'Cost 123.45 · Cumulative avg cost 123.45 PED')
    window._schedule_fit_to_contents()
    app.update_idletasks()
    minimum = window.minsize()
    gesture(app, window, window.winfo_width() + 350, window.winfo_height() + 180)
    window._schedule_fit_to_contents()  # Keep the same long live summary.
    app.update_idletasks()
    assert window.minsize() == minimum
    gesture(app, window, minimum[0], minimum[1])
    assert (window.winfo_width(), window.winfo_height()) == minimum


def test_user_size_survives_live_refreshes_and_changing_text(hud):
    app, window = hud
    width, height = round(300 * window.ui_scale), round(270 * window.ui_scale)
    scaling = float(app.tk.call('tk', 'scaling'))
    main_size = app.winfo_width(), app.winfo_height()
    gesture(app, window, width, height)
    assert float(app.tk.call('tk', 'scaling')) == scaling
    assert (app.winfo_width(), app.winfo_height()) == main_size
    for i in range(4):
        window.update_from_session(None)
        window.vars['durability'].set(f'Gun durability 81.2% · ~{1200+i:,} shots left')
        app.update_idletasks()
        assert (window.winfo_width(), window.winfo_height()) == (width, height)
    assert window.resize_handle.winfo_ismapped()
    assert window.loadout_label.winfo_rooty() + window.loadout_label.winfo_height() <= window.winfo_rooty() + height


@pytest.mark.parametrize('scale', [1.0, 1.25, 1.5, 2.0])
def test_dpi_scaling_is_applied_once_and_minimum_remains_reachable(hud, scale):
    app, old = hud
    old.destroy()
    app.tk.call('tk', 'scaling', scale * 96 / 72)
    app._open_streamer_window()
    app.update()
    window = app.streamer_window
    assert abs(window.winfo_width() - 360 * scale) <= 2
    assert abs(window.winfo_height() - 330 * scale) <= 2
    font = tkfont.nametofont(window.net_label.cget('font'), root=window)
    assert font.cget('size') < 0  # Local pixel font; no second Tk point scaling.
    assert abs(font.cget('size')) <= 26 * scale + 1
    minimum = window.minsize()
    gesture(app, window, *minimum)
    window.update_from_session(None)
    app.update()
    assert (window.winfo_width(), window.winfo_height()) == minimum
    assert window.resize_handle.winfo_ismapped()


def test_small_size_keeps_footer_controls_and_financial_values_visible(hud):
    app, window = hud
    sid = app.store.start_session('hunt')
    app.session_id, app.running = sid, True
    for width, height in [(300, 270), (520, 380), (260, 240), (360, 330)]:
        gesture(app, window, round(width * window.ui_scale), round(height * window.ui_scale))
        app.store.add_event(sid, {'kind': 'loot', 'raw_message': str(width), 'payload': {'value': 0.14}})
        window.update_from_session(app.store.get_session(sid))
        app.update()
        assert window.vars['state'].get() == 'TRACKING'
        assert window.vars['loot'].get().startswith('$')
        assert window.close_button.winfo_ismapped()
        assert window.resize_handle.winfo_ismapped()
        assert window.kill_chart.winfo_height() >= 16
        footer_bottom = window.loadout_label.winfo_rooty() + window.loadout_label.winfo_height()
        assert footer_bottom <= window.winfo_rooty() + window.winfo_height() - round(4 * window.ui_scale)


def test_resize_has_mouse_capture_and_releases_it(hud):
    app, window = hud
    grip = window.resize_handle
    grip.event_generate('<ButtonPress-1>', x=2, y=2)
    assert window.grab_current() == grip
    grip.event_generate('<ButtonRelease-1>', x=2, y=2)
    assert window.grab_current() is None
    assert window._resize_origin is None


@pytest.mark.parametrize('edge,dx,dy', [('e', 45, 0), ('s', 0, 45), ('w', -45, 0), ('n', 0, -45), ('nw', -45, -45)])
def test_edges_and_corners_resize_without_moving_the_opposite_edge(hud, edge, dx, dy):
    app, window = hud
    window.geometry('+200+200')
    app.update_idletasks()
    before = window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height()
    target = window.resize_edges[edge]
    target.event_generate('<ButtonPress-1>', x=2, y=2, rootx=220, rooty=220)
    target.event_generate('<B1-Motion>', rootx=220+dx, rooty=220+dy)
    target.event_generate('<ButtonRelease-1>', rootx=220+dx, rooty=220+dy)
    app.update_idletasks()
    x,y,w,h = before
    if 'w' in edge:
        assert window.winfo_x() + window.winfo_width() == x+w
    else:
        assert window.winfo_x() == x
    if 'n' in edge:
        assert window.winfo_y() + window.winfo_height() == y+h
    else:
        assert window.winfo_y() == y
    assert window.winfo_width() == w + abs(dx)
    assert window.winfo_height() == h + abs(dy)
