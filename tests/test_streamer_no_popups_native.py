"""Real Windows cursor/focus paths; run only with an exclusive desktop lease.

Synthetic private data. These tests never open production data or click Resume.
"""
import ctypes
from ctypes import wintypes
import json
import os
import time
import tkinter as tk
from tkinter import ttk

import pytest

from ped_hunter import app as appmod
from ped_hunter.storage import Store, LoadoutRecord
from test_event_exclusion import book, originals


def pump(app, seconds=.7):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(.01)


def descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from descendants(child)


@pytest.mark.parametrize('blueprint,shared', [
    ('Simple 1 Conductors Blueprint', False),
    ('Civil War Blast Exploding Blueprint', True),
])
def test_real_cursor_and_native_focus_never_create_popup_books(tmp_path, monkeypatch, blueprint, shared):
    assert os.name == 'nt', 'Windows native acceptance, not a simulated desktop pass'
    user = ctypes.windll.user32
    previous = wintypes.POINT()
    assert user.GetCursorPos(ctypes.byref(previous))
    store = Store(tmp_path / 'native-no-popup.sqlite3')
    gear = LoadoutRecord(None, blueprint + ' long saved equipment context', 'Frontier Rifle', cost_per_shot=.1)
    store.save_loadout(gear, make_active=True)
    sid = store.start_session('hunt', gear)
    if shared:
        from ped_hunter.hunt_attribution import HuntAttribution
        engine = HuntAttribution(store)
        engine.update(sid, appmod._kill_trend_points(store, sid), live=True)
        # Produce real mixed hunt/craft uncertainty, not a hard-coded note label.
        book(store, sid, 'combat', 0, damage=1, shot_cost=.1, loadout=gear.name)
        book(store, sid, 'loot', 1, value=.2, item_name='Synthetic hunting receipt')
        book(store, sid, 'loot', 4, value=.3, item_name='Synthetic shared receipt')
    plan_id = book(store, sid, 'manufacturing_offset', 6, blueprint=blueprint,
                   attempts_total=2 if shared else 15, cost_per_attempt=10 if shared else .7,
                   total_cost=20 if shared else 10.5)
    product = blueprint.removesuffix(' Blueprint')
    ids = []
    for i in range(1 if shared else 17):
        ids.append(book(store, sid, 'loot', 8 + i * 2,
                        item_name=product, value=3 if shared else 10.0249 / 17))
    # Real persisted fans, replayed by the actual HUD update.
    store.save_chart_trails(sid, {ids[-1]: [(10 if shared else .7, 10 if shared else .7, 0),
                                               (10 if shared else .7, 10 if shared else .7, 3 if shared else 10.0249 / 17)]})
    app = appmod.PedHunterApp(store=store)
    errors = []
    app.report_callback_exception = lambda *error: errors.append([str(x) for x in error])
    app.title('PED Hunter private native no-popup acceptance')
    app.reduced_motion.set(True)
    app.session_id, app.running = sid, True
    app.currency_mode.set('PED')
    app._refresh_all()
    app._open_streamer_window()
    app.update()
    hud = app.streamer_window
    rows = originals(store)
    try:
        assert hud.cost_title.cget('text') == 'Cost'
        assert ('1/2' if shared else '17/15') in hud.vars['input'].get()
        assert ('20.10 PED' if shared else '11.90 PED') in hud.vars['cost'].get()
        assert 'fans' in hud.chart_note.cget('text')
        if shared:
            assert '* Estimated/shared' in hud.chart_note.cget('text')
        assert hud.vars['loot'].get() not in ('—', '')
        assert hud._cost_display.excess_estimate == pytest.approx(0 if shared else 1.4)
        assert hud._cost_display.cost == pytest.approx(20.1 if shared else 11.9)
        before = {str(w) for w in descendants(app)}
        created = []
        original_top = tk.Toplevel.__init__
        def forbidden_top(self, *args, **kwargs):
            created.append('automatic Toplevel')
            raise AssertionError('Automatic popup creation on native hover/focus')
        monkeypatch.setattr(tk.Toplevel, '__init__', forbidden_top)
        picker = next(w for w in descendants(app) if isinstance(w, ttk.Combobox)
                      and str(w.cget('textvariable')) == str(app.currency_mode))
        evidence = []
        for mode in ('USD', 'PED'):
            app.currency_mode.set(mode)
            app.update()
            targets = [('chart area', hud.kill_chart), ('Observed/USD/Fans/estimated-shared', hud.chart_note),
                       ('bottom blueprint status', hud.loadout_label), ('craft input', hud.input_label),
                       ('chart summary', hud._fixed_labels['kill_trend_badge']),
                       *[(f'setup slot {i+1}', w) for i,w in enumerate(hud.setup_buttons)]]
            for name, widget in targets:
                assert widget.winfo_ismapped(), name
                hud.lift()
                app.update()
                x = widget.winfo_rootx() + max(1, widget.winfo_width() // 2)
                y = widget.winfo_rooty() + max(1, widget.winfo_height() // 2)
                assert user.SetCursorPos(x, y)
                pump(app)
                widget.focus_force()
                pump(app)
                assert not created and not errors, (name, created, errors)
                assert hud._loadout_tip is None
                assert {str(w) for w in descendants(app)} == before, name
                evidence.append({'mode':mode,'target':name,'widget':str(widget),'xy':[x,y],
                                 'focused':str(app.focus_get()),'note':hud.chart_note.cget('text')})
            # Sweep multiple actual canvas regions, not just a single center point.
            for fx,fy in ((.1,.1),(.5,.1),(.9,.1),(.1,.5),(.5,.5),(.9,.5),(.1,.9),(.5,.9),(.9,.9)):
                assert user.SetCursorPos(hud.kill_chart.winfo_rootx()+int(hud.kill_chart.winfo_width()*fx),
                                         hud.kill_chart.winfo_rooty()+int(hud.kill_chart.winfo_height()*fy))
                pump(app)
                assert not created and not errors and hud._loadout_tip is None
            # Raise only this private app to expose its real currency selector.
            app.lift()
            picker.focus_force()
            app.update()
            assert user.SetCursorPos(picker.winfo_rootx()+picker.winfo_width()//2,
                                     picker.winfo_rooty()+picker.winfo_height()//2)
            pump(app)
            assert not created and not errors
        monkeypatch.setattr(tk.Toplevel, '__init__', original_top)
        assert originals(store) == rows
        (tmp_path / 'native-hover-focus-evidence.json').write_text(json.dumps({
            'blueprint':blueprint,'shared':shared,'eventsUnchanged':True,'costCaption':hud.cost_title.cget('text'),
            'cost':hud.vars['cost'].get(),'returns':hud.vars['loot'].get(),'input':hud.vars['input'].get(),
            'nativeTargets':evidence,'automaticToplevels':created,'callbackErrors':errors,
            'clientDimensions':[hud.winfo_width(),hud.winfo_height()],
            'dpi':hud.winfo_fpixels('1i')},indent=2),encoding='utf-8')
    finally:
        app._stop_log_worker()
        app.animator.cancel_all()
        if hud.winfo_exists():
            hud.animator.cancel_all()
        app.destroy()
        user.SetCursorPos(previous.x, previous.y)
