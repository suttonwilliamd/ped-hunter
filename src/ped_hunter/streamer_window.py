"""Tk host for the shared streamer scene (all themes).

Keeps the legacy HUD's live duties: while the shown session is being tracked it
records hunt attribution decisions and chart-trail samples exactly as before.
Theme switching only redraws and saves one preference row.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import tkinter as tk

from .streamer_model import Diagnostics, Setup, ThemePreference, project
from .streamer_render import paint_canvas
from .streamer_scene import DESIGN, MAX_SIZE, MIN_SIZE, THEMES, scene

LEGACY_FOOTPRINT = 0.5  # design space is the legacy 360x330 HUD at 2x
def _estimate_logger(store):
    """Rotating JSONL next to the database (user data, outside the checkout)."""
    folder = store.db_path.parent / "logs"
    logger = logging.getLogger("ped_hunter.streamer.estimates." + hashlib.sha1(
        str(store.db_path.resolve()).encode()).hexdigest()[:12])
    if not logger.handlers:
        logger.propagate = False
        logger.setLevel(logging.INFO)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(folder / "streamer-estimates.jsonl", maxBytes=1_000_000,
                                          backupCount=3, encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


class _Shown:
    """Read-only view of one displayed HUD string (legacy ``vars[key].get()`` contract)."""

    def __init__(self, window, key):
        self._window, self._key = window, key

    def get(self):
        return self._window.displayed().get(self._key, "")


class StreamerWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        from .app import HuntAttribution, TrailRecorder, _streamer_work_area
        from .dpi import window_ui_scale
        self.ui_scale = window_ui_scale(app)
        self._feedback_context = None
        self._drag_origin = None
        self._resize_origin = None
        self._hunt_attribution = HuntAttribution(app.store)
        self._trail_recorder = TrailRecorder(app.store)
        self.kill_trail_histories = {}
        self._attribution_error = False
        self._trail_error = False
        self.demo = bool(getattr(app, "preview_mode", False))
        self.title("PED Hunter Streamer UI · Live feedback")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        width, height = self._logical(DESIGN)
        self.geometry(f"{width}x{height}")
        self.minsize(*self._logical(MIN_SIZE))
        self.maxsize(*self._logical(MAX_SIZE))
        self.canvas = tk.Canvas(self, highlightthickness=0, takefocus=True, width=width, height=height)
        self.canvas.pack(fill="both", expand=True)
        logger = _estimate_logger(app.store)
        self.diagnostics = Diagnostics(lambda record: logger.info(json.dumps(record, sort_keys=True, default=str)))
        self.preference = ThemePreference(app.store)
        self.theme = self.preference.load()
        self.focus_target = "theme"
        self.model = project(None, [], self._setups(app.store.list_loadouts()),
                             unit=app.currency_mode.get(), running=bool(app.running), demo=self.demo)
        self._context_text = ""
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Button-3>", lambda e: self._click(e, step=-1))
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Tab>", lambda _e: self._focus(1))
        self.canvas.bind("<Shift-Tab>", lambda _e: self._focus(-1))
        self.canvas.bind("<Return>", self._key_activate)
        self.canvas.bind("<space>", self._key_activate)
        self.canvas.bind("<Left>", lambda _e: self._arrow(-1))
        self.canvas.bind("<Right>", lambda _e: self._arrow(1))
        self.canvas.bind("<Up>", lambda _e: self._focus(-1))
        self.canvas.bind("<Down>", lambda _e: self._focus(1))
        for key, step in (("Left", -16), ("Right", 16), ("Up", -16), ("Down", 16)):
            self.canvas.bind(f"<Shift-{key}>", lambda _e, step=step: self._keyboard_resize(step))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        # Open at the dashboard monitor's bottom-right work-area corner.
        try:
            left, top, right, bottom = _streamer_work_area(app)
            self.geometry(f"+{max(left, right - width - 16)}+{max(top, bottom - height - 16)}")
        except (tk.TclError, ValueError):
            pass
        self.redraw()
        self.deiconify()
        self.lift()
        self.canvas.focus_set()

    @property
    def vars(self):
        return {key: _Shown(self, key) for key in self.displayed()}

    def displayed(self):
        """Every string currently on the HUD, keyed like the legacy widget HUD."""
        m = self.model
        crafting_plan = m.context.startswith("Crafting: ")
        return {
            "state": m.state, "net_big": m.get("net").displayed, "return": m.get("rate").displayed,
            "loot": m.get("loot").displayed, "cost": m.get("cost").displayed,
            "kills": m.get("groups").displayed, "healing": m.get("healing").displayed,
            "input": m.activity[1] if crafting_plan else "", "loadout": m.context,
            "durability": m.activity[2] if crafting_plan else m.activity[3],
            "kill_trend_badge": "\n".join(line for line in m.activity[:2] if line),
            "kill_trend": m.activity[0], "healing_line": m.activity[2] if not crafting_plan else m.activity[3],
            "theme": THEMES[self.theme],
            "loot_title": "Returns" if m.crafting else "Loot", "cost_title": "Cost",
            "count_title": "Groups" if m.crafting else "Kills",
        }

    # -- geometry -----------------------------------------------------------------
    def _logical(self, size):
        return tuple(round(v * LEGACY_FOOTPRINT * self.ui_scale) for v in size)

    @property
    def render_scale(self):
        width = max(self.canvas.winfo_width(), 2)
        height = max(self.canvas.winfo_height(), 2)
        if width <= 2:  # not mapped yet
            return LEGACY_FOOTPRINT * self.ui_scale
        return min(width / DESIGN[0], height / DESIGN[1])

    def redraw(self):
        view = scene(self.model, self.theme, DESIGN[0], DESIGN[1], 1, self.focus_target)
        view = replace(view, scale=self.render_scale)
        paint_canvas(self.canvas, view)
        self._scene = view

    def _resize_to(self, width):
        lo, hi = self._logical(MIN_SIZE)[0], self._logical(MAX_SIZE)[0]
        width = max(lo, min(hi, round(width)))
        self.geometry(f"{width}x{round(width * DESIGN[1] / DESIGN[0])}")

    def _keyboard_resize(self, step):
        self._resize_to(self.winfo_width() + step * self.ui_scale)
        return "break"

    # -- interaction ----------------------------------------------------------------
    def _target_at(self):
        tags = self.canvas.gettags("current")
        return next((t for t in tags if t in ("theme", "close", "context", "resize") or t.startswith("setup:")), "")

    def _hover(self, _event):
        target = self._target_at()
        cursor = ("size_nw_se" if target == "resize" else "hand2" if target else "fleur")
        if self.canvas.cget("cursor") != cursor:
            self.canvas.configure(cursor=cursor)

    def _click(self, event, step=1):
        target = self._target_at()
        self.canvas.focus_set()
        if target == "theme":
            self.focus_target = "theme"
            self.cycle_theme(step)
        elif target == "resize":
            self._resize_origin = (event.x_root, self.winfo_width())
        elif target and step == 1:
            self.focus_target = target
            self._activate(target)
        elif not target and step == 1:
            self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
        return "break"

    def _motion(self, event):
        if self._resize_origin:
            x, width = self._resize_origin
            self._resize_to(width + event.x_root - x)
        elif self._drag_origin:
            x, y = self._drag_origin
            self.geometry(f"+{event.x_root - x}+{event.y_root - y}")
        return "break"

    def _release(self, _event=None):
        self._drag_origin = self._resize_origin = None

    def _targets(self):
        return (["theme"] + [f"setup:{i}" for i, s in enumerate(self.model.setups) if s.enabled]
                + ["context", "close"])

    def _focus(self, direction):
        targets = self._targets()
        index = targets.index(self.focus_target) if self.focus_target in targets else -direction
        self.focus_target = targets[(index + direction) % len(targets)]
        self.redraw()
        return "break"

    def _arrow(self, direction):
        if self.focus_target == "theme":
            self.cycle_theme(direction)
            return "break"
        return self._focus(direction)

    def _key_activate(self, _event=None):
        if self.focus_target == "theme":
            self.cycle_theme(1)
        else:
            self._activate(self.focus_target)
        return "break"

    def _activate(self, target):
        if target.startswith("setup:"):
            index = int(target.split(":")[1])
            if 0 <= index < len(self.model.setups) and self.model.setups[index].enabled:
                setup_id = self.model.setups[index].id
                active = self.app.store.get_active_loadout()
                if active is None or active.id != setup_id:
                    self.app._activate_saved_loadout(setup_id)
        elif target == "context":
            from .streamer_context import show_context
            show_context(self, self.model.context, self.diagnostics.text or self.model.context)
        elif target == "close":
            self.destroy()

    def select_theme(self, theme):
        if theme not in THEMES:
            raise ValueError("invalid theme")
        previous = self.theme
        self.theme = theme
        try:
            self.redraw()
            if theme != previous:
                self.preference.save(theme)
        except Exception:
            self.theme = previous
            self.redraw()
            raise

    def cycle_theme(self, step=1):
        choices = list(THEMES)
        self.select_theme(choices[(choices.index(self.theme) + step) % len(choices)])

    # -- data -----------------------------------------------------------------------
    @staticmethod
    def _setups(loadouts):
        return tuple(Setup(x.id, x.name, bool(x.active)) for x in list(loadouts)[:4])

    def refresh_setup_slots(self, loadouts):
        self.model = replace(self.model, setups=self._setups(loadouts))
        if self.focus_target not in self._targets():
            self.focus_target = "theme"
        self.redraw()

    def set_restoring(self):
        self.model = replace(project(None, [], self.model.setups, unit=self.app.currency_mode.get()),
                             state="RESTORING")
        self.redraw()

    def update_from_session(self, session):
        """Same live projection duties and failure isolation as the legacy HUD."""
        from .activity_trend import ActivityTrendBuilder
        from .app import (HuntAttribution, KILL_CLUSTER_WINDOW_SECONDS, _context_details,
                          _kill_trend_points, repair_durability_status)
        app, store = self.app, self.app.store
        mode = app.currency_mode.get()
        key = (session.session_id, session.events, session.accounting_revision) if session else (None, 0, 0)
        trend_error = False
        try:
            if getattr(self, "_kill_trend_key", None) == key:
                points = self._raw_kill_trend_points
            else:
                previous = getattr(self, "_kill_trend_key", None)
                if previous is None or previous[0] != key[0] or key[1] < previous[1] or previous[2] != key[2]:
                    self._trend_builder = ActivityTrendBuilder(KILL_CLUSTER_WINDOW_SECONDS)
                    if previous is not None:
                        self._hunt_attribution = HuntAttribution(store)
                points = _kill_trend_points(store, key[0], 16, self._trend_builder)
                self._raw_kill_trend_points = points
                self._kill_trend_key = key
        except Exception:
            logging.getLogger(__name__).exception("Streamer trend refresh failed; totals still shown")
            trend_error = True
            self._kill_trend_key = None
            points = []
        live = bool(session and not session.ended_at and app.running and
                    getattr(app, "session_id", None) == session.session_id and not trend_error)
        try:
            points = self._hunt_attribution.update(key[0], points, live=live)
            self._attribution_error = False
        except Exception:
            self._attribution_error = True
        try:
            self.kill_trail_histories = self._trail_recorder.update(
                key[0], points, live and not self._attribution_error,
                revised_event_ids=getattr(points, "attribution_changed_ids", ()))
            self._trail_error = False
        except Exception:
            self.kill_trail_histories = {}
            self._trail_error = True
        durability = None
        if session:
            status = repair_durability_status(session)
            known = status["label"] != "Durability"
            text = str(status["streamer_text"])
            if not known:
                text = f"{max(0, int(session.repair_shots or 0)) + int((session.loadout_snapshot or {}).get('repair_shots', 0) or 0):,} shots tracked"
            text = text.replace("~", "")  # approximation marks stay off the HUD
            if known and status["percent"] <= 0:
                text += " · EMPTY"
            durability = (text, float(status["percent"]) if known else None)
        self.kill_trend_points = points
        model = project(session, points, self._setups(store.list_loadouts()), unit=mode,
                        running=bool(app.running), demo=self.demo, durability=durability,
                        histories=self.kill_trail_histories, trend_available=not trend_error)
        plan = getattr(points, "manufacturing", None)
        try:
            context_text = _context_details(
                model.context, points, mode, plan=plan,
                unpriced_groups=getattr(points, "unknown_craft_groups", 0),
                allocation_error=self._attribution_error, trail_error=self._trail_error)
        except Exception:
            context_text = model.context
        self.diagnostics.update(model, context_text)
        self.model = model
        if self.focus_target not in self._targets():
            self.focus_target = "theme"
        self.redraw()

    def destroy(self):
        if getattr(self.app, "streamer_window", None) is self:
            self.app.streamer_window = None
        super().destroy()
