"""Offline warning/layout and automatic-popup contracts; no Tk interpreter/window."""
import ast
from copy import deepcopy
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ped_hunter import app as module
from ped_hunter.activity_trend import ActivityPoints
from ped_hunter.storage import LoadoutRecord, Store

LEGACY = "Legacy healing has no saved allocation context; not backfilled"


def pending_points():
    points = ActivityPoints()
    points.healing_pending = .04
    points.healing_pending_reasons = {LEGACY: .04}
    points.attribution_pending = .04
    return points


@pytest.mark.parametrize("mode,amount", [("PED", "0.04 PED"), ("USD", "$0.004")])
@pytest.mark.parametrize("state", ["empty", "priced", "shared", "unknown"])
def test_complete_legacy_warning_in_every_summary_branch(mode, amount, state):
    points = pending_points()
    if state != "empty":
        points.append(dict(kill=1, label="K1", kind="hunt", cost=.1, profit=.2, loot=.3,
                           avg_cost=.1, delta_profit=None, cost_status=None))
        if state == "shared":
            points[-1]["cost_status"] = "shared_ambiguous"
        if state == "unknown":
            points[-1].update(cost=None, profit=None)
    original = deepcopy(points.__dict__)
    for text in (module._healing_status_text(points, mode),
                 module._streamer_trend_detail(points, mode),
                 module._kill_trend_summary(points, mode)):
        assert amount in text
        assert "already in totals" in text
        assert "no saved allocation context; not backfilled" in text
        assert "No new charge or lost data." in text
        assert "…" not in text
    assert points.__dict__ == original


def test_mixed_reasons_do_not_present_every_pending_cost_as_legacy():
    points = pending_points()
    points.healing_pending_reasons["Mixed evidence"] = .02
    points.healing_pending += .02
    points.unpriced_heals = 2
    text = module._healing_status_text(points, "PED")
    assert "0.06 PED" in text and "Legacy included" in text
    assert "2 heal obs. unpriced (cost unknown)" in text
    points.healing_pending_reasons = {"External caster cannot be resolved from receipts": .06}
    text = module._healing_status_text(points, "PED")
    assert "Allocation uncertain" in text and "Legacy" not in text
    points.healing_pending = 0
    assert module._healing_status_text(points, "PED") == "2 heal obs. unpriced (cost unknown)"
    points.unpriced_heals = 0
    assert module._healing_status_text(points, "PED") == ""


class Font:
    def __init__(self, size=10):
        self.size = size

    def measure(self, text):
        return len(text) * abs(self.size) / 2

    def configure(self, *, size):
        self.size = size

    def metrics(self, _key):
        return abs(self.size) + 2


@pytest.mark.parametrize("width", [120, 240, 342, 700])
def test_warning_wrap_height_is_content_based_not_32_chars_or_two_lines(width):
    font = Font()
    text = module._streamer_trend_detail(pending_points(), "PED")
    hud = SimpleNamespace(vars={"kill_trend_badge": SimpleNamespace(get=lambda: text)},
                          _fixed_fonts={"kill_trend_badge": (font, 10)}, content_width=width)
    lines = module.StreamerWindow._summary_lines(hud)
    assert lines >= len(text.splitlines()) > 2
    assert lines == module._wrapped_line_count(text, font, width - 4)
    # No missing final line even when a long token has to be split.
    assert module._wrapped_line_count("x" * 101, font, 50) == 11
    assert module._wrapped_line_count("\n", font, 50) == 2
    assert font.size == 10
    module.StreamerWindow._fit_fixed_text(hud, "kill_trend_badge")
    assert font.size == 10  # This field is never fitted down to a tiny font.


def test_summary_relayout_only_when_wrapped_height_changes():
    hud = SimpleNamespace(_layout_busy=False, _summary_lines=Mock(return_value=5),
                          _summary_layout_lines=2, _layout_geometry_key=(360, 330, False),
                          _schedule_fit_to_contents=Mock(), _fit_fixed_text=Mock())
    module.StreamerWindow._on_metric_change(hud, "kill_trend_badge")
    assert hud._layout_geometry_key is None
    hud._schedule_fit_to_contents.assert_called_once()
    hud._summary_layout_lines = 5
    module.StreamerWindow._on_metric_change(hud, "kill_trend_badge")
    hud._schedule_fit_to_contents.assert_called_once()
    hud._fit_fixed_text.assert_not_called()
    module.StreamerWindow._on_metric_change(hud, "cost")
    hud._fit_fixed_text.assert_called_once_with("cost")


