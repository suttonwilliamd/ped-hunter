"""Saved-ID ordering and native HUD switches; real SQLite and isolated chat only."""
import json
import queue
import tkinter as tk

import pytest

from ped_hunter import app as module
from ped_hunter.storage import LoadoutRecord, Store
from test_log_ingestion import ScriptedStop, SHOT, LOOT, append
from test_setup_editor import editor


def add_setups(store, count):
    ids = []
    for index in range(count):
        blade = index == 1
        ids.append(store.save_loadout(LoadoutRecord(
            id=None, name=f"Setup {index + 1} · {'Blade' if blade else 'Rifle'}",
            weapon="SET Combat Blade (L)" if blade else "Frontier Rifle",
            ammo_burn=65 if blade else 100, decay=0.00001 if blade else 0.0002,
            cost_per_shot=0.00651 if blade else 0.0102,
            repair_shots=17 + index), make_active=index == 0))
    return ids


def domain_rows(store):
    """Full original values, excluding only the new presentation-order column."""
    with store.connect() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: [tuple(row) for row in conn.execute(
            'SELECT ' + ','.join('"' + r['name'] + '"' for r in conn.execute(f'PRAGMA table_info("{name}")')
                                if r['name'] != 'sort_order') + f' FROM "{name}" ORDER BY rowid')]
                for name in names}


def test_legacy_order_is_seeded_once_without_changing_any_original_value(tmp_path):
    store = Store(tmp_path / 'legacy.sqlite3')
    ids = add_setups(store, 6)
    sid = store.start_session('hunt', store.get_loadout(ids[0]))
    store.add_event(sid, {'kind': 'combat', 'raw_message': 'immutable original', 'payload': {'shot_cost': .0102}})
    with store.connect() as conn:
        conn.execute('ALTER TABLE loadouts DROP COLUMN sort_order')
        conn.execute("UPDATE loadouts SET updated_at='2026-01-01T01:00:00'")
        conn.execute("UPDATE loadouts SET updated_at='2026-01-02T01:00:00' WHERE id=?", (ids[-1],))
        conn.commit()
        expected = [r[0] for r in conn.execute('SELECT id FROM loadouts ORDER BY active DESC,updated_at DESC,name,id')]
    original = domain_rows(store)
    reopened = Store(store.db_path)
    assert [r.id for r in reopened.list_loadouts()] == expected
    assert domain_rows(reopened) == original
    requested = [expected[-1], *expected[:-1]]
    reopened.reorder_loadouts(requested)
    assert [r.id for r in Store(store.db_path).list_loadouts()] == requested
    assert domain_rows(reopened) == original


def test_order_survives_activation_rename_delete_append_and_reopen(tmp_path):
    store = Store(tmp_path / 'order.sqlite3')
    ids = add_setups(store, 6)
    requested = [ids[5], ids[2], ids[0], ids[4], ids[1], ids[3]]
    original = domain_rows(store)
    store.reorder_loadouts(requested)
    assert domain_rows(store) == original
    store.set_active_loadout(ids[1])
    renamed = store.get_loadout(ids[2]); renamed.name = 'Renamed without moving'
    store.save_loadout(renamed)
    assert [r.id for r in Store(store.db_path).list_loadouts()] == requested
    store.delete_loadout(ids[4])
    new = store.save_loadout(LoadoutRecord(id=None, name='Appended', weapon='Frontier Rifle'))
    assert [r.id for r in Store(store.db_path).list_loadouts()] == [i for i in requested if i != ids[4]] + [new]
    assert store.get_active_loadout().id == ids[1]


@pytest.mark.parametrize('requested', [[], [1], [1, 1], [1, 2, 999], [True, 2], ['1', 2]])
def test_invalid_or_stale_permutation_is_atomic(tmp_path, requested):
    store = Store(tmp_path / 'invalid.sqlite3'); add_setups(store, 2)
    original = domain_rows(store)
    with pytest.raises(ValueError):
        store.reorder_loadouts(requested)
    assert domain_rows(store) == original
    assert [r.id for r in Store(store.db_path).list_loadouts()] == [1, 2]


