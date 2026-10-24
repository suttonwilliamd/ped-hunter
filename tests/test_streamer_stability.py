"""Live data must not be allowed to drive the streamer's geometry or typography."""
from dataclasses import replace
import time
from unittest.mock import Mock

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.storage import Store


@pytest.fixture
def hud(tmp_path):
    app = PedHunterApp(store=Store(tmp_path / 'stability.sqlite3'))
    app.reduced_motion.set(True)
    app._open_streamer_window()
    app.update()
    app.update()
    yield app, app.streamer_window
    app.destroy()


def positions(window):
    widgets = [window.hero, window.net_label, window.return_label, window.stats,
               *window.stat_cells, window.activity_strip, window.kill_chart,
               window.durability_bar, window.loadout_label, window.resize_handle]
    return tuple((w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()) for w in widgets)


def fonts(window):
    return tuple((str(font), font.cget('size')) for font, _ in window._fonts.values())


def field_fonts(window):
    return tuple((key, font.cget('size')) for key, (font, _) in window._fixed_fonts.items())


@pytest.mark.parametrize('size', [(360, 330), (260, 240), (540, 400)])
def test_live_digit_thresholds_long_activity_and_quiet_reset_keep_layout_still(hud, size):
    app, window = hud
    window.geometry(f'{round(size[0] * window.ui_scale)}x{round(size[1] * window.ui_scale)}')
    app.update()
    before = positions(window), fonts(window), field_fonts(window)
    for i in range(30):
        window.vars['net_big'].set('+$10.00' if i % 2 else '-$9.99')
        window.vars['return'].set('100.00%' if i % 2 else '99.99%')
        window.vars['loot'].set('$100.00' if i % 2 else '$99.99')
        window.vars['cost'].set('$99.99' if i % 2 else '$100.00')
        window.vars['kills'].set(str(9 if i % 2 else 1000))
        window.vars['kill_trend_badge'].set(
            f'K{9 if i % 2 else 1000} · Profit -0.05 PED · ↓ -0.10 vs prev\n'
            'Cost 0.12 · Cumulative avg cost 0.13 PED')
        strip = window.activity_strip
        strip.headline = '+0.05 PED loot recorded' if i % 2 else 'Waiting for new events'
        strip.detail = ('Very long synthetic skill name +1 · ' * 8 if i % 2 else 'Loot and skill gains appear here')
        strip._draw()
        app.update()
        assert (positions(window), fonts(window), field_fonts(window)) == before
    window.activity_strip._quiet()
    app.update()
    assert (positions(window), fonts(window), field_fonts(window)) == before


def test_data_changes_do_not_call_global_layout_or_scale_solver(hud):
    app, window = hud
    window._layout_values = Mock(wraps=window._layout_values)
    window._set_content_scale = Mock(wraps=window._set_content_scale)
    for i in range(12):
        window.vars['net_big'].set(f'+${i}.00')
        window.vars['loot'].set(f'${i * 10}.00')
        window.update_from_session(None)
        app.update()
    window._layout_values.assert_not_called()
    window._set_content_scale.assert_not_called()


def test_activity_strip_has_two_reserved_lines_even_for_long_text(hud):
    app, window = hud
    strip = window.activity_strip
    height = strip.winfo_height(), strip.winfo_reqheight()
    full = 'A long synthetic skill name +123 · ' * 15
    for detail in ('Short', full, 'Loot and skill gains appear here', full):
        strip.detail = detail
        strip._draw()
        app.update()
        assert (strip.winfo_height(), strip.winfo_reqheight()) == height
        assert strip.detail == detail  # Only presentation may ellipsize; retain the original.


def test_number_animation_does_not_relayout_between_frames(hud):
    app, window = hud
    before = positions(window), fonts(window), field_fonts(window)
    window.animator.number('stable-money', 0, lambda value: window.vars['net_big'].set(f'+${value:.2f}'), False)
    window.animator.number('stable-money', 100, lambda value: window.vars['net_big'].set(f'+${value:.2f}'), True)
    deadline = time.monotonic() + 2
    while window.animator.jobs and time.monotonic() < deadline:
        app.update()
        assert (positions(window), fonts(window), field_fonts(window)) == before
        time.sleep(0.01)
    assert not window.animator.jobs
    assert window.vars['net_big'].get() == '+$100.00'


def test_zero_cost_to_live_cost_does_not_move_sections(hud):
    app, window = hud
    sid = app.store.start_session('hunt')
    app.session_id, app.running = sid, True
    window.update_from_session(app.store.get_session(sid))
    app.update()
    before = positions(window), fonts(window)
    app.store.add_event(sid, {'kind': 'combat', 'raw_message': 'test shot', 'payload': {'shot_cost': 0.1, 'damage': 8}})
    app.store.add_event(sid, {'kind': 'loot', 'raw_message': 'test loot', 'payload': {'value': 0.15}})
    window.update_from_session(app.store.get_session(sid))
    app.update()
    assert (positions(window), fonts(window)) == before
    assert window.vars['return'].get() == '150.00%'


def test_large_money_fits_its_own_slot_without_rearranging_other_sections(hud):
    app, window = hud
    before = positions(window), fonts(window)
    sizes = []
    for value in ('+12345678.90 PED', '+0.01 PED', '+12345678.90 PED'):
        window.vars['net_big'].set(value)
        window.vars['loot'].set(value.lstrip('+'))
        window.vars['cost'].set(value.lstrip('+'))
        app.update()
        assert (positions(window), fonts(window)) == before
        assert window.vars['net_big'].get() == value
        sizes.append(field_fonts(window))
    assert sizes[0] == sizes[1] == sizes[2]  # No font-size see-saw after an overflow.
    import tkinter.font as tkfont
    font = tkfont.nametofont(window.net_label.cget('font'), root=window)
    assert font.measure(window.vars['net_big'].get()) <= window.net_label.winfo_width() - 4


def test_manufacturing_attempt_text_changes_do_not_relayout(hud):
    app, window = hud
    sid = app.store.start_session('craft')
    session = replace(app.store.get_session(sid), manufacturing_attempt=1,
                      manufacturing_attempts_total=1000, last_input_cost=0.12)
    window.update_from_session(session)
    app.update()
    before = positions(window), fonts(window)
    for attempt in (9, 10, 99, 100, 999):
        window.update_from_session(replace(session, manufacturing_attempt=attempt))
        app.update()
        assert (positions(window), fonts(window)) == before
        assert f'{attempt}/1000' in window.vars['input'].get()