@pytest.mark.parametrize("details", [False, True])
def test_automatic_summaries_bound_even_user_supplied_multiline_names(details):
    text = module._automatic_context_summary("Setup\n" + "very long " * 500, details=details)
    assert len(text.splitlines()[0]) <= 96
    assert len(text.splitlines()) == (2 if details else 1)
    assert len(text) <= 130
    assert "Click / Enter" in text if details else "Click / Enter" not in text


def test_all_automatic_popup_bindings_are_removed_including_names():
    tree = ast.parse(inspect.getsource(module))
    # Audit direct chart / note callers; only Configure may invoke drawing.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "bind":
            receiver = ast.unparse(node.func.value)
            if receiver in {"self.kill_chart", "self.chart_note"}:
                assert ast.literal_eval(node.args[0]) not in {"<Enter>", "<FocusIn>"}
    build = inspect.getsource(module.StreamerWindow._build)
    assert 'for event in ("<Button-1>", "<Return>", "<space>")' in build
    assert "self._show_context_details" in build
    assert "damage_slot" not in build
    # Preserve beloved 2x2 and activation keys, rather than converting slots to details buttons.
    assert "row=index // 2, column=index % 2" in build
    assert 'button.bind("<Return>"' in build and "button.invoke()" in build
    assert "self._activate_setup_slot(index)" in build
    show = inspect.getsource(module.StreamerWindow._show_loadout)
    assert 'Toplevel' not in show and '_automatic_context_summary' not in show
    assert '_show_loadout' not in build and '_show_setup_slot' not in build


def test_slot_activation_and_popup_use_exact_saved_id_not_presentation_name():
    hud = SimpleNamespace(_setup_slot_ids=[31, 8, None, 50],
                          _setup_slot_labels=["1 name", "2 active", "3 Empty", "4 last"],
                          _show_loadout=Mock(),
                          app=SimpleNamespace(store=SimpleNamespace(get_active_loadout=lambda: SimpleNamespace(id=8)),
                                              _activate_saved_loadout=Mock()))
    module.StreamerWindow._show_setup_slot(hud, 0)
    hud._show_loadout.assert_called_once_with(text="1 name")
    module.StreamerWindow._show_setup_slot(hud, 2)
    hud._show_loadout.assert_called_once()
    for slot in range(4):
        module.StreamerWindow._activate_setup_slot(hud, slot)
    assert hud.app._activate_saved_loadout.call_args_list == [((31,),), ((50,),)]


def test_explicit_details_are_structured_current_facts_not_relocated_manual(monkeypatch):
    points = pending_points()
    plan = SimpleNamespace(blueprint="Synthetic blueprint", per_attempt=10, observed=1,
                           attempts=2, booked_cost=20, returns=25)
    old = deepcopy(points.__dict__)
    for mode, price, total in [("PED", "10.00 PED", "20.00 PED"), ("USD", "$1.00", "$2.00"),
                               ("PED", "10.00 PED", "20.00 PED")]:
        text = module._context_details("Synthetic setup", points, mode, plan=plan, unpriced_groups=1)
        assert price in text and total in text
        assert "SETUP\n" in text and "CRAFT BATCH\n" in text and "HEALING\n" in text
        assert "1 historical craft groups" in text
        assert "No new charge or lost data." in text
        assert len(text) < 1000
        assert "hysteresis" not in text and "4096" not in text and "1024" not in text
        showinfo = Mock()
        monkeypatch.setattr(module.messagebox, "showinfo", showinfo)
        hud = SimpleNamespace(_footer_detail=text, _hide_loadout=Mock())
        assert module.StreamerWindow._show_context_details(hud) == "break"
        hud._hide_loadout.assert_called_once()
        showinfo.assert_called_once_with("Current chart context", text, parent=hud)
    assert points.__dict__ == old