def test_empty_order_and_first_append(tmp_path):
    store = Store(tmp_path / 'empty.sqlite3'); original = domain_rows(store)
    store.reorder_loadouts([])
    assert domain_rows(store) == original
    ids = add_setups(store, 1)
    assert [r.id for r in Store(store.db_path).list_loadouts()] == ids


def test_startup_preserves_selected_next_setup_and_all_saved_values(tmp_path):
    store = Store(tmp_path / 'startup.sqlite3'); ids = add_setups(store, 2)
    sid = store.start_session('hunt', store.get_loadout(ids[0]))
    store.add_event(sid, {'kind':'combat','raw_message':'original hunt use','payload':{'shot_cost':.0102}})
    store.end_session(sid)
    store.set_active_loadout(ids[1])
    with store.connect() as conn:
        conn.execute("UPDATE loadouts SET updated_at='2000-01-01T00:00:00'")
        conn.commit()
    before = domain_rows(store)
    app = object.__new__(module.PedHunterApp); app.store = Store(store.db_path); app.catalog = module.Catalog.load()
    app.session_id = None
    app._restore_default_context()
    assert app.store.get_active_loadout().id == ids[1]
    assert domain_rows(app.store) == before
    assert app.store.get_session(sid).loadout_snapshot['id'] == ids[0]


