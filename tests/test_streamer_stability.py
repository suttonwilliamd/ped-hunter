"""Live data must not be allowed to drive the streamer's geometry or typography."""
from dataclasses import replace
import time

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
               *window.stat_cells, window.kill_chart,
               window.durability_bar, window.loadout_label, window.resize_handle]
    return tuple((w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()) for w in widgets)


def fonts(window, exclude=()):
    """Measure rendered widget typography, not the chart's font-cache entries."""
    pending = [window]
    excluded_vars = {str(window.vars[key]) for key in exclude}
    result = []
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if 'font' in widget.keys():
            if 'textvariable' in widget.keys() and str(widget.cget('textvariable')) in excluded_vars:
                continue
            result.append((str(widget), widget.tk.call('font', 'actual', widget.cget('font'))))
    return tuple(result)


@pytest.mark.parametrize('size', [(360, 330), (260, 240), (540, 400)])
def test_live_digit_thresholds_keep_layout_still(hud, size):
    app, window = hud
    window.geometry(f'{round(size[0] * window.ui_scale)}x{round(size[1] * window.ui_scale)}')
    app.update()
    before = positions(window), fonts(window)
    for i in range(30):
        window.vars['net_big'].set('+$10.00' if i % 2 else '-$9.99')
        window.vars['return'].set('100.00%' if i % 2 else '99.99%')
        window.vars['loot'].set('$100.00' if i % 2 else '$99.99')
        window.vars['cost'].set('$99.99' if i % 2 else '$100.00')
        window.vars['kills'].set(str(9 if i % 2 else 1000))
        window.vars['kill_trend_badge'].set(
            f'K{9 if i % 2 else 1000} · Profit -0.05 PED · ↓ -0.10 vs prev\n'
            'Cost 0.12 · Cumulative avg cost 0.13 PED')
        app.update()
        assert (positions(window), fonts(window)) == before


def test_number_animation_does_not_relayout_between_frames(hud):
    app, window = hud
    before = positions(window), fonts(window)
    window.animator.number('stable-money', 0, lambda value: window.vars['net_big'].set(f'+${value:.2f}'), False)
    window.animator.number('stable-money', 100, lambda value: window.vars['net_big'].set(f'+${value:.2f}'), True)
    deadline = time.monotonic() + 2
    while window.animator.jobs and time.monotonic() < deadline:
        app.update()
        assert (positions(window), fonts(window)) == before
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
    import tkinter.font as tkfont

    app, window = hud
    money_fields = ('net_big', 'loot', 'cost')
    before = positions(window), fonts(window, exclude=money_fields)
    sizes = []
    for value in ('+12345678.90 PED', '+0.01 PED', '+12345678.90 PED'):
        window.vars['net_big'].set(value)
        window.vars['loot'].set(value.lstrip('+'))
        window.vars['cost'].set(value.lstrip('+'))
        app.update()
        assert (positions(window), fonts(window, exclude=money_fields)) == before
        assert window.vars['net_big'].get() == value
        font = tkfont.nametofont(window.net_label.cget('font'), root=window)
        assert font.measure(value) <= window.net_label.winfo_width() - 4
        sizes.append(abs(font.cget('size')))
    # A field may fit its own font, but cannot shrink unrelated labels or leave
    # the next short value permanently tiny after an unusually large balance.
    assert sizes[0] == sizes[2] <= sizes[1]


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


@pytest.mark.parametrize('size', [(360, 330), (260, 240)])
@pytest.mark.parametrize('crafting', [False, True])
def test_chart_or_crafting_inputs_follow_metrics_without_an_event_feed_gap(hud, size, crafting):
    app, window = hud
    sid = app.store.start_session('hunt')
    app.session_id, app.running = sid, True
    if crafting:
        app.store.add_event(sid, {'kind': 'manufacturing_offset', 'raw_message': 'private plan',
                                 'payload': {'attempts_total': 9, 'cost_per_attempt': .03025,
                                             'total_cost': .27225, 'blueprint': 'Private blueprint'}})
    window.update_from_session(app.store.get_session(sid))
    window.geometry(f'{round(size[0] * window.ui_scale)}x{round(size[1] * window.ui_scale)}')
    app.update()
    following = window.input_slot if crafting else window.trend
    gap = following.winfo_rooty() - window.stats.winfo_rooty() - window.stats.winfo_height()
    assert 0 <= gap <= round(5 * window._content_scale) + 1
    for widget in (following, window.kill_chart, window.footer):
        assert widget.winfo_ismapped()
        assert widget.winfo_rooty() >= window.winfo_rooty()
        assert widget.winfo_rooty() + widget.winfo_height() <= window.winfo_rooty() + window.winfo_height()