def test_private_legacy_expenses_and_original_rows_conserved_across_presentations(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    gear = LoadoutRecord(None, "Synthetic", "Frontier Rifle", ammo_burn=100,
                         decay=.0002, cost_per_shot=.0102)
    sid = store.start_session("hunt", gear)
    store.add_event(sid, dict(kind="combat", raw_message="Synthetic retained legacy heal",
                             payload={"healing_action": "self", "healing_cost": .04,
                                      "healing_cost_status": "estimated"}))
    with store.connect() as conn:
        original = [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
    points = module._kill_trend_points(store, sid)
    assert points.healing_pending == pytest.approx(.04)
    for mode in ("PED", "USD", "PED", "USD"):
        assert "no saved allocation context; not backfilled" in module._streamer_trend_detail(points, mode)
        module._context_details("Synthetic", points, mode)
        assert store.get_session(sid).healing_cost == pytest.approx(.04)
        assert points.healing_allocated + points.healing_pending == pytest.approx(.04)
        with store.connect() as conn:
            assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == original


@pytest.mark.parametrize('width,scale', [(240, .75), (342, 1), (680, 1.5)])
def test_layout_allocates_every_warning_line_without_borrowing_damage_or_status_lane(width, scale):
    class Node:
        def __init__(self, height=1):
            self.options = {'height': height}

        def configure(self, **kw):
            self.options.update(kw)

        def cget(self, key):
            return self.options[key]

        def pack_info(self):
            return {'pady': (0, 0), 'ipady': (0, 0)}

        def winfo_manager(self):
            return ''

        grid_configure = columnconfigure = rowconfigure = lambda *args, **kw: None

    body = Font(round(10 * scale))
    warning_font = Font()
    hud = SimpleNamespace(_content_scale=scale, ui_scale=1, content_width=width,
                          _font=lambda size=9, bold=False: body,
                          _fit_setup_labels=Mock(), _fit_fixed_text=Mock(),
                          _fixed_fonts={'kill_trend_badge': (warning_font, 10)},
                          _fixed_labels={'kill_trend_badge': Node()}, stat_cells=[],
                          vars={'kill_trend_badge': SimpleNamespace(get=lambda: module._streamer_trend_detail(pending_points(), 'PED'))})
    for name in ('header', 'hero', 'setup_slot_frame', 'stats', 'input_slot', 'chart_header',
                 'summary_slot', 'trend', 'durability_text_slot', 'durability_section', 'footer'):
        setattr(hud, name, Node())
    hud.kill_chart, hud.durability_bar = Node(54), Node(6)
    hud.outer = SimpleNamespace(pack_slaves=lambda: [hud.header, hud.hero, hud.stats, hud.trend,
                                                    hud.durability_section, hud.footer])
    hud._summary_lines = lambda: module.StreamerWindow._summary_lines(hud)
    required = module.StreamerWindow._layout_metrics(hud, width)
    assert abs(warning_font.size) >= 10
    assert hud.summary_slot.cget('height') == hud._summary_lines() * warning_font.metrics('linespace') + 2
    assert hud._fixed_labels['kill_trend_badge'].options['wraplength'] == width - 4
    for inset in ('bd', 'highlightthickness', 'padx', 'pady'):
        assert hud._fixed_labels['kill_trend_badge'].options[inset] == 0
    assert hud.summary_slot.cget('height') > 2 * warning_font.metrics('linespace')
    assert required >= hud.summary_slot.cget('height') + 54
    assert not hasattr(hud, 'damage_slot')


def test_explicit_pending_breakdown_preserves_mixed_amounts_and_meaning():
    points = pending_points()
    points.healing_pending_reasons.update({
        'Awaiting upcoming loot; no recipient is assigned yet': .02,
        'External/overlapping effect evidence makes the activation recipient uncertain': .03,
        'Encounter timing is incomplete or out of order': .01,
    })
    points.healing_pending = .1
    for mode, legacy, waiting in [('PED', '0.04 PED', '0.02 PED'), ('USD', '$0.004', '$0.002')]:
        text = module._context_details('Synthetic', points, mode)
        assert f'Legacy context missing (no backfill): {legacy}' in text
        assert f'Awaiting upcoming loot: {waiting}' in text
        assert 'External/overlapping effect evidence' in text
        assert 'Incomplete/out-of-order timing' in text
        assert len(text) < 1000


def test_height_deficit_raises_content_minimum_instead_of_clipping_or_tiny_font():
    hud = SimpleNamespace(winfo_width=lambda: 260, winfo_height=lambda: 240, ui_scale=1,
                          _set_content_scale=Mock(), _content_scale=.55,
                          _layout_metrics=Mock(return_value=298), minsize=Mock(),
                          _fixed_labels={}, _update_loadout_text=Mock(),
                          _fit_hero_captions=Mock(), _draw_kill_trend_chart=Mock())
    module.StreamerWindow._layout_values(hud)
    hud.minsize.assert_called_once_with(260, 298)
    assert hud._layout_metrics.call_count <= 4
    hud._layout_metrics.return_value = 220
    hud.minsize.reset_mock()
    module.StreamerWindow._layout_values(hud)
    hud.minsize.assert_not_called()  # No floor from a prior user enlargement.


class BindingWidget:
    """Tk-free adapter: execute production widget construction/bind dispatch, never map."""
    def __init__(self, parent=None, **options):
        self.parent, self.options = parent, options
        self.children, self.bindings, self.manager = [], {}, ''
        self.spacing = {key: (0, 0) for key in ('padx', 'pady', 'ipadx', 'ipady')}
        if parent is not None:
            parent.children.append(self)

    def configure(self, **options):
        self.options.update(options)

    def cget(self, key):
        return self.options.get(key)

    def bind(self, event, callback=None, add=None):
        if callback is None:
            return self.bindings.get(event, ())
        if not add:
            self.bindings[event] = []
        self.bindings.setdefault(event, []).append(callback)

    def dispatch(self, event):
        for callback in self.bindings.get(event, ()):
            if callback(SimpleNamespace(widget=self)) == 'break':
                break

    def pack(self, **options):
        self.manager = 'pack'
        self.spacing.update({key: value if isinstance(value, tuple) else (value, value)
                             for key, value in options.items() if key in self.spacing})

    def grid(self, **options):
        self.manager = 'grid'

    def pack_forget(self):
        self.manager = ''

    def winfo_children(self):
        return self.children

    def winfo_manager(self):
        return self.manager

    def pack_info(self):
        return self.spacing

    def invoke(self):
        return self.options['command']()

    grid_propagate = pack_propagate = rowconfigure = columnconfigure = lambda *a, **kw: None
    withdraw = overrideredirect = attributes = update_idletasks = geometry = deiconify = destroy = lambda *a, **kw: None
    winfo_x = winfo_y = lambda self: 0
    winfo_reqheight = lambda self: 40


class BindingValue:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.mark.parametrize('fans', [False, True])
def test_runtime_estimated_shared_hover_with_civil_war_blueprint_is_popup_free(tmp_path, monkeypatch, fans):
    """Execute the actual _build/update methods, not a literal-text-only binding audit."""
    from types import MethodType
    from ped_hunter.hunt_attribution import HuntAttribution
    from test_event_exclusion import book, originals

    blueprint = 'Civil War Blast Exploding Blueprint'
    store = Store(tmp_path / 'estimated-shared-crafting.sqlite3')
    gear = LoadoutRecord(None, 'Synthetic hunting setup', 'Frontier Rifle', cost_per_shot=.1)
    store.save_loadout(gear, make_active=True)
    slot = store.save_loadout(LoadoutRecord(None, blueprint, 'Synthetic weapon', cost_per_shot=.1))
    sid = store.start_session('hunt', gear)
    engine = HuntAttribution(store)
    engine.update(sid, module._kill_trend_points(store, sid), live=True)
    book(store, sid, 'combat', 0, damage=1, shot_cost=.1, loadout=gear.name)
    book(store, sid, 'loot', 1, value=.2, item_name='Synthetic hunting receipt')
    book(store, sid, 'loot', 4, value=.3, item_name='Synthetic shared receipt')
    book(store, sid, 'manufacturing_offset', 6, blueprint=blueprint, attempts_total=2,
         cost_per_attempt=10, total_cost=20)
    book(store, sid, 'loot', 7, value=3, item_name='Synthetic crafted receipt')
    before, summary = originals(store), store.get_session(sid)
    loadouts = store.list_loadouts()

    for name in ('Frame', 'Button', 'Label', 'Canvas', 'Toplevel'):
        monkeypatch.setattr(module.tk, name, BindingWidget)
    hud = BindingWidget()
    hud.ui_scale = hud._content_scale = 1
    hud.tk = SimpleNamespace(call=lambda *_: 'win32', splitlist=lambda text: text.split())
    mode = BindingValue('PED')
    hud.app = SimpleNamespace(store=store, currency_mode=mode, session_id=sid, running=True,
                              reduced_motion=BindingValue(True), _activate_saved_loadout=Mock())
    hud.vars = {key: BindingValue() for key in (
        'state', 'net_big', 'return', 'loot', 'cost', 'kills', 'input', 'damage',
        'loadout', 'durability', 'kill_trend', 'kill_trend_badge')}

    class CopyFont(Font):
        def copy(self):
            return CopyFont(self.size)

    def font(size=9, bold=False):
        hud._fonts.setdefault((size, bold), (CopyFont(size), size))
        return hud._fonts[size, bold][0]

    hud._font = font
    for name in ('refresh_setup_slots', '_fit_setup_labels', '_activate_setup_slot',
                 '_show_setup_slot', '_show_loadout', '_hide_loadout', '_show_context_details'):
        setattr(hud, name, MethodType(getattr(module.StreamerWindow, name), hud))
    for name in ('_build_resize_edges', '_start_resize', '_resize', '_stop_resize',
                 '_on_overlay_configure', '_draw_kill_trend_chart', '_draw_durability_bar',
                 '_fit_hero_captions', '_schedule_fit_to_contents'):
        setattr(hud, name, Mock())
    module.StreamerWindow._build(hud)
    hud._hunt_attribution = engine
    samples = {1: [(0.1, 0.1, .2), (0.1, 0.1, .3)]} if fans else {}
    hud._trail_recorder = SimpleNamespace(update=lambda *_a, **_kw: deepcopy(samples))
    hud.animator = SimpleNamespace(number=lambda _key, value, render, _animate: render(value),
                                   cancel=Mock())
    hud._feedback_context, hud.demo = None, False
    showinfo = Mock()
    monkeypatch.setattr(module.messagebox, 'showinfo', showinfo)

    for currency, batch_total in [('PED', '20.00 PED'), ('USD', '$2.00'), ('PED', '20.00 PED')]:
        mode.set(currency)
        module.StreamerWindow.update_from_session(hud, store.get_session(sid))
        assert not hud._trend_error and not hud._attribution_error and not hud._trail_error
        # This is the runtime label corresponding to William's '* estimated / shared'.
        note = hud.chart_note.cget('text')
        assert note == f'* Estimated/shared · {currency}' + (' · fans' if fans else '')
        assert note.split('·')[0].replace(' ', '').casefold() == '*estimated/shared'
        assert hud.manufacturing_plan.blueprint == blueprint
        assert blueprint in hud._footer_detail and batch_total in hud._footer_detail
        assert len(hud._footer_detail) < 1000  # Not the old encyclopedia moved to a dialog.

        children_before = len(hud.children)
        for widget in (hud.chart_note, hud.kill_chart):
            for event in ('<Enter>', '<FocusIn>'):
                assert not widget.bind(event)
                widget.dispatch(event)
        assert hud._loadout_tip is None and len(hud.children) == children_before
        showinfo.assert_not_called()

        # Latest requirement removes even bounded automatic footer/slot previews.
        for widget in (hud.loadout_label, *hud.setup_buttons):
            for event in ('<Enter>', '<FocusIn>'):
                widget.dispatch(event)
            assert hud._loadout_tip is None
        assert len(hud.children) == children_before
        showinfo.assert_not_called()
        # Full context appears only on deliberate activation and remains concise.
        hud.loadout_label.dispatch('<Return>')
        showinfo.assert_called_once_with('Current chart context', hud._footer_detail, parent=hud)
        showinfo.reset_mock()
        hud.setup_buttons[1].dispatch('<Return>')
        hud.app._activate_saved_loadout.assert_called_with(slot)
        assert originals(store) == before
        assert store.get_session(sid) == summary
        assert store.list_loadouts() == loadouts
        assert summary.hunting_cost == pytest.approx(20.1)


def test_content_floor_survives_its_own_height_configure_but_resets_for_width_or_warning():
    geometry = [260, 240]
    warning = ['Long retained legacy context']
    hud = SimpleNamespace(
        _fit_after_id=None, _layout_busy=False, _initial_position_pending=False,
        _layout_geometry_key=None, ui_scale=1,
        winfo_exists=lambda: True, winfo_width=lambda: geometry[0],
        winfo_height=lambda: geometry[1],
        input_slot=SimpleNamespace(winfo_manager=lambda: ''),
        vars={'kill_trend_badge': SimpleNamespace(get=lambda: warning[0])},
        minsize=Mock(), _layout_values=Mock())
    module.StreamerWindow._fit_to_contents(hud)
    hud.minsize.assert_called_once_with(260, 240)
    hud.minsize.reset_mock()
    geometry[1] = 340  # Configure generated by the content minimum itself.
    module.StreamerWindow._fit_to_contents(hud)
    hud.minsize.assert_not_called()  # Never restore the smaller OS request here.
    geometry[0] = 400
    module.StreamerWindow._fit_to_contents(hud)
    hud.minsize.assert_called_once_with(260, 240)
    hud.minsize.reset_mock()
    warning[0] = 'Resolved'
    hud._layout_geometry_key = None  # Same invalidation as the real text trace.
    module.StreamerWindow._fit_to_contents(hud)
    hud.minsize.assert_called_once_with(260, 240)