def test_startup_falls_back_to_recorded_setup_only_when_no_active_choice(tmp_path):
    store = Store(tmp_path / 'fallback.sqlite3'); ids = add_setups(store, 2)
    sid = store.start_session('hunt', store.get_loadout(ids[0]))
    store.add_event(sid, {'kind':'combat','raw_message':'recorded use','payload':{'shot_cost':.0102}})
    store.end_session(sid)
    with store.connect() as conn:
        conn.execute('UPDATE loadouts SET active=0'); conn.commit()
        events = [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
        sessions = [tuple(row) for row in conn.execute('SELECT * FROM sessions ORDER BY id')]
    app = object.__new__(module.PedHunterApp); app.store = Store(store.db_path); app.catalog = module.Catalog.load()
    app.session_id = None
    app._restore_default_context()
    assert app.store.get_active_loadout().id == ids[0]
    with store.connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')] == events
        assert [tuple(row) for row in conn.execute('SELECT * FROM sessions ORDER BY id')] == sessions


def test_activation_cannot_change_equipment_during_pending_resume(editor):
    target = editor.store.save_loadout(LoadoutRecord(id=None, name='Other', weapon='Frontier Rifle'))
    before = domain_rows(editor.store)
    editor._resume_pending = queue.Queue()
    assert not editor._activate_saved_loadout(target)
    assert domain_rows(editor.store) == before
    assert 'Restoring' in editor.status_text.get()


def test_switch_uses_fresh_saved_target_after_unsaved_save_prompt(editor, monkeypatch):
    target = editor.store.save_loadout(LoadoutRecord(
        id=None, name='Edited blade', weapon='SET Combat Blade (L)',
        ammo_burn=65, decay=.00001, cost_per_shot=.00651))
    editor._populate_loadout_form(editor.store.get_loadout(target))
    editor.damage_enhancers.set('2')
    monkeypatch.setattr(module.messagebox, 'askyesnocancel', lambda *_args, **_kwargs: True)
    assert editor._activate_saved_loadout(target)
    current = editor.store.get_active_loadout()
    assert current.id == target and current.damage_enhancers == 2
    assert current.cost_per_shot > .00651
    assert editor.damage_enhancers.get() == '2'


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    module.enable_high_dpi()
    chat = tmp_path / 'synthetic-chat.log'; chat.touch()
    monkeypatch.setattr(module, '_default_chat_log_path', lambda: chat)
    try:
        app = module.PedHunterApp(store=Store(tmp_path / 'native.sqlite3'))
    except tk.TclError as error:
        pytest.skip(f'Native display unavailable: {error}')
    app.title('PED010 ISOLATED pytest'); app.reduced_motion.set(True)
    app.loadouts_tab.master.select(app.loadouts_tab); app.update()
    yield app, chat
    app.running = False
    app.destroy()


def open_hud(app, count):
    ids = add_setups(app.store, count)
    app._refresh_loadouts(); app._open_streamer_window(); app.update()
    return ids, app.streamer_window


@pytest.mark.parametrize('count', [0, 1, 3, 4, 6])
def test_slots_follow_saved_order_and_disable_missing_entries(native_app, count):
    app, _ = native_app; ids, hud = open_hud(app, count)
    assert app.loadouts_tree.get_children()[0] == 'new'
    for index, button in enumerate(hud.setup_buttons):
        if index < count:
            assert str(button.cget('state')) == 'normal'
            assert button.cget('text').startswith(str(index + 1))
            tag = app.loadouts_tree.item(str(ids[index]), 'tags')[0]
            assert str(app.loadouts_tree.tag_configure(tag, 'foreground')) == str(button.cget('foreground'))
        else:
            assert str(button.cget('state')) == 'disabled'
            assert 'Empty' in button.cget('text')
    if count:
        assert '✓' in hud.setup_buttons[0].cget('text')
        if count > 4:
            app._activate_saved_loadout(ids[-1]); app.update()
            assert not any('✓' in b.cget('text') for b in hud.setup_buttons)
            assert app.store.get_active_loadout().id == ids[-1]
            assert hud._setup_slot_ids == ids[:4]
            assert app.store.get_loadout(ids[-1]).name in hud.vars['loadout'].get()


@pytest.mark.parametrize('scale,width,height', [(1,360,330),(1.25,450,413),(2,720,660),(2,520,480)])
def test_native_header_buttons_and_metrics_fit_without_overlap(native_app, monkeypatch, scale, width, height):
    app, _ = native_app
    monkeypatch.setattr(module, 'window_ui_scale', lambda _window: scale)
    ids, hud = open_hud(app, 6)
    sid = app.store.start_session('hunt', app.store.get_loadout(ids[0]))
    hud.update_from_session(app.store.get_session(sid))
    hud.geometry(f'{width}x{height}+100+100'); app.update(); hud._layout_values(); app.update()
    boxes = []
    for widget in [*hud.setup_buttons, hud.net_caption, hud.return_caption, hud.net_label, hud.return_label]:
        assert widget.winfo_ismapped()
        x, y, w, h = widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()
        assert w > 5 and h > 5
        assert hud.winfo_rootx() <= x < x + w <= hud.winfo_rootx() + hud.winfo_width()
        assert hud.winfo_rooty() <= y < y + h <= hud.winfo_rooty() + hud.winfo_height()
        variable = widget.cget('textvariable')
        text = widget.getvar(variable) if variable else widget.cget('text')
        if widget in (hud.net_caption, hud.return_caption):
            wrap = int(widget.cget('wraplength'))
            assert 0 < wrap <= w
            assert all(int(widget.tk.call('font', 'measure', widget.cget('font'), word)) <= wrap for word in text.split())
            assert widget.winfo_reqheight() <= h
        else:
            assert int(widget.tk.call('font', 'measure', widget.cget('font'), text)) <= w
        boxes.append((x,y,x+w,y+h))
    for index, a in enumerate(boxes):
        for b in boxes[index+1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_native_drag_keeps_new_pinned_and_draft_active_history_unchanged(native_app, monkeypatch):
    app, _ = native_app; ids, hud = open_hud(app, 6)
    app.loadout_name.set('Unsaved draft must survive')
    original = domain_rows(app.store); tree = app.loadouts_tree
    prompts = []
    monkeypatch.setattr(module.messagebox, 'askyesnocancel',
                        lambda *_args, **_kwargs: prompts.append('unexpected drag prompt') or None)
    tree.see(str(ids[-1])); app.update()
    source = tree.bbox(str(ids[-1])); target = tree.bbox(str(ids[0]))
    assert source and target
    tree.event_generate('<ButtonPress-1>', x=40, y=source[1]+source[3]//2)
    tree.event_generate('<B1-Motion>', x=40, y=target[1]+target[3]//2)
    tree.event_generate('<ButtonRelease-1>', x=40, y=target[1]+target[3]//2)
    app.update()
    expected = [ids[-1], *ids[:-1]]
    assert [r.id for r in Store(app.store.db_path).list_loadouts()] == expected
    assert list(tree.get_children()) == ['new', *map(str, expected)]
    assert hud._setup_slot_ids == expected[:4]
    assert app.loadout_name.get() == 'Unsaved draft must survive'
    assert app.selected_loadout_id is None
    assert domain_rows(app.store) == original
    # The pinned draft is not an orderable saved entry.
    first = tree.bbox('new'); second = tree.bbox(str(ids[0])); assert first and second
    tree.event_generate('<ButtonPress-1>', x=40, y=first[1]+first[3]//2)
    tree.event_generate('<B1-Motion>', x=40, y=second[1]+second[3]//2)
    app.update()  # A real event loop between motion/release must not select a hovered row.
    tree.event_generate('<ButtonRelease-1>', x=40, y=second[1]+second[3]//2)
    app.update()
    assert list(tree.get_children()) == ['new', *map(str, expected)]
    assert domain_rows(app.store) == original
    source = tree.bbox(str(ids[-2])); target = tree.bbox(str(ids[0]))
    assert source and target
    tree.event_generate('<ButtonPress-1>', x=40, y=source[1]+source[3]//2)
    tree.event_generate('<B1-Motion>', x=40, y=target[1]+target[3]//2)
    app._cancel_loadout_drag(); app.update()
    tree.event_generate('<ButtonRelease-1>', x=40, y=target[1]+target[3]//2)
    app.update()
    assert list(tree.get_children()) == ['new', *map(str, expected)]
    assert domain_rows(app.store) == original
    assert app.loadout_name.get() == 'Unsaved draft must survive'
    assert not prompts


def test_active_slot_preserves_dirty_draft_and_cancelled_switch_preserves_all_data(native_app, monkeypatch):
    app, _ = native_app; ids, hud = open_hud(app, 4)
    app.loadout_name.set('Do not discard this new draft')
    original = domain_rows(app.store)
    hud.setup_buttons[0].invoke()
    assert domain_rows(app.store) == original
    assert app.selected_loadout_id is None
    assert app.loadout_name.get() == 'Do not discard this new draft'
    monkeypatch.setattr(module.messagebox, 'askyesnocancel', lambda *_args, **_kwargs: None)
    hud.setup_buttons[1].invoke()
    assert domain_rows(app.store) == original
    assert app.loadout_name.get() == 'Do not discard this new draft'


def test_native_hud_switch_prices_only_subsequent_actual_file_events(native_app):
    app, chat = native_app; ids, hud = open_hud(app, 4)
    sid = app.store.start_session('hunt', app.store.get_loadout(ids[0]))
    app.session_id, app.running = sid, True
    app._log_queue = queue.Queue()
    first_rows = []
    def activate():
        with app.store.connect() as conn:
            first_rows.extend(tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id'))
        hud.setup_buttons[1].invoke()
        assert app.store.get_active_loadout().id == ids[1]
        assert app.store.get_session(sid).loadout_snapshot['id'] == ids[1]
        assert app.store.get_loadout(ids[1]).name in app.getvar(app.hero_session_label.cget('textvariable'))
    steps = [lambda: append(chat, SHOT), activate,
             lambda: append(chat, SHOT.replace(b'04:36:26', b'04:36:27') + LOOT)]
    app._log_worker_loop(chat, sid, 0, ScriptedStop(steps)); app.update()
    with app.store.connect() as conn:
        rows = [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
        payloads = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM events WHERE kind='combat' ORDER BY id")]
    assert rows[:len(first_rows)] == first_rows
    assert [p['shot_cost'] for p in payloads] == pytest.approx([.0102, .00651])
    assert [p['ammo_cost'] for p in payloads] == pytest.approx([.01, .0065])
    summary = app.store.get_session(sid)
    assert (summary.events, summary.hunting_cost, summary.loot_value) == pytest.approx((3,.01671,.0527))
    assert hud._setup_slot_ids == ids
    assert '✓' in hud.setup_buttons[1].cget('text')
    assert '✓' not in hud.setup_buttons[0].cget('text')
