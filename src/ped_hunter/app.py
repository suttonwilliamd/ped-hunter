"""PED Hunter's main window.

This run's readouts stay on top; run, setup and healing controls sit in a rail
(or a run bar and drawer in small windows); the hunt chart and the tabs share
the rest. The layout changes at a few size thresholds (see responsive.py).
"""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import logging
import math
import queue
from datetime import datetime
from pathlib import Path
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
import uuid

from .activity_trend import ActivityTrendBuilder
from .catalog import Catalog, WeaponRecord
from .chart_scales import plan_chart_scales
from .chart_trails import TrailRecorder
from .cockpit_chart import chart_scene, next_landmark, step_at as chart_step_at
from .hunt_trajectory import active_hours, build_trajectory, log_provenance
from .streamer_render import paint_canvas
from .currency import currency_name, format_chart_money, format_money
from .crafting_display import crafting_cost_display
from .dpi import enable_high_dpi, window_ui_scale
from .feedback import ActivityStrip, Animator, Snapshot
from .hunt_attribution import HuntAttribution
from .healing import estimate_healing_cost
from .sounds import CuePlayer
from .log_tail import LogTail
from .parser import ParsedEvent, extract_wtb_segments, is_conversion_output_item, parse_line
from .scrolling import ScrollPane
from .search_select import SearchSelect
from .table_sort import apply_sort, make_sortable
from .responsive import (
    DEFAULT_SIZE, FONTS as RESPONSIVE_FONTS, MIN_SIZE, RAIL_WIDTH, SizeClass, chart_height, classify,
)
from .resume_handoff import (
    claim_resume_handoff, complete_resume_handoff, has_resume_handoff, read_resume_handoff,
)
from .storage import LifetimeTotals, LoadoutRecord, SessionResumeState, SessionSummary, SkillGainSummary, Store


POLL_MS = 250
LOG_IDLE_WARNING_SECONDS = 30.0
AMP_CATEGORIES = {"BLP Amp", "Energy Amp", "Melee Amp", "MF Amp"}
SCOPE_CATEGORIES = {"Scope"}
SIGHT_CATEGORIES = {"Sight"}
STREAMER_DEFAULT_WIDTH = 360
STREAMER_DEFAULT_HEIGHT = 330
STREAMER_MIN_WIDTH = 260
STREAMER_MIN_HEIGHT = 240
STREAMER_EDGE_MARGIN = 16
KILL_CLUSTER_WINDOW_SECONDS = 0.75
HUD_SETUP_COLORS = ("#5EEAD4", "#93C5FD", "#FCD34D", "#D8B4FE")
# Events tab filter -> stored event kinds (None: everything except chat).
EVENT_FILTERS = {
    "Loot": ("loot",),
    "Loot and skills": ("loot", "skill"),
    "Combat": ("combat",),
    "Globals": ("global",),
    "Everything": None,
}
_EVENT_TYPES = {"manufacturing_offset": "Crafting", "craft": "Crafting"}
HELP_SECTIONS = (
    ("Start a hunt",
     "1. In Entropia Universe, turn on chat logging to a file.\n"
     "2. Check the chat log path under Settings above.\n"
     "3. In Setups, build the weapon you are using, then Save and activate.\n"
     "4. Press New run and hunt. Press End run when you are done."),
    ("Continue a run later", "Open Runs, select the run, then Resume this run."),
    ("Switch weapon mid-hunt", "Press Alt+1 to Alt+4, or use the setup buttons. Only later shots use the new setup."),
    ("Read the chart",
     "Each bar is one or more encounters; the line ends at this run's net. Hover a bar, or click the chart "
     "and use the left and right arrow keys. [ and ] jump between the standout wins."),
    ("Nothing is updating",
     "Check the chat log path, make sure logging is turned on in the game, then press New run or Resume. "
     "The status line at the bottom says what PED Hunter is doing."),
    ("A cost looks wrong",
     "Check the active setup's weapon, amplifier and enhancers. Setups > Weapon data shows the figures used. "
     "Healing cost comes from the Heal and HoT tools chosen under Healing."),
    ("A loot item should not count",
     "Open Events, select the item, then Don't count this loot. Nothing is deleted; "
     "Loot corrections lets you count it again."),
    ("Crafting", "Use the Crafting tab to add the material cost of your attempts to this run."),
    ("Your data", "Runs, setups and settings are saved on this PC only."),
)


def _held(store):
    """One database connection for a whole refresh or batch (see ``Store.hold``)."""
    return store.hold() if getattr(type(store), "hold", None) is not None else nullcontext()


@dataclass(slots=True)
class MetricCard:
    frame: ttk.Frame
    value: tk.StringVar
    label: tk.StringVar


@dataclass(slots=True)
class _ResumeView:
    """Read-only work prepared off Tk; ownership transfers through one queue."""
    revision: tuple
    session: SessionSummary
    sessions: list[SessionSummary]
    totals: LifetimeTotals
    skills: list[SkillGainSummary]
    builder: ActivityTrendBuilder | None
    points: list[dict[str, object]]


class PedHunterApp(tk.Tk):
    """Main PED Hunter desktop UI."""

    def __init__(self, *, store: Store | None = None) -> None:
        enable_high_dpi()  # Must precede Tk's first native window.
        super().__init__()
        self.title("PED Hunter")
        self.display_scale = window_ui_scale(self)
        # A compact default that leaves the desktop free; never larger than the screen.
        width = min(DEFAULT_SIZE[0] * self.display_scale, self.winfo_screenwidth() * 0.92)
        height = min(DEFAULT_SIZE[1] * self.display_scale, self.winfo_screenheight() * 0.88)
        self.geometry(f"{round(width)}x{round(height)}")
        self.minsize(round(MIN_SIZE[0] * self.display_scale), round(MIN_SIZE[1] * self.display_scale))
        self._initial_size = (width, height)
        self.size_class = SizeClass()
        self._ui_fonts: dict[tuple[str, int, str], tkfont.Font] = {}

        self.store = store if store is not None else Store()
        self.catalog = Catalog.load()
        self.session_id: str | None = None
        self.last_size = 0
        self._last_ingested_log_line: str | None = None
        self.running = False
        self.current_log_path: Path | None = None

        self.streamer_window: StreamerWindow | None = None
        self._full_refresh_pending = False
        self._resume_pending: queue.Queue | None = None
        self._prepared_resume: _ResumeView | None = None
        self._session_summaries: dict[str, SessionSummary] = {}
        self._log_drain_pending = False
        self._log_worker: threading.Thread | None = None
        self._log_worker_stop = threading.Event()
        self._log_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.chat_path = tk.StringVar(value=str(_default_chat_log_path()))
        self.status_text = tk.StringVar(value="Ready — press New run, or pick a run under Runs and Resume")
        self.session_text = tk.StringVar(value="No active session")
        self.catalog_query = tk.StringVar(value="")
        blueprint_names = sorted(self.catalog.blueprints)
        self.crafting_blueprint = tk.StringVar(value="")
        self.crafting_attempts = tk.StringVar(value="1")
        self.crafting_preview = tk.StringVar(value="Pick a blueprint to see its material cost.")
        self.active_loadout_title = tk.StringVar(value="No active setup")
        self.active_loadout_details = tk.StringVar(value="Configure and activate a hunting setup before starting for accurate costs.")

        self.active_loadout_text = tk.StringVar(value="No active setup — configure one before tracking")
        self.repair_radar_text = tk.StringVar(value="Durability tracking starts with a fresh run at full gun and amp TT")
        self.selected_session_text = tk.StringVar(value="Select a recent session.")
        self.hero_net = tk.StringVar(value="—")
        self.hero_return = tk.StringVar(value="—")
        self.hero_burn = tk.StringVar(value="—")
        self.hero_loot = tk.StringVar(value="Loot $0.00")
        self.hero_cost = tk.StringVar(value="Cost $0.00")
        self.hero_events = tk.StringVar(value="Events 0")
        self.hero_state = tk.StringVar(value="Not tracking")
        self.hero_session = tk.StringVar(value="No active session — press New run, or resume one from Runs")
        self.healing_tool = tk.StringVar(value=self.store.get_selected_healing_tool() or "None")
        self.hot_tool = tk.StringVar(value=self.store.get_selected_hot_tool() or "None")
        self.healing_detail = tk.StringVar()
        self.healing_totals = tk.StringVar()
        self.skill_total_text = tk.StringVar(value="0.0000 XP")
        self.skill_proc_text = tk.StringVar(value="0 skill gains")
        self.skill_summary_text = tk.StringVar(value="No skill gains recorded for this session yet.")
        self.wtb_summary_text = tk.StringVar(value="No buy requests seen yet.")
        self.lifetime_summary_text = tk.StringVar(value="No saved runs yet — your lifetime totals build up as you hunt.")
        self.lifetime_vars = {
            "total_cost": tk.StringVar(value="$0.00"),
            "total_loot": tk.StringVar(value="$0.00"),
            "total_net": tk.StringVar(value="+$0.00"),
            "overall_return": tk.StringVar(value="0.00%"),
            "total_events": tk.StringVar(value="0"),
            "avg_return": tk.StringVar(value="0.00%"),
            "best_run": tk.StringVar(value="—"),
            "worst_run": tk.StringVar(value="—"),
            "avg_profit": tk.StringVar(value="+$0.00"),
        }

        self.loadout_name = tk.StringVar(value="")
        self.loadout_weapon = tk.StringVar(value="")
        self.loadout_amp = tk.StringVar(value="None")
        self.loadout_scope = tk.StringVar(value="None")
        self.loadout_sight_1 = tk.StringVar(value="None")
        self.loadout_sight_2 = tk.StringVar(value="None")
        self.damage_enhancers = tk.StringVar(value="0")
        self.accuracy_enhancers = tk.StringVar(value="0")
        self.economy_enhancers = tk.StringVar(value="0")
        self.loadout_preview = tk.StringVar(value="Cost/shot: —")
        self.selected_loadout_id: int | None = None
        self.loadout_draft_text = tk.StringVar(value="New setup · not saved")
        self._loadout_clean_state: tuple[str, ...] = ()
        self.top_area_expanded = tk.BooleanVar(value=True)
        self.top_toggle_text = tk.StringVar(value="Hide totals")
        self.currency_mode = tk.StringVar(value="USD")
        self.reduced_motion = tk.BooleanVar(value=False)
        self.feedback_sounds = tk.BooleanVar(value=False)
        self.cue_player = CuePlayer()
        self.animator = Animator(self)
        self._feedback_context = None
        self._activity_cache: Snapshot | None = None
        self._chart_context = None
        self._skill_context = None
        self._skill_counts: dict[str, int] = {}
        self._event_context = None
        self._event_ids: set[str] = set()

        self.metric_cards: dict[str, MetricCard] = {}
        self._configure_theme()
        self._build_layout()
        with _held(self.store):
            self._restore_default_context()
            # Widgets and restored context must exist before a currency refresh.
            self.currency_mode.trace_add("write", self._on_currency_changed)
            self._refresh_all()

    def _ui_font(self, points: int = 10, *, family: str = "Segoe UI", weight: str = "normal") -> tkfont.Font:
        """Readable point-equivalent fonts, sized once at this window's DPI."""
        key = family, points, weight
        pixels = -max(1, round(points * 96 / 72 * self.display_scale))
        font = self._ui_fonts.get(key)
        if font is None:
            font = self._ui_fonts[key] = tkfont.Font(self, family=family, size=pixels, weight=weight)
        elif int(font.cget("size")) != pixels:
            font.configure(size=pixels)
        return font

    def _configure_theme(self) -> None:
        bg = "#101419"
        panel = "#181e25"
        panel_2 = "#202831"
        panel_3 = "#2b3642"
        border = "#394654"
        text = "#edf1f5"
        muted = "#a5b0bc"
        accent = "#78c9bb"
        accent_soft = "#0f3438"
        good = "#22c55e"

        self.configure(bg=bg)
        p = self._px
        style = ttk.Style(self)
        try:
            if style.theme_use() != "clam":
                style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Root.TFrame", background=bg)
        style.configure("Header.TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel, borderwidth=0, relief="flat")
        style.configure("Soft.TFrame", background=panel_2, borderwidth=0, relief="flat")
        style.configure("Card.TFrame", background=panel_2, borderwidth=0, relief="flat")
        style.configure("Hero.TFrame", background=panel_2, borderwidth=0, relief="flat")
        style.configure("HeroAccent.TFrame", background=accent, borderwidth=0, relief="flat")
        style.configure("HeroInset.TFrame", background="#0f1726", borderwidth=0, relief="flat")
        style.configure("AccentLine.TFrame", background=accent)
        style.configure(".", font=self._ui_font())
        # Entry fonts are widget options, not only ttk style settings. Named
        # fonts also keep editable fields and combobox popdowns in sync on DPI changes.
        for pattern in ("*TEntry.font", "*TCombobox.font", "*TSpinbox.font", "*TCombobox*Listbox.font"):
            self.option_add(pattern, self._ui_font())
        style.configure("TLabel", background=bg, foreground=text, font=self._ui_font())
        style.configure("Muted.TLabel", background=bg, foreground=muted, font=self._ui_font(9))
        style.configure("Kicker.TLabel", background=bg, foreground=accent, font=self._ui_font(9, family="Segoe UI Semibold"))
        style.configure("Panel.TLabel", background=panel, foreground=text, font=self._ui_font())
        style.configure("PanelTitle.TLabel", background=panel, foreground="#d8fffb", font=self._ui_font(10, family="Segoe UI Semibold"))
        style.configure("PanelMuted.TLabel", background=panel, foreground=muted, font=self._ui_font(9))
        style.configure("CardValue.TLabel", background=panel_2, foreground=text, font=self._role_font("card"))
        style.configure("CardTitle.TLabel", background=panel_2, foreground=text, font=self._ui_font(11, family="Segoe UI Semibold"))
        style.configure("CardLabel.TLabel", background=panel_2, foreground=muted, font=self._ui_font(9))
        style.configure("HeroKicker.TLabel", background=panel_2, foreground=accent, font=self._ui_font(8, family="Segoe UI Semibold"))
        style.configure("HeroGood.TLabel", background=panel_2, foreground=accent, font=self._role_font("net"))
        style.configure("HeroBad.TLabel", background=panel_2, foreground="#fb923c", font=self._role_font("net"))
        style.configure("HeroNeutral.TLabel", background=panel_2, foreground=text, font=self._role_font("net"))
        style.configure("HeroReturnGood.TLabel", background=panel_2, foreground="#99f6e4", font=self._ui_font(14, family="Segoe UI Semibold"))
        style.configure("HeroReturnBad.TLabel", background=panel_2, foreground="#fed7aa", font=self._ui_font(14, family="Segoe UI Semibold"))
        style.configure("HeroReturnNeutral.TLabel", background=panel_2, foreground=muted, font=self._ui_font(14, family="Segoe UI Semibold"))
        style.configure("HeroSmallValue.TLabel", background="#0f1726", foreground=text, font=self._ui_font(13, family="Segoe UI Semibold"))
        style.configure("HeroSmallLabel.TLabel", background="#0f1726", foreground=muted, font=self._role_font("label"))
        style.configure("HeroSetup.TLabel", background=panel_2, foreground=muted, font=self._ui_font(9))
        style.configure("ReadoutValue.TLabel", background="#0f1726", foreground=text, font=self._role_font("value"))
        for suffix, colour in (("Good", "#99f6e4"), ("Bad", "#fed7aa"), ("Neutral", text)):
            style.configure(f"Readout{suffix}.TLabel", background="#0f1726", foreground=colour,
                            font=self._role_font("value"))
        style.configure("RailKicker.TLabel", background=panel, foreground=accent, font=self._ui_font(9, family="Segoe UI Semibold"))
        style.configure("RailValue.TLabel", background=panel, foreground=text, font=self._ui_font(9))
        style.configure("RailLive.TLabel", background=panel, foreground=accent, font=self._ui_font(9, family="Segoe UI Semibold"))
        style.configure("TrendTitle.TLabel", background="#020617", foreground="#d8fffb", font=self._ui_font(9, family="Segoe UI Semibold"))
        style.configure("TrendBody.TLabel", background="#020617", foreground=muted, font=self._ui_font(8))
        style.configure("Pill.TLabel", background=accent_soft, foreground=accent, font=self._ui_font(9, family="Segoe UI Semibold"), padding=(12, 5))
        style.configure("Title.TLabel", background=bg, foreground=accent, font=self._ui_font(16, family="Segoe UI Semibold"))
        style.configure("Subtitle.TLabel", background=bg, foreground=muted, font=self._ui_font())
        style.configure("Status.TLabel", background=panel, foreground=muted, font=self._ui_font(9))
        style.configure("Accent.TButton", background=accent, foreground="#031412", font=self._ui_font(9, family="Segoe UI Semibold"), padding=(p(10), p(3)), borderwidth=0, focusthickness=0)
        style.configure("Ghost.TButton", background=panel_3, foreground=text, font=self._ui_font(9), padding=(p(8), p(3)), borderwidth=0, focusthickness=0)
        style.configure("TButton", background=panel_3, foreground=text, font=self._ui_font(9), padding=(p(8), p(3)), borderwidth=0, focusthickness=0)
        style.configure("Footer.TButton", background=panel_2, foreground=muted, font=self._ui_font(8), padding=(p(6), p(1)), borderwidth=0, focusthickness=0)
        style.map("Footer.TButton", background=[("pressed", "#1f293d"), ("active", "#223049")], foreground=[("active", text)])
        style.map(
            "Accent.TButton",
            background=[("disabled", "#2a3444"), ("pressed", "#14b8a6"), ("active", "#5eead4")],
            foreground=[("disabled", "#748094"), ("active", "#031412")],
        )
        style.map(
            "Ghost.TButton",
            background=[("disabled", "#151c2a"), ("pressed", "#1f293d"), ("active", "#223049")],
            foreground=[("disabled", "#697586"), ("active", text)],
        )
        style.map("TButton", background=[("disabled", "#1c232c"), ("pressed", "#1f293d"), ("active", "#223049")],
                  foreground=[("disabled", "#697586"), ("active", text)])
        style.configure("TCheckbutton", background=panel, foreground=muted, font=self._ui_font(9), indicatorbackground=panel_2,
                        indicatorsize=p(12), padding=(p(2), p(2)))
        style.map("TCheckbutton", background=[("active", panel)], foreground=[("selected", text), ("active", text)], indicatorbackground=[("selected", accent), ("active", panel_3)])
        style.map("TCombobox", fieldbackground=[("readonly", "#0a0f1a")], selectbackground=[("readonly", panel_3)], selectforeground=[("readonly", text)])
        style.configure("TEntry", font=self._ui_font(), fieldbackground="#0a0f1a", foreground=text, insertcolor=text, bordercolor=border, lightcolor=border, darkcolor=border, padding=(p(6), p(3)), borderwidth=1)
        # A wide arrow button: the dropdown target must be easy to hit at any display scale.
        style.configure("TCombobox", font=self._ui_font(), fieldbackground="#0a0f1a", foreground=text, arrowcolor=text, arrowsize=p(18), background=panel_3, bordercolor=border, lightcolor=border, darkcolor=border, padding=(p(6), p(3)), borderwidth=1)
        style.configure("TSpinbox", font=self._ui_font(), fieldbackground="#0a0f1a", foreground=text, arrowcolor=text, arrowsize=p(12), background=panel_3, bordercolor=border, lightcolor=border, darkcolor=border, padding=(p(6), p(3)), borderwidth=1)
        for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(bar, background=panel_3, troughcolor=panel, borderwidth=0, arrowcolor=muted,
                            arrowsize=p(11), bordercolor=panel, lightcolor=panel_3, darkcolor=panel_3)
        # The tab strip is a bar of its own: it switches the tables below, not the run above.
        style.configure("TNotebook", background=panel, borderwidth=0, tabmargins=(0, 0, 0, 0), lightcolor=panel, darkcolor=panel, bordercolor=panel)
        style.configure("TNotebook.Tab", background=panel, foreground=muted, padding=(p(10), p(4)), font=self._ui_font(9, family="Segoe UI Semibold"), borderwidth=0, relief="flat", lightcolor=panel, darkcolor=panel, bordercolor=panel)
        style.layout("TNotebook.Tab", [("Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [("Notebook.label", {"side": "top", "sticky": ""})]})]})])
        style.map("TNotebook.Tab", expand=[("selected", (0, 0, 0, 0))],
                  lightcolor=[("selected", accent_soft)], darkcolor=[("selected", accent_soft)],
                  bordercolor=[("selected", accent_soft)])
        style.map("TNotebook.Tab", background=[("selected", accent_soft), ("active", panel_3)], foreground=[("selected", accent), ("active", text)])
        tree_font = self._ui_font(9)
        # Rows and glyphs use the same monitor scale, independently of Tk's
        # process-wide point scaling; never clip the rendered line.
        tree_line_height = tree_font.metrics("linespace")
        tree_row_height = max(round(23 * self.display_scale),
                              tree_line_height + round(8 * self.display_scale))
        style.configure(
            "Treeview",
            background="#0a0f1a",
            fieldbackground="#0a0f1a",
            foreground=text,
            bordercolor=panel,
            rowheight=tree_row_height,
            font=tree_font,
            borderwidth=0,
        )
        style.configure("Treeview.Heading", background=panel_3, foreground="#c7fffa", font=self._ui_font(9, family="Segoe UI Semibold"), borderwidth=0, padding=(p(6), p(3)))
        style.map("Treeview", background=[("selected", "#155e59")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("pressed", "#1f293d"), ("active", "#3a4757")],
                  foreground=[("active", "#ffffff")])

        self.colors = {
            "bg": bg,
            "panel": panel,
            "panel_2": panel_2,
            "panel_3": panel_3,
            "border": border,
            "text": text,
            "muted": muted,
            "accent": accent,
            "accent_soft": accent_soft,
            "good": good,
            "warn": "#f59e0b",
            "bad": "#ef4444",
            "orange": "#fb923c",
            "cyan": "#38bdf8",
        }

    def _px(self, logical: float) -> int:
        """Logical pixels at this window's monitor scale (spacing uses 4 / 8 / 12)."""
        return round(logical * self.display_scale)

    def _role_font(self, role: str) -> tkfont.Font:
        """Readout fonts that change with the window's size class, never per pixel."""
        index = ("net", "value", "label", "card").index(role)
        points = RESPONSIVE_FONTS[self.size_class.name][index]
        pixels = -max(1, round(points * 96 / 72 * self.display_scale))
        fonts = self.__dict__.setdefault("_role_fonts", {})
        font = fonts.get(role)
        if font is None:
            family = "Segoe UI" if role == "label" else "Segoe UI Semibold"
            font = fonts[role] = tkfont.Font(self, family=family, size=pixels)
        elif int(font.cget("size")) != pixels:
            font.configure(size=pixels)
        return font

    def _build_layout(self) -> None:
        p = self._px
        self.root_frame = ttk.Frame(self, style="Root.TFrame", padding=(p(8), p(6)))
        root = self.root_frame
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # This run's readouts stay on top; the run bar (small windows) or the
        # rail (larger ones) holds the controls; chart and tabs share the body.
        self._build_session_hero(root, row=0)
        self._build_run_bar(root, row=1)
        self.body = body = ttk.Frame(root, style="Root.TFrame")
        body.grid(row=2, column=0, sticky="nsew", pady=(p(8), 0))
        body.columnconfigure(1, weight=1)
        self._body_height = 0
        body.bind("<Configure>", self._on_body_resize)
        self._build_command_rail(body)
        self._build_hunt_chart(body)

        notebook = ttk.Notebook(body)
        notebook.grid(row=1, column=1, sticky="nsew")
        self.notebook = notebook
        pad = (0, p(8), 0, 0)
        self.dashboard_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.statistics_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.events_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.skills_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.loadouts_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.manufacturing_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.wtb_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        self.setup_tab = ttk.Frame(notebook, style="Root.TFrame", padding=pad)
        for tab, title in ((self.dashboard_tab, "Runs"), (self.statistics_tab, "Lifetime"),
                           (self.events_tab, "Events"), (self.skills_tab, "Skills"), (self.loadouts_tab, "Setups"),
                           (self.manufacturing_tab, "Crafting"), (self.wtb_tab, "WTB"),
                           (self.setup_tab, "Help & settings")):
            notebook.add(tab, text=title)

        self._build_footer(root, row=3)
        self._build_dashboard_tab()
        self._build_events_tab()
        self._build_skills_tab()
        self._build_loadouts_tab()
        self._build_manufacturing_tab()
        self._build_wtb_tab()
        self._build_setup_tab()
        notebook.bind("<<NotebookTabChanged>>", lambda _event: self._refresh_visible_tab())
        self._apply_size_class(*self._initial_size, force=True)
        self.bind("<Configure>", self._on_resize)
        self.bind("<Map>", self._on_restore, add="+")
        self.bind("<Escape>", lambda _event: self._toggle_drawer(False), add="+")
        for slot in range(4):
            self.bind(f"<Alt-Key-{slot + 1}>", lambda _event, index=slot: self._switch_setup_slot(index))

    # ----- size classes --------------------------------------------------
    def _apply_size_class(self, width: float, height: float, *, force: bool = False) -> None:
        """Re-lay the window only when a size threshold is crossed (see responsive.py)."""
        scale = max(0.5, self.display_scale)
        size = classify(width / scale, height / scale, None if force else self.size_class)
        if size == self.size_class and not force:
            return
        self.size_class = size
        for role in ("net", "value", "label", "card"):
            self._role_font(role)
        self._layout_hero()
        self._layout_body()
        self._layout_tabs()

    def _layout_hero(self) -> None:
        size, hero, left, p = self.size_class, self.hero_panel, self.hero_left, self._px
        compact = not size.docked
        cells = self._hero_cells
        for widget in (left, self.hero_kicker, self.hero_net_label, self.hero_session_label,
                       *(cell[0] for cell in cells.values())):
            widget.grid_forget()
        for column in range(len(cells) + 2):
            hero.columnconfigure(column, weight=0, uniform="")
        shown = [key for key in cells if not (compact and key == "events") and not (size.tight and key == "burn")]
        if compact:
            self.hero_net_label.grid(row=0, column=0, sticky="w")
            left.grid(row=0, column=0, sticky="w", padx=(0, p(8)))
            hero.columnconfigure(len(shown) + 1, weight=1)
        else:
            self.hero_kicker.grid(row=0, column=0, sticky="w")
            self.hero_net_label.grid(row=1, column=0, sticky="w")
            self.hero_session_label.grid(row=2, column=0, sticky="ew")
            left.grid(row=0, column=0, sticky="nsew", padx=(0, p(12)))
            hero.columnconfigure(0, weight=3)
        for column, key in enumerate(shown, start=1):
            frame, label, value, long_text, short_text = cells[key]
            label.grid_forget()
            value.grid_forget()
            frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 1 else p(4), 0))
            if compact:
                frame.configure(padding=(p(6), p(2)))
                label.configure(text=short_text)
                label.grid(row=0, column=0, sticky="w", padx=(0, p(4)))
                value.grid(row=0, column=1, sticky="w")
            else:
                frame.configure(padding=(p(8), p(4)))
                label.configure(text=long_text)
                label.grid(row=0, column=0, sticky="w")
                value.grid(row=1, column=0, sticky="w")
                hero.columnconfigure(column, weight=1, uniform="readouts")

    def _layout_body(self) -> None:
        size, p, rail, body = self.size_class, self._px, self.command_rail, self.body
        rail.configure(width=p(RAIL_WIDTH[size.name]))
        body.columnconfigure(0, minsize=p(RAIL_WIDTH[size.name]) if size.docked else 0)
        for widget in self._rail_bar_twins:
            widget.grid() if size.docked else widget.grid_remove()
        if size.docked:
            self._drawer_open = False
            rail.place_forget()
            rail.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, p(8)))
            self.run_bar.grid_remove()
        else:
            rail.grid_forget()
            self.run_bar.grid()
            if self._drawer_open:
                rail.place(in_=body, x=0, y=0, relheight=1.0, width=p(RAIL_WIDTH["compact"]))
                rail.lift()
            else:
                rail.place_forget()
        self.drawer_text.set("Close ◂" if self._drawer_open else "Healing & gear ▸")
        idle = self.__dict__.get("_shown_session") is None  # nothing to chart yet
        chart_only = size.split and self.chart_first.get() and not idle
        if idle or (size.split and not chart_only):
            self.chart_panel.grid_remove()
        else:
            self.chart_panel.grid()
        if chart_only:
            self.notebook.grid_remove()
        else:
            self.notebook.grid()
        body.rowconfigure(0, weight=1 if chart_only else 0)
        body.rowconfigure(1, weight=0 if chart_only else 1)
        if size.split and not idle:
            self.bar_chart_button.grid()
        else:
            self.bar_chart_button.grid_remove()
        if size.tight:
            self.bar_setup_label.grid_remove()
        else:
            self.bar_setup_label.grid()
        self.chart_toggle_text.set("Show tables" if chart_only else "Show chart")
        self._size_chart_row()
        self._sync_run_bar()

    def _size_chart_row(self) -> None:
        scale = max(0.5, self.display_scale)
        height = chart_height(self._body_height / scale, self.size_class)
        if self.__dict__.get("_shown_session") is None:
            height = 0
        self.body.rowconfigure(0, minsize=round(height * scale))

    def _on_body_resize(self, event: tk.Event) -> None:
        if event.widget is self.body and event.height != self._body_height:
            self._body_height = event.height
            self._size_chart_row()

    def _layout_tabs(self) -> None:
        size, p = self.size_class, self._px
        compact = not size.docked
        ttk.Style(self).configure("TNotebook.Tab", padding=(p(6), p(3)) if compact else (p(10), p(4)))
        self.sessions_tree.configure(displaycolumns=(
            ("started", "setup", "return", "net") if size.tight
            else ("started", "setup", "return", "loot", "cost", "net") if compact else "#all"))
        self.loadouts_tree.configure(displaycolumns=("active", "name", "cost") if compact else "#all")
        self.loadout_list_panel.configure(width=p(190 if compact else 300 if size.name == "wide" else 250))
        for widget in self._setup_extras:
            widget.grid_remove() if compact else widget.grid()
        for tree in (self.sessions_tree, self.loadouts_tree):
            self._fit_tree(tree)
        if size.name == "wide":
            self.loot_chart_canvas.grid()
        else:
            self.loot_chart_canvas.grid_remove()  # the table needs the room
        columns = 3 if compact else 5
        for column in range(5):
            self.lifetime_grid.columnconfigure(column, weight=1 if column < columns else 0,
                                               uniform="lifetime" if column < columns else "")
        for index, card in enumerate(self.lifetime_cards):
            row, column = divmod(index, columns)
            card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else p(8), 0),
                      pady=(0 if row == 0 else p(8), 0))
        for note in self.lifetime_notes:
            note.pack_forget() if compact else note.pack(anchor="w")

    def _toggle_drawer(self, open_: bool | None = None) -> None:
        """Small windows: the rail (setups, healing, equipment) slides over the body."""
        if self.size_class.docked:
            return
        self._drawer_open = (not self._drawer_open) if open_ is None else open_
        self._layout_body()

    def _toggle_chart_first(self) -> None:
        self.chart_first.set(not self.chart_first.get())
        self._layout_body()

    def _sync_run_bar(self) -> None:
        """The run bar mirrors the rail's buttons and shows only what applies now."""
        if "resume_run_button" not in self.__dict__:
            return
        for button, source in ((self.bar_start, self.start_button), (self.bar_resume, self.resume_button),
                               (self.bar_stop, self.stop_button)):
            button.configure(state=str(source.cget("state")))
        tracking = str(self.stop_button.cget("state")) != "disabled"
        for widget in (self.bar_state, self.bar_stop, self.rail_state, self.stop_button):
            widget.grid() if tracking else widget.grid_remove()
        for widget in (self.bar_start, self.bar_resume, self.start_button, self.resume_button):
            widget.grid_remove() if tracking else widget.grid()
        self.resume_run_button.configure(state="disabled" if tracking else "normal")

    def _build_session_hero(self, parent: ttk.Frame, *, row: int) -> None:
        """This run's readout strip: hunt net first, then return, burn, loot, cost, events."""
        p = self._px
        self.hero_panel = ttk.Frame(parent, style="Hero.TFrame", padding=(p(8), p(4)))
        hero = self.hero_panel
        hero.grid(row=row, column=0, sticky="ew")
        self.hero_left = ttk.Frame(hero, style="Hero.TFrame")
        left = self.hero_left
        left.columnconfigure(0, weight=1)
        self.hero_kicker = ttk.Label(left, text="THIS RUN · HUNT NET", style="HeroKicker.TLabel")
        self.hero_net_label = ttk.Label(left, textvariable=self.hero_net, style="HeroNeutral.TLabel")
        self.hero_session_label = ttk.Label(left, textvariable=self.hero_session, style="HeroSetup.TLabel", wraplength=p(300))
        self.hero_right = hero  # readouts sit in the strip itself
        self._hero_cells: dict[str, tuple] = {}
        self.hero_return_label = self._add_hero_stat(hero, "return", self.hero_return, "RETURN", "Return")
        self._add_hero_stat(hero, "burn", self.hero_burn, "COST / HOUR", "Per hour")
        self._add_hero_stat(hero, "loot", self.hero_loot, "LOOT", "Loot")
        self._add_hero_stat(hero, "cost", self.hero_cost, "COST", "Cost")
        self._add_hero_stat(hero, "events", self.hero_events, "EVENTS", "Events")
        left.bind("<Configure>", self._on_hero_resize)

    def _build_run_bar(self, parent: ttk.Frame, *, row: int) -> None:
        """Small windows: run controls and the active setup in one row; the rest is a drawer."""
        p = self._px
        self.run_bar = ttk.Frame(parent, style="Panel.TFrame", padding=(p(8), p(4)))
        bar = self.run_bar
        bar.grid(row=row, column=0, sticky="ew", pady=(p(4), 0))
        self._drawer_open = False
        self.drawer_text = tk.StringVar(value="Healing & gear ▸")
        self.chart_first = tk.BooleanVar(value=False)
        self.chart_toggle_text = tk.StringVar(value="Show chart")
        self.bar_start = ttk.Button(bar, text="New run", command=self.start, style="Accent.TButton")
        self.bar_resume = ttk.Button(bar, text="Resume", command=self.resume_selected_session, style="Ghost.TButton")
        self.bar_state = ttk.Label(bar, text="● Tracking", style="RailLive.TLabel")
        self.bar_stop = ttk.Button(bar, text="End run", command=self.stop)
        self.bar_start.grid(row=0, column=0, padx=(0, p(4)))
        self.bar_resume.grid(row=0, column=1, padx=(0, p(8)))
        self.bar_state.grid(row=0, column=2, padx=(0, p(8)))
        self.bar_stop.grid(row=0, column=3, padx=(0, p(8)))
        self.bar_setup_label = ttk.Label(bar, text="Setup", style="PanelMuted.TLabel")
        self.bar_setup_label.grid(row=0, column=4, padx=(0, p(4)))
        self.bar_setup = ttk.Combobox(bar, state="readonly", width=8)
        self.bar_setup.grid(row=0, column=5, sticky="ew", padx=(0, p(8)))
        self.bar_setup.bind("<<ComboboxSelected>>", lambda _event: self._bar_setup_chosen())
        bar.columnconfigure(5, weight=1)
        self.bar_chart_button = ttk.Button(bar, textvariable=self.chart_toggle_text, command=self._toggle_chart_first,
                                           style="Ghost.TButton")
        self.bar_chart_button.grid(row=0, column=6, padx=(0, p(4)))
        ttk.Button(bar, textvariable=self.drawer_text, command=self._toggle_drawer,
                   style="Ghost.TButton").grid(row=0, column=7)

    def _bar_setup_chosen(self) -> None:
        index = self.bar_setup.current()
        loadouts = list(self.store.list_loadouts())
        self.bar_setup.selection_clear()
        if 0 <= index < len(loadouts):
            if not loadouts[index].active:
                self._activate_saved_loadout(loadouts[index].id)
        else:
            self.notebook.select(self.loadouts_tab)
        self._refresh_setup_rail(self.store.list_loadouts())

    def _build_footer(self, parent: ttk.Frame, *, row: int) -> None:
        """One status line; the chat-log path itself lives under Help & settings."""
        p = self._px
        self.controls_panel = ttk.Frame(parent, style="Panel.TFrame", padding=(p(8), p(2)))
        footer = self.controls_panel
        footer.grid(row=row, column=0, sticky="ew", pady=(p(4), 0))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status_text, style="Status.TLabel", width=1, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, p(8)))
        self.log_button = ttk.Button(footer, style="Footer.TButton", command=self._open_settings)
        self.log_button.grid(row=0, column=1, padx=(0, p(4)))
        ttk.Button(footer, text="Streamer UI", command=self._open_streamer_window,
                   style="Footer.TButton").grid(row=0, column=2, padx=(0, p(4)))
        ttk.Button(footer, textvariable=self.top_toggle_text, command=self._toggle_top_area,
                   style="Footer.TButton").grid(row=0, column=3)
        self.chat_path.trace_add("write", lambda *_args: self._refresh_log_button())
        self._refresh_log_button()

    def _refresh_log_button(self) -> None:
        text = self.chat_path.get().strip().strip('"')
        try:
            found = bool(text) and Path(text).is_file()
        except OSError:
            found = False
        if found:
            self.log_button.configure(text=f"Log: {Path(text).name}", style="Footer.TButton")
        else:
            self.log_button.configure(text="Choose chat log…", style="Accent.TButton")

    def _open_settings(self) -> None:
        self.notebook.select(self.setup_tab)
        if self.size_class.split and self.chart_first.get():
            self._toggle_chart_first()
        self.help_scroll.canvas.yview_moveto(0)
        self.chat_log_entry.focus_set()

    def _build_command_rail(self, body: ttk.Frame) -> None:
        """Run, setup, healing and equipment controls: docked beside the chart, or a drawer."""
        p = self._px
        self.command_rail = ttk.Frame(body, style="Panel.TFrame")
        self.command_rail.grid_propagate(False)
        self.command_rail.columnconfigure(0, weight=1)
        self.command_rail.rowconfigure(0, weight=1)
        self.rail_scroll = ScrollPane(self.command_rail, background=self.colors["panel"], autohide=True)
        self.rail_scroll.grid(row=0, column=0, sticky="nsew")
        rail = ttk.Frame(self.rail_scroll.content, style="Panel.TFrame", padding=(p(8), p(8)))
        rail.pack(fill="both", expand=True)
        rail.columnconfigure(0, weight=1)
        rail.bind("<Configure>", self._on_rail_resize)

        run_title = ttk.Label(rail, text="RUN", style="RailKicker.TLabel")
        run_title.grid(row=0, column=0, sticky="w", pady=(0, p(4)))
        actions = ttk.Frame(rail, style="Panel.TFrame")
        actions.grid(row=1, column=0, sticky="ew")
        actions.columnconfigure((0, 1), weight=1, uniform="run")
        # Only what applies now: New run / Resume while idle, End run while tracking.
        self.start_button = ttk.Button(actions, text="New run", command=self.start, style="Accent.TButton")
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, p(4)))
        self.resume_button = ttk.Button(actions, text="Resume", command=self.resume_selected_session, style="Ghost.TButton")
        self.resume_button.grid(row=0, column=1, sticky="ew")
        self.rail_state = ttk.Label(actions, text="● Tracking", style="RailLive.TLabel")
        self.rail_state.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(actions, text="End run", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew")
        self.activity_strip = ActivityStrip(rail, reduced_motion=self.reduced_motion, money=self._money, compact=True, width=1)
        self.activity_strip.grid(row=2, column=0, sticky="ew", pady=(p(4), 0))

        setup_title = ttk.Label(rail, text="SETUP  ·  Alt+1–4", style="RailKicker.TLabel")
        setup_title.grid(row=4, column=0, sticky="w", pady=(p(12), 0))
        self.setup_rail = ttk.Frame(rail, style="Panel.TFrame")
        self.setup_rail.grid(row=5, column=0, sticky="ew", pady=(p(4), p(12)))
        # In small windows the run bar has these; the drawer then starts at Healing.
        self._rail_bar_twins = (run_title, actions, self.activity_strip, setup_title, self.setup_rail)
        self.setup_rail.columnconfigure(0, weight=1)
        self.setup_rail_buttons: list[ttk.Button] = []
        for slot in range(4):
            button = ttk.Button(self.setup_rail, style="Ghost.TButton", width=1,
                                command=lambda index=slot: self._switch_setup_slot(index))
            button.grid(row=slot, column=0, sticky="ew", pady=(0, p(2)))
            self.setup_rail_buttons.append(button)
        ttk.Button(self.setup_rail, text="Edit setups…", style="Ghost.TButton",
                   command=lambda: (self._toggle_drawer(False), self.notebook.select(self.loadouts_tab))
                   ).grid(row=4, column=0, sticky="ew")

        healing_title = ttk.Frame(rail, style="Panel.TFrame")
        healing_title.grid(row=6, column=0, sticky="ew")
        healing_title.columnconfigure(0, weight=1)
        ttk.Label(healing_title, text="HEALING", style="RailKicker.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(healing_title, text="How it's costed", style="Footer.TButton",
                   command=self._show_healing_info).grid(row=0, column=1, sticky="e")
        self.hero_healing = ttk.Frame(rail, style="Panel.TFrame")
        healing = self.hero_healing
        healing.grid(row=7, column=0, sticky="ew", pady=(p(4), 0))
        healing.columnconfigure(1, weight=1)
        ttk.Label(healing, text="Heal", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, p(4)))
        self.healing_selector = SearchSelect(
            healing, textvariable=self.healing_tool, width=1,
            colors=self.colors, list_font=self._ui_font(9),
            values=["None", *sorted(self.catalog.healing_tools, key=str.casefold)])
        self.healing_selector.grid(row=0, column=1, columnspan=2, sticky="ew")
        self.healing_selector.bind("<<ComboboxSelected>>", lambda _event: self._healing_tool_changed())
        ttk.Label(healing, text="HoT", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, p(4)), pady=(p(4), 0))
        self.hot_selector = SearchSelect(
            healing, textvariable=self.hot_tool, width=1,
            colors=self.colors, list_font=self._ui_font(9),
            values=["None", *sorted((name for name, tool in self.catalog.healing_tools.items() if tool.has_hot), key=str.casefold)])
        self.hot_selector.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(p(4), 0))
        self.hot_selector.bind("<<ComboboxSelected>>", lambda _event: self._hot_tool_changed())
        self.healing_detail_label = ttk.Label(healing, textvariable=self.healing_detail, style="PanelMuted.TLabel", wraplength=p(180))
        self.healing_detail_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(p(4), 0))
        self.healing_totals_label = ttk.Label(healing, textvariable=self.healing_totals, style="RailValue.TLabel", wraplength=p(180))
        self.healing_totals_label.grid(row=3, column=0, columnspan=3, sticky="ew")
        healing.bind("<Configure>", self._on_healing_resize)

        ttk.Label(rail, text="EQUIPMENT", style="RailKicker.TLabel").grid(row=8, column=0, sticky="w", pady=(p(12), 0))
        self.hero_repair_label = ttk.Label(rail, textvariable=self.repair_radar_text, style="RailValue.TLabel", wraplength=p(180))
        self.hero_repair_label.grid(row=9, column=0, sticky="ew", pady=(p(4), 0))
        setup_line = ttk.Label(rail, textvariable=self.active_loadout_text, style="PanelMuted.TLabel", wraplength=p(180))
        setup_line.grid(row=10, column=0, sticky="ew", pady=(p(4), 0))
        self._rail_wrapped = (setup_line,)
        self.rail_scroll.attach()

    def _on_rail_resize(self, event: tk.Event) -> None:
        wrap = max(1, event.width - self._px(16))
        for label in (self.hero_repair_label, self.healing_detail_label, self.healing_totals_label,
                      *self.__dict__.get("_rail_wrapped", ())):
            label.configure(wraplength=wrap)

    def _build_hunt_chart(self, body: ttk.Frame) -> None:
        p = self._px
        self.chart_panel = ttk.Frame(body, style="Panel.TFrame", padding=(p(4), p(4)))
        panel = self.chart_panel
        panel.grid(row=0, column=1, sticky="nsew", pady=(0, p(8)))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="This run · net by encounter", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(p(4), 0))
        self.hunt_chart = tk.Canvas(panel, height=1, bg=self.colors["panel"], highlightthickness=0, takefocus=1)
        self.hunt_chart.grid(row=1, column=0, sticky="nsew", pady=(p(4), 0))
        self._hunt_view = None
        self._hunt_focus: int | None = None
        self._hunt_pulse = 0.0
        canvas = self.hunt_chart
        canvas.bind("<Configure>", lambda _event: self._draw_hunt_chart())
        canvas.bind("<Motion>", self._hunt_chart_hover)
        canvas.bind("<Leave>", lambda _event: None if self.focus_get() is canvas else self._set_hunt_focus(None))
        canvas.bind("<Button-1>", lambda event: (canvas.focus_set(), self._hunt_chart_hover(event)))
        canvas.bind("<FocusIn>", lambda _event: self._draw_hunt_chart())
        canvas.bind("<FocusOut>", lambda _event: self._set_hunt_focus(None))
        for key, move in (("<Left>", -1), ("<Right>", 1)):
            canvas.bind(key, lambda _event, step=move: self._step_hunt_focus(step))
        canvas.bind("<Home>", lambda _event: self._set_hunt_focus(0))
        canvas.bind("<End>", lambda _event: self._set_hunt_focus(len(self._hunt_view.steps) - 1 if self._hunt_view else None))
        canvas.bind("<bracketright>", lambda _event: self._jump_hunt_landmark(1))
        canvas.bind("<bracketleft>", lambda _event: self._jump_hunt_landmark(-1))
        canvas.bind("<Escape>", lambda _event: self._set_hunt_focus(None))

    # ----- cockpit chart -------------------------------------------------
    def _refresh_hunt_chart(self, session: SessionSummary | None) -> None:
        """Rebuild the reconciled trajectory when the hunt's events change.

        Reads stored attribution only (``live=False``): the HUD remains the single
        writer of attribution state, so two views never race on one session.
        """
        if not hasattr(self, "hunt_chart"):
            return
        try:
            minimized = self.state() == "iconic"
        except tk.TclError:
            minimized = False
        if minimized:
            # None of the cockpit is on screen (the HUD has its own projection):
            # rebuild once, from the then-current run, when the window returns.
            self._hunt_chart_deferred = (session,)
            return
        self._hunt_chart_deferred = None
        key = (session.session_id, session.events, session.accounting_revision,
               round(session.loot_value, 6), round(session.hunting_cost, 6), self.currency_mode.get()) if session else None
        if key == getattr(self, "_hunt_key", None):
            return
        previous = getattr(self, "_hunt_trajectory", None)
        trajectory = build_trajectory(None, [])
        if session is not None:
            try:
                builder_key = (session.session_id, session.accounting_revision)
                view = self.__dict__.get("_prepared_resume")
                if (getattr(self, "_hunt_builder_key", None) != builder_key
                        or session.events < getattr(self, "_hunt_events", 0)):
                    if (view is not None and view.builder is not None
                            and view.session.session_id == session.session_id):
                        self._hunt_builder = deepcopy(view.builder)
                    else:
                        self._hunt_builder = ActivityTrendBuilder(KILL_CLUSTER_WINDOW_SECONDS)
                    self._hunt_attribution = HuntAttribution(self.store)
                    self._hunt_builder_key = builder_key
                points = _kill_trend_points(self.store, session.session_id, builder=self._hunt_builder)
                points = self._hunt_attribution.update(session.session_id, points, live=False)
                trajectory = build_trajectory(session, points)
                log_provenance(getattr(self.store, "db_path", None), session.session_id, trajectory)
            except Exception:
                logging.exception("Hunt chart refresh failed; totals still shown")
                self._hunt_builder_key = None
                trajectory = build_trajectory(session, [])
        self._hunt_events = session.events if session else 0
        self._hunt_key = key
        self._hunt_trajectory = trajectory
        if trajectory.groups and session is not None:
            hours = active_hours(trajectory)
            self.hero_burn.set(f"{self._money(trajectory.cost / hours)}/h" if hours >= 0.05 else "—")
        else:
            self.hero_burn.set("—")
        fresh = (previous is not None and previous.groups and trajectory.standouts
                 and len(trajectory.groups) > len(previous.groups)
                 and trajectory.standouts[-1] >= len(previous.groups))
        if fresh and not self.reduced_motion.get() and self.running:
            self.animator.tween("hunt_chart", self._hunt_chart_pulse, 900)
        else:
            self._draw_hunt_chart()

    def _hunt_chart_pulse(self, phase: float) -> None:
        self._hunt_pulse = 0.0 if phase >= 1.0 else phase
        self._draw_hunt_chart()

    def _draw_hunt_chart(self) -> None:
        canvas = self.__dict__.get("hunt_chart")
        if canvas is None or not canvas.winfo_exists():
            return
        trajectory = self.__dict__.get("_hunt_trajectory") or build_trajectory(None, [])
        scale = max(0.5, self.display_scale)
        width = max(canvas.winfo_width(), 1) / scale
        height = max(canvas.winfo_height(), 1) / scale
        if width < 200 or height < 140:
            return
        view = chart_scene(trajectory, width, height, mode=self.currency_mode.get(), scale=scale,
                           focus=self._hunt_focus, pulse=self._hunt_pulse,
                           focused_ring=self.focus_get() is canvas, compact=height < 212)
        if self._hunt_focus is not None and self._hunt_focus >= len(view.steps):
            self._hunt_focus = None
        self._hunt_view = view
        paint_canvas(canvas, view.scene)

    def _hunt_chart_hover(self, event) -> None:
        view = self._hunt_view
        if view is None:
            return
        index = chart_step_at(view, event.x / max(0.5, self.display_scale))
        if index is not None and index != self._hunt_focus:
            self._set_hunt_focus(index)

    def _set_hunt_focus(self, index: int | None) -> str:
        self._hunt_focus = index
        self._draw_hunt_chart()
        return "break"

    def _step_hunt_focus(self, move: int) -> str:
        view = self._hunt_view
        if view is None or not view.steps:
            return "break"
        current = self._hunt_focus
        target = (len(view.steps) - 1 if move < 0 else 0) if current is None else current + move
        return self._set_hunt_focus(max(0, min(len(view.steps) - 1, target)))

    def _jump_hunt_landmark(self, direction: int) -> str:
        if self._hunt_view is None:
            return "break"
        return self._set_hunt_focus(next_landmark(self._hunt_view, self._hunt_focus, direction))

    def _switch_setup_slot(self, index: int) -> str:
        loadouts = list(self.store.list_loadouts())[:4]
        if 0 <= index < len(loadouts) and not loadouts[index].active:
            self._activate_saved_loadout(loadouts[index].id)
        return "break"

    def _refresh_setup_rail(self, loadouts) -> None:
        buttons = self.__dict__.get("setup_rail_buttons", [])
        loadouts = list(loadouts)
        for slot, button in enumerate(buttons):
            if slot < len(loadouts):
                loadout = loadouts[slot]
                mark = "✓ " if loadout.active else ""
                button.configure(text=f"{slot + 1}  {mark}{loadout.name}", state="normal",
                                 style="Accent.TButton" if loadout.active else "Ghost.TButton")
                button.grid()
            else:
                button.grid_remove()
        picker = self.__dict__.get("bar_setup")
        if picker is not None:
            picker.configure(values=[*(loadout.name for loadout in loadouts), "Edit setups…"])
            active = next((loadout.name for loadout in loadouts if loadout.active), "")
            picker.set(active or "No setup")

    def _on_currency_changed(self, *_args) -> None:
        """Refresh rendered money for selector and programmatic mode writes."""
        self._refresh_all()
        self._refresh_crafting_preview()

    def _money(self, value_ped: float, *, signed: bool = False, decimals: int = 2) -> str:
        try:
            mode_var = object.__getattribute__(self, "currency_mode")
        except AttributeError:
            mode_var = None
        mode = mode_var.get() if mode_var is not None else "USD"
        return format_money(value_ped, mode, signed=signed, decimals=decimals)

    def _money_label(self, label: str, value_ped: float, *, signed: bool = False, decimals: int = 2) -> str:
        return f"{label} {self._money(value_ped, signed=signed, decimals=decimals)}"

    def _add_hero_stat(self, parent: ttk.Frame, key: str, value: tk.StringVar, label: str, short: str) -> ttk.Label:
        frame = ttk.Frame(parent, style="HeroInset.TFrame")
        name = ttk.Label(frame, text=label, style="HeroSmallLabel.TLabel")
        shown = ttk.Label(frame, textvariable=value, style="ReadoutValue.TLabel")
        self._hero_cells[key] = (frame, name, shown, label, short)
        return shown

    def _on_hero_resize(self, event: tk.Event) -> None:
        if event.width >= self._px(120):
            self.hero_session_label.configure(wraplength=max(1, event.width - self._px(4)))

    def _on_healing_resize(self, event: tk.Event) -> None:
        if event.width > 1:
            for label in (self.healing_detail_label, self.healing_totals_label):
                label.configure(wraplength=max(1, event.width - self._px(4)))

    def _healing_tool_changed(self) -> None:
        name = self.healing_tool.get()
        self.store.set_selected_healing_tool(None if name == "None" else name)
        session = self.store.get_session(self.session_id) if self.session_id else None
        self._refresh_healing_summary(session)
        self.status_text.set("Healing selection saved for future messages; previous costs unchanged")

    def _hot_tool_changed(self) -> None:
        name = self.hot_tool.get()
        self.store.set_selected_hot_tool(None if name == "None" else name)
        session = self.store.get_session(self.session_id) if self.session_id else None
        self._refresh_healing_summary(session)
        self.status_text.set("HoT selection saved for future activations; retained cast context and past costs unchanged")

    def _refresh_healing_summary(self, session: SessionSummary | None) -> None:
        """Priced tools show their per-use cost; pricing caveats stay in the ? help and logs."""
        def detail(label, name):
            tool = self.catalog.healing_tools.get(name)
            if (tool is None or not tool.cost_verified or tool.cost_per_use is None or tool.tracking_limit
                    or (label == "HoT" and not tool.has_hot)
                    or (tool.has_hot and (tool.hot_fraction is None or tool.hot_duration is None))):
                return ""
            unit = "cast" if tool.has_hot else "use"
            return f"{label} {self._money(tool.cost_per_use, decimals=5)}/{unit}"
        self.healing_detail.set(" · ".join(part for part in (
            detail("Heal", self.healing_tool.get()), detail("HoT", self.hot_tool.get())) if part))
        uses = session.estimated_healing_uses if session else 0
        self.healing_totals.set(f"Healing {self._money(session.healing_cost if session else 0, decimals=5)}"
                                + (f" · {uses:,} uses" if uses else ""))

    def _show_healing_info(self) -> None:
        tools = [self.catalog.healing_tools.get(var.get()) for var in (self.healing_tool, self.hot_tool)]
        notes = "\n".join(f"{tool.name}: {tool.verification_note or tool.tracking_limit or 'selected-profile estimate'}\n{tool.source_url}"
                          for tool in tools if tool)
        messagebox.showinfo(
            "Healing cost",
            "Pick the healing tool you use and, if you have one, your heal-over-time (HoT) tool. "
            "PED Hunter adds an estimated cost each time the chat log shows a heal.\n\n"
            "The log does not say which tool you fired, so keep these matching what you actually use. "
            "Changing them affects future heals only; costs already recorded stay as they are.\n\n"
            "A HoT is charged once per cast, not per tick. Heals the log does not clearly show "
            "(silent uses, overlapping effects, a second identical heal in the same second) are left "
            "uncosted rather than guessed."
            + (f"\n\nDetails for the selected tools\n{notes}" if notes else ""),
            parent=self,
        )

    def _toggle_top_area(self) -> None:
        """Hide or show the readout strip; the status line stays."""
        expanded = not self.top_area_expanded.get()
        self.top_area_expanded.set(expanded)
        self.top_toggle_text.set("Hide totals" if expanded else "Show totals")
        if expanded:
            self.hero_panel.grid()
        else:
            self.hero_panel.grid_remove()

    def _on_restore(self, event: tk.Event) -> None:
        deferred = self.__dict__.get("_hunt_chart_deferred")
        if event.widget is self and deferred is not None:
            with _held(self.store):
                self._refresh_hunt_chart(deferred[0])

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        scale = window_ui_scale(self)
        if abs(scale - self.display_scale) > 0.01:
            self.display_scale = scale
            self.minsize(round(MIN_SIZE[0] * scale), round(MIN_SIZE[1] * scale))
            self._configure_theme()
            self._apply_size_class(event.width, event.height, force=True)
            return
        self._apply_size_class(event.width, event.height)

    def _build_dashboard_tab(self) -> None:
        """Runs: every saved run, and Resume for the selected one."""
        p = self._px
        tab = self.dashboard_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        self.statistics_tab.columnconfigure(0, weight=1)
        totals_panel = ttk.Frame(self.statistics_tab, style="Panel.TFrame", padding=(p(8), p(8)))
        totals_panel.grid(row=0, column=0, sticky="new")
        self._build_lifetime_totals(totals_panel)

        table = ttk.Frame(tab, style="Panel.TFrame")
        table.grid(row=0, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        numeric = ("return", "loot", "cost", "net", "events")
        self.sessions_tree = self._tree(
            table,
            headings=("Started", "Setup", "Return", "Loot", "Cost", "Net", "Events", "Status"),
            columns=("started", "setup", "return", "loot", "cost", "net", "events", "status"),
            numeric=numeric, widths={"started": 104, "setup": 130, "return": 66, "loot": 70, "cost": 70,
                                     "net": 72, "events": 64, "status": 66},
        )
        make_sortable(self.sessions_tree, numeric=numeric, text=("started", "setup", "status"))
        self.sessions_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_session_selected())

        action = ttk.Frame(tab, style="Card.TFrame", padding=(p(8), p(4)))
        action.grid(row=1, column=0, sticky="ew", pady=(p(4), 0))
        action.columnconfigure(0, weight=1)
        ttk.Label(action, textvariable=self.selected_session_text, style="CardLabel.TLabel", width=1, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, p(8)))
        self.resume_run_button = ttk.Button(action, text="Resume this run", command=self.resume_selected_session,
                                            style="Accent.TButton")
        self.resume_run_button.grid(row=0, column=1, sticky="e")

    def _build_lifetime_totals(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, textvariable=self.lifetime_summary_text, style="PanelMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, self._px(8)))
        self.lifetime_grid = ttk.Frame(parent, style="Panel.TFrame")
        self.lifetime_grid.grid(row=1, column=0, sticky="ew")
        self.lifetime_cards: list[ttk.Frame] = []
        self.lifetime_notes: list[ttk.Label] = []  # explanations, shown when there is room
        for key, label in (
            ("total_net", "LIFETIME NET"), ("overall_return", "OVERALL RETURN"), ("total_loot", "TOTAL LOOT"),
            ("total_cost", "TOTAL COST"), ("avg_profit", "AVG NET / RUN"), ("avg_return", "AVG RETURN / RUN"),
            ("best_run", "BEST RUN"), ("worst_run", "WORST RUN"), ("total_events", "EVENTS"),
        ):
            self._add_lifetime_card(self.lifetime_grid, key, label)

    def _add_lifetime_card(self, parent: ttk.Frame, key: str, label: str) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(self._px(8), self._px(4)))
        ttk.Label(frame, text=label, style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=self.lifetime_vars[key], style="CardValue.TLabel").pack(anchor="w")
        note = {"overall_return": "All loot ÷ all cost", "avg_return": "Each run counts equally"}.get(key)
        if note:
            self.lifetime_notes.append(ttk.Label(frame, text=note, style="CardLabel.TLabel"))
        self.lifetime_cards.append(frame)

    def _build_events_tab(self) -> None:
        """Events: what this run recorded, newest first, plus the one everyday correction."""
        p = self._px
        tab = self.events_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        bar = ttk.Frame(tab, style="Root.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, p(4)))
        bar.columnconfigure(2, weight=1)
        ttk.Label(bar, text="Show", style="Muted.TLabel").grid(row=0, column=0, padx=(0, p(4)))
        self.event_filter = tk.StringVar(value="Loot")
        picker = ttk.Combobox(bar, textvariable=self.event_filter, values=tuple(EVENT_FILTERS), state="readonly", width=14)
        picker.grid(row=0, column=1, sticky="w")
        picker.bind("<<ComboboxSelected>>", lambda _event: (picker.selection_clear(), self._event_filter_changed()))
        self.corrections_text = tk.StringVar(value="Loot corrections…")
        self.exclude_loot_button = ttk.Button(bar, text="Don't count this loot…", command=self._exclude_selected_loot,
                                              state="disabled")
        self.exclude_loot_button.grid(row=0, column=3, padx=(p(8), p(4)))
        self.event_history_button = ttk.Button(bar, textvariable=self.corrections_text, command=self._open_event_history,
                                               style="Ghost.TButton")
        self.event_history_button.grid(row=0, column=4)

        self.loot_chart_canvas = tk.Canvas(tab, bg=self.colors["panel_2"], highlightthickness=0, relief="flat", height=p(96))
        self.loot_chart_canvas.grid(row=1, column=0, sticky="ew", pady=(0, p(4)))
        self.loot_chart_canvas.bind("<Configure>", lambda _event: self._draw_loot_chart())

        table = ttk.Frame(tab, style="Panel.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.events_tree = self._tree(table, columns=("time", "kind", "summary"), headings=("Time", "Type", "Detail"),
                                      widths={"time": 92, "kind": 64, "summary": 320}, fixed=("time", "kind"))
        self.events_tree.bind("<<TreeviewSelect>>", lambda _event: self._event_selection_changed())
        self._event_kinds: dict[str, str] = {}
        note = ttk.Label(tab, style="Muted.TLabel", text=(
            "Not from this hunt (a trade, a pickup)? Select the loot and choose Don't count this loot. "
            "Nothing is deleted; Loot corrections can count it again."))
        note.grid(row=3, column=0, sticky="ew", pady=(p(4), 0))
        tab.bind("<Configure>", lambda event: note.configure(wraplength=max(1, event.width - p(8)))
                 if event.widget is tab else None)

    def _event_filter_changed(self) -> None:
        self._event_context = None
        self._event_ids.clear()
        with _held(self.store):
            self._refresh_events(self._session_id_value())

    def _event_selection_changed(self) -> None:
        selection = self.events_tree.selection()
        loot = bool(selection) and self._event_kinds.get(selection[0]) == "loot"
        self.exclude_loot_button.configure(state="normal" if loot else "disabled")

    def _exclude_selected_loot(self) -> None:
        """Open Loot corrections on the selected receipt; the change is confirmed there."""
        selection = self.events_tree.selection()
        if not selection or self._event_kinds.get(selection[0]) != "loot":
            return
        self._open_event_history()
        window = self._event_history_window
        window.select_run(self._session_id_value())
        window.event_id.set(selection[0])
        window.find_event()
        window.lift()

    def _open_event_history(self) -> None:
        from .event_history import EventHistoryWindow
        window = self.__dict__.get("_event_history_window")
        if window is None or not window.winfo_exists():
            self._event_history_window = EventHistoryWindow(self)
        else:
            window.deiconify()

    def _history_accounting_changed(self, session_id: str) -> None:
        """Refresh corrected accounting without touching the running source reader."""
        self._activity_cache = None
        self._feedback_context = None
        self._chart_context = None
        self._event_context = None
        self._event_ids.clear()
        self._prepared_resume = None
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window._feedback_context = None
        self._refresh_all()

    def _build_skills_tab(self) -> None:
        p = self._px
        tab = self.skills_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, textvariable=self.skill_summary_text, style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, p(4)))
        table = ttk.Frame(tab, style="Panel.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        numeric = ("xp", "procs", "proc_pct")
        self.skills_tree = self._tree(table, columns=("skill", "xp", "procs", "proc_pct"),
                                      headings=("Skill", "XP gained", "Procs", "Proc %"), numeric=numeric,
                                      widths={"skill": 200, "xp": 90, "procs": 70, "proc_pct": 70})
        make_sortable(self.skills_tree, numeric=numeric, text=("skill",), default=("xp", True))

    def _build_loadouts_tab(self) -> None:
        p = self._px
        tab = self.loadouts_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        list_panel = self._panel(tab, "Saved setups", 0, 0)
        # The comparison table may scroll horizontally; it must not force the
        # editor narrower than its fields just to satisfy every column's width.
        self.loadout_list_panel = list_panel
        list_panel.configure(width=p(250))
        list_panel.grid_propagate(False)
        self.loadouts_tree = self._tree(
            list_panel,
            columns=("active", "name", "weapon", "amp", "enh", "cost"),
            headings=("#", "Name", "Weapon", "Amp", "Enhancers", "Cost/shot"),
            widths={"active": 34, "name": 96, "weapon": 130, "amp": 100, "enh": 80, "cost": 64}, fixed=("active", "cost"),
        )
        xscroll = ttk.Scrollbar(list_panel, orient="horizontal", command=self.loadouts_tree.xview)
        self.loadouts_tree.configure(xscrollcommand=xscroll.set)
        xscroll.grid(row=1, column=0, sticky="ew")
        hint = ttk.Label(list_panel, text="Click to use · drag to reorder · 1–4 are the Alt+1–4 slots",
                         style="PanelMuted.TLabel", wraplength=p(180))
        hint.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(p(4), 0))
        self._loadout_drag = None
        for index, color in enumerate(HUD_SETUP_COLORS):
            self.loadouts_tree.tag_configure(f"hud_slot_{index}", foreground=color)
        self.loadouts_tree.bind("<ButtonPress-1>", self._loadout_drag_start)
        self.loadouts_tree.bind("<B1-Motion>", self._loadout_drag_motion)
        self.loadouts_tree.bind("<Escape>", self._cancel_loadout_drag)
        self.loadouts_tree.bind("<<TreeviewSelect>>", lambda _event: self._load_selected_loadout())
        self.loadouts_tree.bind("<ButtonRelease-1>", self._loadout_row_clicked)
        self.loadouts_tree.bind("<Return>", lambda _event: self._load_selected_loadout(explicit=True))

        editor = self._panel(tab, "Setup builder", 0, 1)
        self.loadout_scroll = ScrollPane(editor, background=self.colors["panel"], autohide=True)
        self.loadout_scroll.grid(row=0, column=0, sticky="nsew")
        form = self.loadout_scroll.content
        form.columnconfigure(1, weight=1)

        def select(variable, values):
            return SearchSelect(form, textvariable=variable, values=values, on_change=self._refresh_loadout_preview,
                                colors=self.colors, list_font=self._ui_font(9), width=1)

        weapon_names = sorted(self.catalog.weapons, key=_listing_order)
        amp_names = ["None"] + _attachment_names_by_category(self.catalog, AMP_CATEGORIES)
        scope_names = ["None"] + _attachment_names_by_category(self.catalog, SCOPE_CATEGORIES)
        sight_names = ["None"] + _attachment_names_by_category(self.catalog, SIGHT_CATEGORIES)

        def spin(variable):
            return ttk.Spinbox(form, from_=0, to=10, textvariable=variable, width=6, command=self._refresh_loadout_preview)

        rows = [
            ("Name", ttk.Entry(form, textvariable=self.loadout_name, width=1)),
            ("Weapon", select(self.loadout_weapon, weapon_names)),
            ("Amplifier", select(self.loadout_amp, amp_names)),
            ("Scope", select(self.loadout_scope, scope_names)),
            ("Sight 1", select(self.loadout_sight_1, sight_names)),
            ("Sight 2", select(self.loadout_sight_2, sight_names)),
            ("Damage enhancers", spin(self.damage_enhancers)),
            ("Accuracy enhancers", spin(self.accuracy_enhancers)),
            ("Economy enhancers", spin(self.economy_enhancers)),
        ]
        self.loadout_fields = dict(rows)
        for r, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label, style="PanelMuted.TLabel").grid(row=r, column=0, sticky="w", padx=(0, p(8)), pady=p(2))
            widget.grid(row=r, column=1, sticky="w" if isinstance(widget, ttk.Spinbox) else "ew", pady=p(2))
            widget.bind("<KeyRelease>", lambda _event: self._refresh_loadout_preview())
            widget.bind("<FocusOut>", lambda _event: self._refresh_loadout_preview())
        note = ttk.Label(form, style="PanelMuted.TLabel", text=(
            "Click a selector to open its list, or type part of a name to narrow it. "
            "A setup is the gear you shoot with: it sets the cost per shot behind cost, return and net. "
            "Each damage enhancer adds 10% ammo and decay; each economy enhancer removes 1%."))
        note.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=(p(8), 0))
        form.bind("<Configure>", lambda event: note.configure(wraplength=max(1, event.width - p(4)))
                  if event.widget is form else None, add="+")  # keep ScrollPane's own binding
        self.loadout_scroll.attach()

        # Actions and cost feedback belong outside the scroll viewport.
        footer = ttk.Frame(editor, style="Panel.TFrame")
        footer.grid(row=1, column=0, sticky="ew", pady=(p(4), 0))
        footer.columnconfigure(0, weight=1)
        draft = ttk.Label(footer, textvariable=self.loadout_draft_text, style="PanelMuted.TLabel")
        draft.grid(row=0, column=0, sticky="ew")
        # Hidden in small windows so the fields keep the height.
        self._setup_extras = (hint, xscroll, draft, *(panel.master.grid_slaves(row=0, column=0)[0]
                                                      for panel in (list_panel, editor)))
        preview = ttk.Label(footer, textvariable=self.loadout_preview, style="Panel.TLabel")
        preview.grid(row=1, column=0, sticky="ew", pady=(p(2), p(4)))
        footer.bind("<Configure>", lambda event: (
            draft.configure(wraplength=max(1, event.width)),
            preview.configure(wraplength=max(1, event.width)),
        ))
        buttons = ttk.Frame(footer, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")
        self.save_activate_button = ttk.Button(buttons, text="Save and activate", command=lambda: self._save_loadout(make_active=True), style="Accent.TButton")
        self.save_activate_button.pack(side="left", padx=(0, p(4)))
        self.save_loadout_button = ttk.Button(buttons, text="Save", command=lambda: self._save_loadout(make_active=False))
        self.save_loadout_button.pack(side="left", padx=(0, p(4)))
        self.delete_loadout_button = ttk.Button(buttons, text="Delete", command=self._delete_selected_loadout, state="disabled")
        self.delete_loadout_button.pack(side="left")
        ttk.Button(buttons, text="Weapon data…", command=self._open_weapon_data, style="Ghost.TButton").pack(side="right")
        self._new_loadout_draft()

    def _build_manufacturing_tab(self) -> None:
        """Crafting: add the material cost the chat log does not report."""
        p = self._px
        tab = self.manufacturing_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        form = self._panel(tab, "Add crafting cost", 0, 0)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(0, weight=0)

        blueprint_names = sorted(self.catalog.blueprints, key=_listing_order)
        blueprint_combo = SearchSelect(form, textvariable=self.crafting_blueprint, values=blueprint_names,
                                       on_change=self._refresh_crafting_preview, colors=self.colors,
                                       list_font=self._ui_font(9), width=1)
        attempts_spin = ttk.Spinbox(form, from_=1, to=1000000, textvariable=self.crafting_attempts, width=10, command=self._refresh_crafting_preview)
        blueprint_combo.bind("<KeyRelease>", lambda _event: self._refresh_crafting_preview())
        attempts_spin.bind("<KeyRelease>", lambda _event: self._refresh_crafting_preview())

        ttk.Label(form, text="Blueprint", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, p(8)), pady=p(2))
        blueprint_combo.grid(row=0, column=1, sticky="ew", pady=p(2))
        ttk.Label(form, text="Attempts", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, p(8)), pady=p(2))
        attempts_spin.grid(row=1, column=1, sticky="w", pady=p(2))
        self.crafting_preview_label = ttk.Label(form, textvariable=self.crafting_preview, style="Panel.TLabel",
                                                font=self._ui_font(10, family="Segoe UI Semibold"), width=1, wraplength=p(400))
        self.crafting_preview_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(p(8), p(4)))
        ttk.Button(form, text="Add to this run's cost", command=self._add_crafting_cost_to_session, style="Accent.TButton").grid(row=3, column=0, columnspan=2, sticky="w")
        note = ttk.Label(form, style="PanelMuted.TLabel", width=1, text=(
            "Crafting spends materials the chat log doesn't report. Pick the blueprint and how many attempts you ran; "
            "PED Hunter adds the materials' TT value to this run's cost. What you craft is still picked up as loot, "
            "so return and net show your real crafting result."))
        note.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(p(12), 0))
        form.bind("<Configure>", lambda event: (
            note.configure(wraplength=max(1, event.width - p(4))),
            self.crafting_preview_label.configure(wraplength=max(1, event.width - p(4))),
        ) if event.widget is form else None)
        self._refresh_crafting_preview()

    def _build_catalog_tab(self) -> None:
        """Weapon data: a lookup window opened from Setups, not an everyday tab."""
        p = self._px
        self.catalog_window = window = tk.Toplevel(self)
        window.withdraw()
        window.title("PED Hunter · Weapon data")
        window.configure(bg=self.colors["bg"])
        window.geometry(f"{p(720)}x{p(420)}")
        window.minsize(p(420), p(260))
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        self.catalog_tab = tab = ttk.Frame(window, style="Root.TFrame", padding=(p(8), p(8)))
        tab.pack(fill="both", expand=True)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        ttk.Label(tab, style="Muted.TLabel", text="The weapon figures PED Hunter uses for cost per shot. "
                  "Look here if a setup's cost seems off.").grid(row=0, column=0, sticky="w", pady=(0, p(8)))
        search = ttk.Frame(tab, style="Root.TFrame")
        search.grid(row=1, column=0, sticky="ew", pady=(0, p(8)))
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="Find weapon", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, p(8)))
        entry = ttk.Entry(search, textvariable=self.catalog_query)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<KeyRelease>", lambda _event: self._search_catalog())
        table = ttk.Frame(tab, style="Panel.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        numeric = ("ammo", "decay", "cost")
        self.catalog_tree = self._tree(
            table, columns=("name", "category", "ammo", "decay", "cost", "aliases"),
            headings=("Name", "Class", "Ammo", "Decay (PED)", "Cost/shot (PED)", "Also known as"), numeric=numeric,
            widths={"name": 190, "category": 100, "ammo": 56, "decay": 90, "cost": 112, "aliases": 130})
        make_sortable(self.catalog_tree, numeric=numeric, text=("name", "category"))

    def _open_weapon_data(self) -> None:
        if "catalog_tree" not in self.__dict__:
            self._build_catalog_tab()
        weapon = self.loadout_weapon.get().strip()
        if weapon:
            self.catalog_query.set(weapon)
        self._search_catalog()
        self.catalog_window.deiconify()
        self.catalog_window.lift()

    def _build_wtb_tab(self) -> None:
        p = self._px
        tab = self.wtb_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, textvariable=self.wtb_summary_text, style="Muted.TLabel", width=1, anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(0, p(4)))
        table = ttk.Frame(tab, style="Panel.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.wtb_tree = self._tree(
            table, columns=("time", "player", "want", "qty", "price"),
            headings=("Seen", "Player", "Wants to buy", "Qty", "Price"), numeric=("qty",),
            widths={"time": 92, "player": 130, "want": 240, "qty": 50, "price": 80}, fixed=("time", "qty"))
        make_sortable(self.wtb_tree, numeric=("qty",), text=("time", "player", "want", "price"))
        self.wtb_tree.bind("<<TreeviewSelect>>", lambda _event: self._wtb_selected())
        self._wtb_details: dict[str, str] = {}
        self.wtb_detail_text = tk.StringVar(value="Select a request to read the full message.")
        detail = ttk.Label(tab, textvariable=self.wtb_detail_text, style="Muted.TLabel")
        detail.grid(row=2, column=0, sticky="ew", pady=(p(4), 0))
        tab.bind("<Configure>", lambda event: detail.configure(wraplength=max(1, event.width - p(8)))
                 if event.widget is tab else None)

    def _wtb_selected(self) -> None:
        selection = self.wtb_tree.selection()
        self.wtb_detail_text.set(self._wtb_details.get(selection[0], "") if selection
                                 else "Select a request to read the full message.")

    def _build_setup_tab(self) -> None:
        """Help & settings: the few settings first, then task-focused help."""
        p = self._px
        tab = self.setup_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.help_scroll = ScrollPane(tab, background=self.colors["panel"], autohide=True)
        self.help_scroll.grid(row=0, column=0, sticky="nsew")
        panel = self.help_scroll.content
        panel.columnconfigure(0, weight=1)

        settings = ttk.Frame(panel, style="Panel.TFrame", padding=(p(8), p(8)))
        settings.grid(row=0, column=0, sticky="ew")
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="SETTINGS", style="RailKicker.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, p(4)))
        ttk.Label(settings, text="Chat log", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, p(8)))
        self.chat_log_entry = ttk.Entry(settings, textvariable=self.chat_path, width=1)
        self.chat_log_entry.grid(row=1, column=1, sticky="ew", padx=(0, p(4)))
        ttk.Button(settings, text="Browse…", command=self._browse_log).grid(row=1, column=2)
        ttk.Label(settings, text="Show money in", style="PanelMuted.TLabel").grid(row=2, column=0, sticky="w", padx=(0, p(8)), pady=(p(8), 0))
        self.currency_picker = currency = ttk.Combobox(settings, textvariable=self.currency_mode, values=("USD", "PED"),
                                                       state="readonly", width=6)
        currency.grid(row=2, column=1, sticky="w", pady=(p(8), 0))
        currency.bind("<<ComboboxSelected>>", lambda _event: currency.selection_clear())
        options = ttk.Frame(settings, style="Panel.TFrame")
        options.grid(row=3, column=0, columnspan=3, sticky="w", pady=(p(8), 0))
        self.reduced_motion_button = ttk.Checkbutton(options, text="Reduced motion", variable=self.reduced_motion, command=self._refresh_all)
        self.reduced_motion_button.grid(row=0, column=0, sticky="w", padx=(0, p(12)))
        self.feedback_sounds_button = ttk.Checkbutton(options, text="Quiet loot and skill sounds", variable=self.feedback_sounds)
        self.feedback_sounds_button.grid(row=0, column=1, sticky="w")
        actions = ttk.Frame(settings, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(p(8), 0))
        self.preview_feedback_button = ttk.Button(actions, text="Preview live feedback (demo data)", command=self._preview_feedback)
        self.preview_feedback_button.grid(row=0, column=0, sticky="w", padx=(0, p(4)))
        self.refresh_data_button = ttk.Button(actions, text="Reload saved data", command=self._refresh_all)
        self.refresh_data_button.grid(row=0, column=1, sticky="w")

        text = tk.Text(
            panel, width=1, height=1, wrap="word", bg=self.colors["panel_2"], fg=self.colors["text"], relief="flat",
            font=self._ui_font(9), padx=p(8), pady=p(8), spacing1=p(1), spacing3=p(1),
        )
        text.grid(row=1, column=0, sticky="ew", padx=p(8), pady=(0, p(8)))
        text.tag_configure("heading", font=self._ui_font(9, family="Segoe UI Semibold"), foreground=self.colors["accent"],
                           spacing1=p(8))
        for heading, body in HELP_SECTIONS:
            text.insert("end", heading + "\n", "heading")
            text.insert("end", body + "\n")
        text.delete("end-1c", "end")
        text.configure(state="disabled")
        self.help_scroll.fit_text(text)
        self.help_scroll.attach()

    def _preview_feedback(self) -> None:
        from .preview import open_preview
        open_preview()

    def _add_metric_card(self, parent: ttk.Frame, column: int, key: str, value: str, label: str) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(18, 16))
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
        value_var = tk.StringVar(value=value)
        label_var = tk.StringVar(value=label)
        ttk.Label(frame, textvariable=label_var, style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=value_var, style="CardValue.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Frame(frame, style="AccentLine.TFrame", height=2).pack(fill="x", pady=(14, 0))
        self.metric_cards[key] = MetricCard(frame=frame, value=value_var, label=label_var)

    def _panel(self, parent: ttk.Frame, title: str, row: int, column: int) -> ttk.Frame:
        p = self._px
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=(p(8), p(4), p(8), p(8)))
        panel.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else p(8), 0), pady=(0 if row == 0 else p(8), 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text=title, style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, p(4)))
        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        return body

    def _tree(self, parent: ttk.Frame, *, columns: tuple[str, ...], headings: tuple[str, ...],
              widths: dict[str, int] | None = None, numeric: tuple[str, ...] = (),
              fixed: tuple[str, ...] = ()) -> ttk.Treeview:
        """A table: ``widths`` in logical px, ``numeric`` right-aligned, ``fixed`` never stretched."""
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse", style="Treeview", height=3)
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        tree.configure(yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns", padx=(self._px(4), 0))
        tree.tag_configure("odd", background="#0d1421")
        tree.tag_configure("even", background="#0a0f1a")
        tree.tag_configure("arrival", background="#123b38", foreground="#d8fffb")
        tree.base_widths = {}
        for col, heading in zip(columns, headings):
            anchor = "e" if col in numeric else "w"
            tree.heading(col, text=heading, anchor=anchor)
            tree.base_widths[col] = self._px((widths or {}).get(col, 110))
            tree.column(col, width=tree.base_widths[col], minwidth=self._px(36),
                        stretch=col not in fixed and col not in numeric, anchor=anchor)
        tree.bind("<Configure>", lambda event: self._fit_tree(tree, event.width), add="+")
        return tree

    @staticmethod
    def _fit_tree(tree: ttk.Treeview, width: int | None = None) -> None:
        """Share the table's width among the columns now shown (they change with the size class)."""
        width = tree.winfo_width() if width is None else width
        shown = tree.cget("displaycolumns")
        shown = list(tree.cget("columns")) if "#all" in shown else list(shown)
        base = tree.base_widths
        spare = width - sum(base[col] for col in shown) - 2
        flexible = [col for col in shown if str(tree.column(col, "stretch")) in ("1", "True")] or shown[-1:]
        total = sum(base[col] for col in flexible) or 1
        for col in shown:
            extra = round(spare * base[col] / total) if spare > 0 and col in flexible else 0
            tree.column(col, width=base[col] + extra)

    def _refresh_loadout_preview(self) -> None:
        self._update_loadout_draft()
        try:
            loadout = self._loadout_from_form()
        except ValueError as exc:
            self.loadout_preview.set(f"Not ready to save: {exc}")
            return
        self.loadout_preview.set(
            f"Ammo burn: {loadout.ammo_burn} • Decay: {self._money(loadout.decay, decimals=5)} • Cost/shot: {self._money(loadout.cost_per_shot, decimals=5)}"
        )

    def _loadout_from_form(self) -> LoadoutRecord:
        name = self.loadout_name.get().strip()
        if not name:
            raise ValueError("name is required")
        weapon_name = self.loadout_weapon.get().strip()
        weapon = self.catalog.find_weapon(weapon_name) if weapon_name else None
        if not weapon:
            raise ValueError("choose a known weapon")
        if not _attachment_in_categories(self.catalog, self.loadout_amp.get(), AMP_CATEGORIES):
            raise ValueError("amplifier must be an amp, not a scope or sight")
        if not _attachment_in_categories(self.catalog, self.loadout_scope.get(), SCOPE_CATEGORIES):
            raise ValueError("scope must be selected from scope items")
        if not _attachment_in_categories(self.catalog, self.loadout_sight_1.get(), SIGHT_CATEGORIES):
            raise ValueError("sight 1 must be selected from sight items")
        if not _attachment_in_categories(self.catalog, self.loadout_sight_2.get(), SIGHT_CATEGORIES):
            raise ValueError("sight 2 must be selected from sight items")
        damage = _bounded_int(self.damage_enhancers.get())
        accuracy = _bounded_int(self.accuracy_enhancers.get())
        economy = _bounded_int(self.economy_enhancers.get())
        if weapon.category.startswith("Mining Laser") and accuracy:
            raise ValueError("Mining laser enhancer compatibility is unverified; use an unmodified setup")
        ammo, decay = calculate_loadout_cost(
            catalog=self.catalog,
            weapon_name=weapon.name,
            amp=_clean_part(self.loadout_amp.get()),
            scope=_clean_part(self.loadout_scope.get()),
            sight_1=_clean_part(self.loadout_sight_1.get()),
            sight_2=_clean_part(self.loadout_sight_2.get()),
            damage_enhancers=damage,
            economy_enhancers=economy,
        )
        return LoadoutRecord(
            id=self.selected_loadout_id,
            name=name,
            weapon=weapon.name,
            amp=_clean_part(self.loadout_amp.get()),
            scope=_clean_part(self.loadout_scope.get()),
            sight_1=_clean_part(self.loadout_sight_1.get()),
            sight_2=_clean_part(self.loadout_sight_2.get()),
            damage_enhancers=damage,
            accuracy_enhancers=accuracy,
            economy_enhancers=economy,
            ammo_burn=ammo,
            decay=decay,
            cost_per_shot=decay + (ammo / 10_000.0),
        )

    def _loadout_form_state(self) -> tuple[str, ...]:
        return tuple(variable.get() for variable in (
            self.loadout_name, self.loadout_weapon, self.loadout_amp, self.loadout_scope,
            self.loadout_sight_1, self.loadout_sight_2, self.damage_enhancers,
            self.accuracy_enhancers, self.economy_enhancers,
        ))

    def _update_loadout_draft(self) -> None:
        title = "New setup · not saved" if self.selected_loadout_id is None else "Editing saved setup"
        if self._loadout_form_state() != self._loadout_clean_state:
            title += " · Unsaved changes"
        self.loadout_draft_text.set(title)
        self.delete_loadout_button.configure(state="disabled" if self.selected_loadout_id is None else "normal")

    def _new_loadout_draft(self) -> None:
        self._populate_loadout_form(LoadoutRecord(id=None, name="", weapon=""))

    def _select_loadout_row(self) -> None:
        row = "new" if self.selected_loadout_id is None else str(self.selected_loadout_id)
        if self.loadouts_tree.exists(row):
            self.loadouts_tree.selection_set(row)
            self.loadouts_tree.focus(row)

    def _confirm_loadout_change(self) -> bool:
        if self._loadout_form_state() == self._loadout_clean_state:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved setup changes",
            "Save this setup before changing selection?\n\n"
            "Yes saves without activating. No discards these edits. Cancel keeps editing.",
            parent=self,
        )
        return self._save_loadout(make_active=False) if answer else answer is not None

    def _loadout_drag_start(self, event: tk.Event) -> str | None:
        row = self.loadouts_tree.identify_row(event.y)
        self._loadout_drag = None
        if not row:
            return None
        # Defer the class selection binding until release. A drag must neither
        # activate equipment nor discard the form/draft underneath it.
        self.loadouts_tree.focus_set()
        self._loadout_drag = {"row": row, "y": event.y, "dragged": False}
        return "break"

    def _loadout_drag_motion(self, event: tk.Event) -> str | None:
        state = self._loadout_drag
        if not state:
            return None
        if state.get("cancelled"):
            return "break"
        if state["row"] == "new":
            if abs(event.y - state["y"]) >= 5:
                state["cancelled"] = True
            return "break"
        if not state["dragged"] and abs(event.y - state["y"]) < 5:
            return "break"
        state["dragged"] = True
        tree = self.loadouts_tree
        if event.y < 20:
            tree.yview_scroll(-1, "units")
        elif event.y > tree.winfo_height() - 20:
            tree.yview_scroll(1, "units")
        target = tree.identify_row(event.y)
        index = max(1, tree.index(target)) if target else len(tree.get_children()) - 1
        tree.move(state["row"], "", index)
        return "break"

    def _cancel_loadout_drag(self, _event=None) -> str | None:
        if not self._loadout_drag:
            return None
        # Keep suppression until release; Escape must not become a row click.
        self._loadout_drag["cancelled"] = True
        self._refresh_loadouts()
        return "break"

    def _loadout_row_clicked(self, event: tk.Event) -> str | None:
        state = self.__dict__.get("_loadout_drag")
        self._loadout_drag = None
        if state is None:
            return None
        if state.get("cancelled"):
            return "break"
        if state["dragged"]:
            try:
                self.store.reorder_loadouts([int(row) for row in self.loadouts_tree.get_children() if row != "new"])
            except ValueError as exc:
                self.status_text.set(str(exc))
            self._refresh_loadouts()
            return "break"
        self.loadouts_tree.selection_set(state["row"])
        self._load_selected_loadout(explicit=True)
        return "break"

    def _save_loadout(self, *, make_active: bool) -> bool:
        try:
            loadout = self._loadout_from_form()
            saved = self.store.list_loadouts()
            if any(item.name == loadout.name and item.id != loadout.id for item in saved):
                raise ValueError("A setup already has that name. Choose a different name, or select that saved setup to edit it.")
        except ValueError as exc:
            messagebox.showerror("PED Hunter", str(exc), parent=self)
            return False
        previous = next((item for item in saved if item.id == loadout.id), None)
        loadout.active = make_active or bool(previous and previous.active)
        loadout.id = self.store.save_loadout(loadout, make_active=make_active)
        self._populate_loadout_form(loadout)
        if loadout.active and self.running and self.session_id:
            synced_loadout = with_repair_estimates(self.catalog, loadout)
            self.store.update_session_loadout(self.session_id, synced_loadout)
        self.status_text.set(f"Saved loadout: {loadout.name}")
        self._refresh_all()
        return True


    def _delete_selected_loadout(self) -> None:
        if self.selected_loadout_id is None:
            return
        self.store.delete_loadout(self.selected_loadout_id)
        self._new_loadout_draft()
        self.status_text.set("Deleted loadout")
        self._refresh_all()


    def _load_selected_loadout(self, *, explicit: bool = False) -> None:
        if self.__dict__.get("_loadout_drag") is not None and not explicit:
            return
        selection = self.loadouts_tree.selection()
        if not selection:
            return
        row = selection[0]
        current = "new" if self.selected_loadout_id is None else str(self.selected_loadout_id)
        # Queued list refreshes must not discard or activate a draft. An
        # explicit click/Return can activate a clean, saved-but-inactive row.
        if row == current:
            if not explicit:
                return
            if row != "new":
                if self._loadout_form_state() != self._loadout_clean_state:
                    return
                active = self.store.get_active_loadout()
                if active and active.id == self.selected_loadout_id:
                    return
        if not self._confirm_loadout_change():
            self._select_loadout_row()
            return
        if row == "new":
            self._new_loadout_draft()
        else:
            self._activate_saved_loadout(int(row), confirm=False)
            return
        self._refresh_loadouts()

    def _activate_saved_loadout(self, loadout_id: int, *, confirm: bool = True) -> bool:
        """Shared list/HUD switch: only subsequent events use the new equipment."""
        if self.__dict__.get("_resume_pending") is not None:
            self.status_text.set("Restoring session; choose a setup when restoration finishes.")
            return False
        if confirm and not self._confirm_loadout_change():
            return False
        # Saving from the discard prompt may update this very target.
        loadout = self.store.get_loadout(loadout_id)
        if loadout is None:
            return False
        self._populate_loadout_form(loadout)
        if not loadout.active:
            self.store.set_active_loadout(loadout.id)
        if self.running and self.session_id:
            self.store.update_session_loadout(self.session_id, with_repair_estimates(self.catalog, loadout))
        self.status_text.set(f"Active setup: {loadout.name}")
        self._refresh_loadouts()
        streamer = self.__dict__.get("streamer_window")
        if self.running and self.session_id:
            self._refresh_setup_views()
        elif streamer and streamer.winfo_exists():
            streamer.update_from_session(None)
        return True


    def _populate_loadout_form(self, loadout: LoadoutRecord) -> None:
        self.selected_loadout_id = loadout.id
        self.loadout_name.set(loadout.name)
        self.loadout_weapon.set(loadout.weapon)
        self.loadout_amp.set(loadout.amp or "None")
        self.loadout_scope.set(loadout.scope or "None")
        self.loadout_sight_1.set(loadout.sight_1 or "None")
        self.loadout_sight_2.set(loadout.sight_2 or "None")
        self.damage_enhancers.set(str(loadout.damage_enhancers))
        self.accuracy_enhancers.set(str(loadout.accuracy_enhancers))
        self.economy_enhancers.set(str(loadout.economy_enhancers))
        self._loadout_clean_state = self._loadout_form_state()
        self._refresh_loadout_preview()

    def _refresh_loadouts(self) -> None:
        self.loadouts_tree.delete(*self.loadouts_tree.get_children())
        self.loadouts_tree.insert("", "end", iid="new", tags=("arrival",), values=("", "New setup", "", "", "", ""))
        active = self.store.get_active_loadout()
        if active:
            attachments = " • ".join(
                part for part in (
                    f"Amp: {active.amp}" if active.amp else "Amp: none",
                    f"Scope: {active.scope}" if active.scope else "Scope: none",
                    f"Sights: {active.sight_1 or 'none'} / {active.sight_2 or 'none'}",
                )
            )
            self.active_loadout_title.set(f"Hunt · {active.name}")
            self.active_loadout_details.set(f"{active.weapon} • {attachments} • {self._money(active.cost_per_shot, decimals=5)}/shot")
            self.active_loadout_text.set(f"Setup: {active.name} · {self._money(active.cost_per_shot, decimals=5)}/shot")
        else:
            self.active_loadout_title.set("No active setup")
            self.active_loadout_details.set("Activate a hunting setup before starting.")
            self.active_loadout_text.set("No active setup — configure one before tracking for accurate costs")
        loadouts = self.store.list_loadouts()
        self._refresh_setup_rail(loadouts)
        for index, loadout in enumerate(loadouts):
            self.loadouts_tree.insert(
                "",
                "end",
                iid=str(loadout.id),
                tags=(f"hud_slot_{index}" if index < 4 else "even" if index % 2 == 0 else "odd",),
                values=(
                    ("✓ " if loadout.active else "") + (str(index + 1) if index < 4 else ""),
                    loadout.name,
                    loadout.weapon,
                    loadout.amp or "—",
                    f"D{loadout.damage_enhancers}/A{loadout.accuracy_enhancers}/E{loadout.economy_enhancers}",
                    f"{loadout.cost_per_shot:.5f}",
                ),
            )
        self._select_loadout_row()
        streamer = self.__dict__.get("streamer_window")
        if streamer and streamer.winfo_exists():
            streamer.refresh_setup_slots(loadouts)

    def _browse_log(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Entropia chat.log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.chat_path.set(path)

    def _open_streamer_window(self) -> None:
        """Toggle the streamer overlay, using its normal close cleanup."""
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.destroy()
            return
        self.streamer_window = StreamerWindow(self)
        if self.__dict__.get("_resume_pending") is not None:
            self.streamer_window.set_restoring()
            return
        session = self.store.get_current_session()
        self._prime_resume_chart(session)
        self.streamer_window.update_from_session(session)

    def start(self) -> None:
        if self.running:
            return
        path = Path(self.chat_path.get().strip().strip('"'))
        if not path.exists():
            messagebox.showerror("PED Hunter", f"Chat log not found:\n{path}")
            self.status_text.set(f"Chat log not found: {path}")
            return

        active_loadout = self.store.get_active_loadout()
        if not active_loadout:
            messagebox.showerror(
                "PED Hunter",
                "Activate a setup before starting. The active setup determines how PED Hunter interprets the log.",
            )
            self.status_text.set("No active setup — activate one before starting")
            return

        active_loadout = with_repair_estimates(self.catalog, active_loadout)
        self.current_log_path = path
        self._last_ingested_log_line = None
        self._prepared_resume = None
        self._full_refresh_pending = False
        self.store.end_other_active_sessions()
        self.session_id = self.store.start_session("hunt", active_loadout)
        self.last_size = path.stat().st_size
        self.running = True
        self.start_button.configure(state="disabled")
        self.resume_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_text.set(f"Tracking {path.name}; new lines will appear in Events")
        self._refresh_all()
        self._start_log_worker()

    def resume_selected_session(self) -> None:
        if self.running:
            messagebox.showinfo("PED Hunter", "Stop the active session before resuming another one.")
            return
        session_id = self._resume_session_id()
        if not session_id:
            messagebox.showinfo("PED Hunter", "No saved sessions are available to resume yet.")
            return
        path = Path(self.chat_path.get().strip().strip('"'))
        if not path.exists():
            messagebox.showerror("PED Hunter", f"Chat log not found:\n{path}")
            self.status_text.set(f"Chat log not found: {path}")
            return
        session = self.store.get_session_resume_state(session_id)
        if not session:
            messagebox.showerror("PED Hunter", "That session could not be found in local history.")
            self._refresh_all()
            return
        try:
            active = self.store.get_active_loadout()
            handoff = read_resume_handoff(
                self.store.db_path, session_id, path, asdict(active) if active else None,
            )
            if handoff is not None:
                if session.ended_at is not None:
                    raise ValueError("A cutover handoff cannot reopen an ended session")
                claim_resume_handoff(self.store.db_path, handoff)
        except (OSError, ValueError) as exc:
            self.status_text.set(f"Resume requires reconciliation: {exc}")
            messagebox.showerror("PED Hunter", self.status_text.get())
            return
        if session.ended_at is not None:
            if not messagebox.askyesno(
                "Resume ended session?",
                "PED Hunter will reopen this session and append only new chat.log events from this point forward.",
            ):
                return
            self.store.resume_session(session_id)
        if handoff is None:
            restored_loadout = self._restore_session_loadout(session)
            if restored_loadout and restored_loadout.id is not None:
                self.store.set_active_loadout(restored_loadout.id)

        self.current_log_path = path
        self._last_ingested_log_line = handoff["last_line"] if handoff else None
        self._resume_handoff = handoff
        self._full_refresh_pending = False
        self.store.end_other_active_sessions(session_id)
        self.session_id = session_id
        self.last_size = int(handoff["offset"]) if handoff else path.stat().st_size
        self.running = True
        self.start_button.configure(state="disabled")
        self.resume_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._sync_run_bar()
        self.status_text.set(f"Restoring {session_id}; new log lines will be read after history is ready")
        self._begin_resume_refresh(session_id)

    def _begin_resume_refresh(self, session_id: str) -> None:
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        self._resume_pending = result_queue
        store = self.store

        def prepare() -> None:
            try:
                result_queue.put(_load_resume_view(store, session_id))
            except Exception as exc:
                result_queue.put(exc)

        threading.Thread(target=prepare, daemon=True, name="ped-hunter-resume-view").start()
        self.after(20, lambda: self._poll_resume_refresh(session_id, result_queue))

    def _poll_resume_refresh(self, session_id: str, result_queue: queue.Queue) -> None:
        if self._resume_pending is not result_queue or not self.running or self.session_id != session_id:
            return  # End run/new context invalidated this result; never restart its reader.
        try:
            view = result_queue.get_nowait()
        except queue.Empty:
            self.after(20, lambda: self._poll_resume_refresh(session_id, result_queue))
            return
        if isinstance(view, Exception):
            self._resume_refresh_failed(view)
            return
        try:
            if view.revision != _resume_revision(self.store):
                self._begin_resume_refresh(session_id)
                return  # A real setup/session/input change invalidated the read.
            self._prepared_resume = view
            self._activity_cache = Snapshot(
                session_id, view.session.loot_value, view.session.events,
                {gain.skill: gain.procs for gain in view.skills},
                accounting_revision=view.session.accounting_revision,
            )
            self._refresh_metrics(view.session, view.sessions)
            self._refresh_lifetime_totals(view.totals)
            self._refresh_sessions(view.sessions[:20])
            self._refresh_events(session_id)
            self._refresh_skills(session_id, view.skills)
            self._refresh_loot_chart(session_id)
            self._refresh_wtb()
            self._refresh_loadouts()
            if self.streamer_window and self.streamer_window.winfo_exists():
                self._prime_resume_chart(view.session)
                self.streamer_window.update_from_session(view.session)
        except Exception as exc:
            logging.exception("Resume view restoration failed")
            self._resume_refresh_failed(exc)
            return
        self._resume_pending = None
        self.status_text.set(f"Resumed {session_id}; tracking new lines from {self.current_log_path.name}")
        self._start_log_worker()

    def _resume_refresh_failed(self, error: Exception) -> None:
        self._resume_pending = None
        self._prepared_resume = None
        self.running = False
        self.start_button.configure(state="disabled" if self.__dict__.get("_resume_handoff") else "normal")
        self.resume_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._sync_run_bar()
        self.status_text.set(f"Resume restoration failed; tracking has not started: {error}")
        messagebox.showerror("PED Hunter", self.status_text.get())

    def _prime_resume_chart(self, session: SessionSummary | None) -> None:
        view = self.__dict__.get("_prepared_resume")
        if (view is None or view.builder is None or session is None
                or (view.session.session_id, view.session.events) != (session.session_id, session.events)):
            return
        overlay = self.streamer_window
        overlay._trend_builder = view.builder
        overlay._raw_kill_trend_points = view.points
        overlay._kill_trend_key = (session.session_id, session.events, session.accounting_revision)
        overlay._hunt_attribution = HuntAttribution(self.store)
        self._prepared_resume = None

    def _selected_session_id(self) -> str | None:
        selection = self.sessions_tree.selection()
        return selection[0] if selection else None

    def _resume_session_id(self) -> str | None:
        """Return the explicitly selected session, or default to the preferred recent session."""
        selected = self._selected_session_id()
        if selected:
            return selected
        preferred = self._preferred_session()
        if preferred:
            return preferred.session_id
        children = self.sessions_tree.get_children()
        if children:
            return children[0]
        sessions = self.store.list_recent_sessions(1)
        return sessions[0].session_id if sessions else None

    def _restore_default_context(self) -> None:
        if has_resume_handoff(self.store.db_path):
            return  # Preserve the captured active cost setup until validated Resume.
        if self.store.get_active_loadout() is not None:
            return  # Preserve an explicit next-hunt choice; Resume restores its own snapshot.
        preferred = self._preferred_session()
        if not preferred:
            return
        restored_loadout = self._restore_session_loadout(preferred)
        if restored_loadout and restored_loadout.id is not None:
            self.store.set_active_loadout(restored_loadout.id)

    def _restore_session_loadout(self, session: SessionSummary | SessionResumeState) -> LoadoutRecord | None:
        snapshot = session.loadout_snapshot or {}
        if not isinstance(snapshot, dict) or not snapshot:
            return None
        loadout: LoadoutRecord | None = None
        snapshot_id = snapshot.get('id')
        try:
            if snapshot_id is not None:
                loadout = self.store.get_loadout(int(snapshot_id))
        except (TypeError, ValueError):
            loadout = None
        if loadout is None:
            snapshot_name = str(snapshot.get('name') or '').strip()
            if snapshot_name:
                loadout = next((item for item in self.store.list_loadouts() if item.name == snapshot_name), None)
        if loadout is None:
            try:
                loadout = LoadoutRecord(
                    id=int(snapshot_id) if snapshot_id is not None else None,
                    name=str(snapshot.get('name') or '').strip(),
                    weapon=str(snapshot.get('weapon') or '').strip(),
                    amp=str(snapshot.get('amp') or ''),
                    scope=str(snapshot.get('scope') or ''),
                    sight_1=str(snapshot.get('sight_1') or ''),
                    sight_2=str(snapshot.get('sight_2') or ''),
                    damage_enhancers=int(snapshot.get('damage_enhancers') or 0),
                    accuracy_enhancers=int(snapshot.get('accuracy_enhancers') or 0),
                    economy_enhancers=int(snapshot.get('economy_enhancers') or 0),
                    ammo_burn=int(snapshot.get('ammo_burn') or 0),
                    decay=float(snapshot.get('decay') or 0.0),
                    cost_per_shot=float(snapshot.get('cost_per_shot') or 0.0),
                    active=False,
                    repair_shots=int(snapshot.get('repair_shots') or 0),
                    repair_decay_per_shot=float(snapshot.get('repair_decay_per_shot') or 0.0),
                    repair_budget=float(snapshot.get('repair_budget') or 0.0),
                    repair_budget_known=bool(snapshot.get('repair_budget_known', False)),
                    repair_items=list(snapshot.get('repair_items') or []),
                )
            except (TypeError, ValueError):
                return None
        return with_repair_estimates(self.catalog, loadout)

    def _on_session_selected(self) -> None:
        session_id = self._selected_session_id()
        if not session_id:
            self.selected_session_text.set("Select a run to resume it.")
            return
        session = self.__dict__.get("_session_summaries", {}).get(session_id) or self.store.get_session(session_id)
        if not session:
            self.selected_session_text.set("Selected run is no longer available.")
            return
        setup = session.loadout_name or session.activity.title()
        self.selected_session_text.set(
            f"{setup} · {_short_time(session.started_at)} · {self._run_status(session)} · "
            f"net {self._money(session.net_value, signed=True)} · return {_return_pct(session):.2f}% · "
            f"{session.events:,} events"
        )

    def _run_status(self, session: SessionSummary) -> str:
        """tracking (being read now), paused (not ended; can be resumed) or ended."""
        if self.__dict__.get("running") and session.session_id == self._session_id_value():
            return "tracking"
        return "paused" if session.ended_at is None else "ended"

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_log_worker()
        self._resume_pending = None
        self._prepared_resume = None
        if self.session_id:
            self.store.end_session(self.session_id)
        self.running = False
        self.session_id = None
        self.current_log_path = None
        self._full_refresh_pending = False
        self.start_button.configure(state="normal")
        self.resume_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_text.set("Run stopped")
        self._refresh_all()

    def _start_log_worker(self) -> None:
        self._stop_log_worker()
        if not self.running or self.session_id is None or self.current_log_path is None:
            return
        self._log_queue = queue.Queue()
        stop_event = threading.Event()
        self._log_worker_stop = stop_event
        self._log_drain_pending = False
        worker = threading.Thread(
            target=self._log_worker_loop,
            args=(self.current_log_path, self.session_id, self.last_size, stop_event,
                  self._last_ingested_log_line, getattr(self, "_resume_handoff", None)),
            daemon=True,
            name="ped-hunter-log-worker",
        )

        self._log_worker = worker
        worker.start()
        self._schedule_log_drain()

    def _stop_log_worker(self) -> None:
        worker = self._log_worker
        self._log_worker_stop.set()
        if worker and worker.is_alive() and threading.current_thread() is not worker:
            worker.join(timeout=1.0)
        self._log_worker = None
        self._log_drain_pending = False

    def _schedule_log_drain(self) -> None:
        if self._log_drain_pending:
            return
        self._log_drain_pending = True
        # Leave the event loop at least twice a refresh's own duration for input
        # and painting; saved events simply wait in the queue count until then.
        cost = self.__dict__.get("_live_refresh_ms", 0.0)
        self.after(max(100, min(1000, round(3 * cost))), self._drain_log_queue)

    def _drain_log_queue(self) -> None:
        self._log_drain_pending = False
        parsed_total = 0
        notice = None
        try:
            while True:
                try:
                    kind, payload = self._log_queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "parsed":
                    parsed_total += int(payload)
                elif kind in {"error", "status"}:
                    notice = str(payload)
            if parsed_total:
                self.status_text.set(f"Parsed {parsed_total} new event{'s' if parsed_total != 1 else ''}")
                started = time.perf_counter()
                try:
                    self._refresh_live_views()
                    self._live_refresh_ms = (time.perf_counter() - started) * 1000
                except Exception as exc:
                    # Ingestion has already committed. A broken/closed drawing
                    # widget must not kill the only callback draining the queue.
                    logging.exception("Live display refresh failed; ingestion continues")
                    notice = f"Events saved; display refresh failed: {exc}"
                finally:
                    self._schedule_full_refresh()
            if notice:
                self.status_text.set(notice)
        finally:
            worker = self._log_worker
            if self.running and worker and worker.is_alive():
                self._schedule_log_drain()

    def _log_worker_loop(self, path: Path, session_id: str, start_size: int, stop_event: threading.Event,
                         last_ingested_log_line: str | None = None, handoff: dict | None = None) -> None:
        try:
            tail = LogTail(path, start_size, handoff=handoff)
        except (OSError, ValueError) as exc:
            self._log_queue.put(("error", f"Resume handoff requires reconciliation: {exc}"))
            return
        last_activity = time.monotonic()
        last_notice = None
        while not stop_event.is_set():
            try:
                previous_offset = tail.offset
                lines = tail.read_lines()
                now = time.monotonic()
                if tail.offset != previous_offset or lines:
                    last_activity = now
                    if last_notice is not None:
                        self._log_queue.put(("status", f"Reading new lines from {path.name}"))
                    last_notice = None
                if lines:
                    parsed, last_ingested_log_line = self._process_lines(
                        lines, session_id, last_ingested_log_line, handoff=handoff,
                    )
                    if parsed:
                        self._log_queue.put(("parsed", parsed))
                if handoff is not None and tail.acknowledge_handoff():
                    completed, handoff = handoff, None
                    try:
                        complete_resume_handoff(self.store.db_path, completed)
                    except (OSError, ValueError) as exc:
                        # Tracking continues; the retained claim keeps blocking
                        # the next Resume until an operator reconciles it.
                        self._log_queue.put(("error", f"Resume handoff receipt not retired: {exc}"))
                    else:
                        self._log_queue.put(("status", f"Resume handoff complete; reading new lines from {path.name}"))
                if now - last_activity >= LOG_IDLE_WARNING_SECONDS:
                    notice = (f"No changes in {path.name} for 30s — if playing, check Entropia's chat logging and the selected file")
                    if notice != last_notice:
                        self._log_queue.put(("status", notice))
                        last_notice = notice
            except Exception as exc:
                if handoff is not None:
                    self._log_queue.put(("error", f"Resume handoff incomplete; retain receipt and reconcile: {exc}"))
                    return
                notice = f"Tracking error: {exc}"
                if notice != last_notice:
                    self._log_queue.put(("error", notice))
                    last_notice = notice
            if stop_event.wait(POLL_MS / 1000.0):
                break

    def _process_lines(
        self,
        lines: list[str],
        session_id: str,
        last_ingested_log_line: str | None,
        *,
        handoff: dict | None = None,
    ) -> tuple[int, str | None]:
        with _held(self.store):
            return PedHunterApp._process_held_lines(self, lines, session_id, last_ingested_log_line, handoff=handoff)

    def _process_held_lines(
        self,
        lines: list[str],
        session_id: str,
        last_ingested_log_line: str | None,
        *,
        handoff: dict | None = None,
    ) -> tuple[int, str | None]:
        parsed = 0
        session_state = self.store.get_session_resume_state(session_id)
        if session_state is None or session_state.ended_at is not None:
            # A stale worker from a stopped/older GUI must never keep appending
            # events to an ended session.
            return 0, last_ingested_log_line
        if handoff is not None:
            saved_setup = handoff["cost_setup"]
            active_loadout = LoadoutRecord(**saved_setup) if saved_setup is not None else None
        else:
            active_loadout = self.store.get_active_loadout()
        if active_loadout:
            active_loadout = with_repair_estimates(self.catalog, active_loadout)
        elif handoff is None:
            active_loadout = self._restore_session_loadout(session_state)
        # Old cutover receipts preserve weapon context, not healing context.
        # Do not assign today's selection to messages from that earlier gap.
        healing_name = self.store.get_selected_healing_tool() if handoff is None else None
        healing_tool = self.catalog.healing_tools.get(healing_name) if healing_name else None
        hot_name = self.store.get_selected_hot_tool() if handoff is None else None
        hot_tool = self.catalog.healing_tools.get(hot_name) if hot_name else None
        for raw in lines:
            if _is_duplicate_log_line(last_ingested_log_line, raw):
                continue
            normalized_raw = raw.strip()
            event = parse_line(raw)
            if not event:
                continue
            estimate_healing_cost(event, healing_name, healing_tool, hot_name, hot_tool)
            if handoff is not None and "healing_selection" in event.payload:
                event.payload["healing_selection"]["enabled"] = False
            if event.kind == "craft" and event.payload.get("result") in {"success", "fail"}:
                allocation = self.store.allocate_manufacturing_attempt(session_id, str(event.payload.get("item") or ""))
                if allocation:
                    event.payload.update(allocation)
            elif event.kind == "loot":
                self.store.add_manufacturing_output(session_id, event.to_row(), str(event.payload.get("item_name") or ""))
                if normalized_raw:
                    last_ingested_log_line = normalized_raw
                parsed += 1
                continue
            if active_loadout and _event_consumes_shot(event):
                ammo_cost = active_loadout.ammo_burn / 10_000.0
                event.payload["shot_cost"] = active_loadout.cost_per_shot
                event.payload["ammo_cost"] = ammo_cost
                event.payload["repair_decay"] = active_loadout.repair_decay_per_shot
                event.payload["loadout"] = active_loadout.name
            if event.kind == "repair":
                if active_loadout:
                    estimated_cost = self.store.estimate_repair_cost_since_last_repair(
                        session_id,
                        active_loadout.name,
                        active_loadout.repair_decay_per_shot or active_loadout.decay,
                    )
                    event.payload.update(
                        {
                            "estimated_cost": estimated_cost,
                            "loadout": active_loadout.name,
                            "estimation": "repair_decay_since_last_repair",
                            "resets_durability": True,
                        }
                    )
                else:
                    event.payload.update({"estimated_cost": 0.0, "estimation": "missing_active_loadout", "resets_durability": True})
            self.store.add_event(session_id, event.to_row())
            if normalized_raw:
                last_ingested_log_line = normalized_raw

            parsed += 1

        return parsed, last_ingested_log_line

    def _refresh_crafting_preview(self) -> None:
        if not self.crafting_blueprint.get().strip():
            self.crafting_preview.set("Pick a blueprint to see its material cost.")
            return
        try:
            attempts = _positive_int(self.crafting_attempts.get())
            per_attempt, _materials = calculate_blueprint_material_cost(self.catalog, self.crafting_blueprint.get())
        except ValueError as exc:
            self.crafting_preview.set(f"Not ready to add: {exc}")
            return
        total = per_attempt * attempts
        mode = self.currency_mode.get()
        self.crafting_preview.set(
            f"Material TT: {format_chart_money(per_attempt, mode)}/attempt • "
            f"{attempts:,} attempts = {format_chart_money(total, mode)} input"
        )

    def _add_crafting_cost_to_session(self) -> None:
        current = self.store.get_current_session()
        session_id = self.session_id or (current.session_id if current else None)
        if not session_id:
            messagebox.showinfo("PED Hunter", "Start or resume a session before adding manufacturing material cost.")
            return

        try:
            attempts = _positive_int(self.crafting_attempts.get())
            per_attempt, materials = calculate_blueprint_material_cost(self.catalog, self.crafting_blueprint.get())
        except ValueError as exc:
            messagebox.showerror("PED Hunter", str(exc))
            self._refresh_crafting_preview()
            return
        total = per_attempt * attempts
        blueprint = self.catalog.find_blueprint(self.crafting_blueprint.get())
        blueprint_name = blueprint.name if blueprint else self.crafting_blueprint.get().strip()
        self.store.add_event(
            session_id,
            {
                "kind": "manufacturing_offset",
                "raw_message": f"Manufacturing cost: {attempts} x {blueprint_name} = {total:.2f} PED",
                "payload": {
                    "offset_id": uuid.uuid4().hex,
                    "blueprint": blueprint_name,
                    "attempts": attempts,
                    "attempts_total": attempts,
                    "attempts_remaining": attempts,
                    "cost_per_attempt": per_attempt,
                    "total_cost": total,
                    "materials": [
                        {"name": name, "quantity": quantity, "tt_value": tt_value, "total": line_total}
                        for name, quantity, tt_value, line_total in materials
                    ],
                },
            },
        )
        self.status_text.set(f"Added {self._money(total)} manufacturing cost for {attempts:,} attempt{'s' if attempts != 1 else ''}")
        self._refresh_all()

    def _schedule_full_refresh(self) -> None:
        if self._full_refresh_pending:
            return
        self._full_refresh_pending = True
        # Secondary tables must leave time for Windows to paint while a game
        # is consuming CPU/GPU time. Live totals have their own faster path.
        self.after(1500, self._run_full_refresh)

    def _run_full_refresh(self) -> None:
        self._full_refresh_pending = False
        if not self.running and self.session_id is None:
            return
        with _held(self.__dict__.get("store")):
            self._refresh_visible_tab()

    def _refresh_visible_tab(self) -> None:
        """Keep hidden tables, lifetime queries and catalog work off live ticks."""
        selected = self.dashboard_tab.master.select()
        session_id = self._session_id_value()
        if selected == str(self.dashboard_tab):
            self._refresh_sessions(self.store.list_recent_sessions(20))
        elif selected == str(self.statistics_tab):
            self._refresh_lifetime_totals()
        elif selected == str(self.events_tab):
            self._refresh_events(session_id)
            self._refresh_loot_chart(session_id)
        elif selected == str(self.skills_tab):
            self._refresh_skills(session_id)
        elif selected == str(self.wtb_tab):
            self._refresh_wtb()

    def _refresh_live_views(self) -> None:
        with _held(self.__dict__.get("store")):
            session_id = self._session_id_value()
            display = self.store.get_session(session_id) if session_id else None
            self._refresh_metrics(display, [])
            if self.streamer_window and self.streamer_window.winfo_exists():
                self.streamer_window.update_from_session(display)

    def _refresh_setup_views(self) -> None:
        """Setup changes do not invalidate the last displayed financial totals."""
        session_id = self._session_id_value()
        display = self.__dict__.get("_session_summaries", {}).get(session_id)
        state = self.store.get_session_resume_state(session_id) if display else None
        if state is None:
            self._refresh_live_views()
            return
        snapshot = state.loadout_snapshot
        display = replace(display, ended_at=state.ended_at,
                          loadout_name=snapshot.get("name") if snapshot else None,
                          loadout_snapshot=snapshot)
        self._refresh_metrics(display, [])
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.update_from_session(display)

    def _refresh_all(self) -> None:
        with _held(self.__dict__.get("store")):
            self._refresh_everything()

    def _refresh_everything(self) -> None:
        current = self.store.get_current_session()
        sessions = self.store.list_recent_sessions(20)
        display = self._display_session(current, sessions)
        self._refresh_metrics(display, sessions)

        self._refresh_lifetime_totals()

        self._refresh_sessions(sessions)

        self._refresh_events(display.session_id if display else None)
        self._refresh_skills(display.session_id if display else None)
        self._refresh_loot_chart(display.session_id if display else None)
        self._refresh_wtb()
        self._refresh_loadouts()
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.update_from_session(display)

    def _session_id_value(self) -> str | None:
        try:
            return object.__getattribute__(self, "session_id")
        except AttributeError:
            return None

    def _preferred_session(self) -> SessionSummary | None:
        try:
            session_id = object.__getattribute__(self, "session_id")
        except AttributeError:
            session_id = None
        if session_id:
            resumed_or_active = self.store.get_session(session_id)
            if resumed_or_active:
                return resumed_or_active
        if hasattr(self.store, "get_last_contributed_session"):
            last_contributed = self.store.get_last_contributed_session()
            if last_contributed:
                return last_contributed
        if hasattr(self.store, "get_current_session"):
            current = self.store.get_current_session()
            if current:
                return current
        if hasattr(self.store, "list_recent_sessions"):
            sessions = self.store.list_recent_sessions(1)
            return sessions[0] if sessions else None
        return None

    def _display_session(self, current: SessionSummary | None, sessions: list[SessionSummary]) -> SessionSummary | None:
        try:
            session_id = object.__getattribute__(self, "session_id")
        except AttributeError:
            session_id = None
        if session_id:
            resumed_or_active = self.store.get_session(session_id)
            if resumed_or_active:
                return resumed_or_active
        # Saved runs belong in history, not in the live summary on launch.
        # A run becomes the display context only after Start or Resume.
        return None

    def _refresh_metrics(self, session: SessionSummary | None, sessions: list[SessionSummary]) -> None:
        if session is not None:
            self.__dict__.setdefault("_session_summaries", {})[session.session_id] = session
        self.hero_state.set("Tracking" if self.running else "Not tracking")
        shown = session.session_id if session is not None else None
        if shown != self.__dict__.get("_shown_session", ""):
            self._shown_session = shown
            if "chart_panel" in self.__dict__:
                self._layout_body()
        self._sync_run_bar()
        self._refresh_healing_summary(session)
        context = (session.session_id if session else None, self.currency_mode.get(), self.running,
                   session.accounting_revision if session else 0)
        animate = context == self._feedback_context and self.running and not self.reduced_motion.get()
        self._feedback_context = context
        arrival = self.activity_strip.update_snapshot(self._activity_snapshot(session), self.running)
        if self.feedback_sounds.get() and (arrival.loot > 0 or arrival.skills):
            self.cue_player.play("loot" if arrival.loot > 0 else "skill")
        if not session:
            for key in ("net", "return", "loot", "cost"):
                self.animator.cancel(key)
                self.animator.values.pop(key, None)
                self.animator.targets.pop(key, None)
            self.session_text.set("No sessions yet — start a run to begin collecting data")
            self.hero_session.set("No active session — press New run, or resume one from Runs")
            self.hero_net.set("—")
            self.hero_return.set("—")
            self.hero_loot.set(self._money(0))
            self.hero_cost.set(self._money(0))
            self.hero_events.set("0")
            self.repair_radar_text.set("Durability tracking starts with a fresh run at full gun and amp TT")
            self._set_hero_profit_style("neutral")
            self._refresh_hunt_chart(None)
            return

        status = "active" if session.ended_at is None else "ended"
        setup_name = (session.loadout_name or "").partition("\n")[0].strip()
        setup = setup_name or session.activity.title()
        return_pct = _return_pct(session)

        self.session_text.set(f"{session.session_id} • {setup} • {status} • started {session.started_at}")
        self.hero_session.set(f"{setup} · started {_short_time(session.started_at)} · "
                              f"{'tracking' if self.running else 'paused' if session.ended_at is None else 'ended'}")
        self.animator.number("net", session.net_value, lambda value: self.hero_net.set(self._money(value, signed=True)), animate)
        self.animator.number("return", return_pct, lambda value: self.hero_return.set(f"{value:.2f}%"), animate)
        self.animator.number("loot", session.loot_value, lambda value: self.hero_loot.set(self._money(value)), animate)
        self.animator.number("cost", session.hunting_cost, lambda value: self.hero_cost.set(self._money(value)), animate)
        self.hero_events.set(f"{session.events:,}")
        radar = format_repair_radar(session, self.currency_mode.get()).removeprefix("Repair radar: ")
        self.repair_radar_text.set(radar[:1].upper() + radar[1:])
        self._set_hero_profit_style(_profit_state(session.net_value))
        self._refresh_hunt_chart(session)

    def _activity_snapshot(self, session: SessionSummary | None) -> Snapshot:
        if session is None:
            return Snapshot(None)
        cached = self._activity_cache
        if cached and (cached.session_id, cached.events, cached.loot, cached.accounting_revision) == (
                session.session_id, session.events, session.loot_value, session.accounting_revision):
            return cached
        gains = self.store.skill_gains_for_session(session.session_id)
        snapshot = Snapshot(session.session_id, session.loot_value, session.events,
                            {gain.skill: gain.procs for gain in gains}, session.accounting_revision)
        self._activity_cache = snapshot
        return snapshot

    def _refresh_lifetime_totals(self, totals: LifetimeTotals | None = None) -> None:
        if totals is None:
            totals = self.store.lifetime_totals()
        if totals.session_count == 0:
            self.lifetime_summary_text.set("No saved runs yet — your lifetime totals build up as you hunt.")
            self._set_empty_lifetime_totals()
            return
        active_note = " · includes active run" if totals.active_count else ""
        self.lifetime_summary_text.set(
            f"All {totals.session_count:,} saved run{'s' if totals.session_count != 1 else ''}{active_note}"
        )
        self.lifetime_vars["total_cost"].set(self._money(totals.total_cost))
        self.lifetime_vars["total_loot"].set(self._money(totals.total_loot))
        self.lifetime_vars["total_net"].set(self._money(totals.total_net, signed=True))
        self.lifetime_vars["overall_return"].set(f"{totals.overall_return_pct:.2f}%")
        self.lifetime_vars["total_events"].set(f"{totals.total_events:,}")
        self.lifetime_vars["avg_return"].set(f"{totals.avg_return_pct:.2f}%")
        self.lifetime_vars["avg_profit"].set(self._money(totals.avg_profit_per_run, signed=True))
        self.lifetime_vars["best_run"].set(_lifetime_run_label(totals.best_session, self.currency_mode.get()))
        self.lifetime_vars["worst_run"].set(_lifetime_run_label(totals.worst_session, self.currency_mode.get()))

    def _set_empty_lifetime_totals(self) -> None:
        self.lifetime_vars["total_cost"].set(self._money(0))
        self.lifetime_vars["total_loot"].set(self._money(0))
        self.lifetime_vars["total_net"].set(self._money(0, signed=True))
        self.lifetime_vars["overall_return"].set("0.00%")
        self.lifetime_vars["total_events"].set("0")
        self.lifetime_vars["avg_return"].set("0.00%")
        self.lifetime_vars["best_run"].set("—")
        self.lifetime_vars["worst_run"].set("—")
        self.lifetime_vars["avg_profit"].set(self._money(0, signed=True))

    def _set_hero_profit_style(self, state: str) -> None:
        suffix = {"good": "Good", "bad": "Bad"}.get(state, "Neutral")
        if hasattr(self, "hero_net_label"):
            self.hero_net_label.configure(style=f"Hero{suffix}.TLabel")
        if hasattr(self, "hero_return_label"):
            self.hero_return_label.configure(style=f"Readout{suffix}.TLabel")

    def _refresh_sessions(self, sessions: list[SessionSummary]) -> None:
        self._session_summaries = {session.session_id: session for session in sessions}
        previous_selection = self._selected_session_id()
        tree = self.sessions_tree
        tree.delete(*tree.get_children())
        keys = getattr(tree, "sort_keys", {})
        keys.clear()
        for index, session in enumerate(sessions):
            keys[(session.session_id, "started")] = str(session.started_at or "")
            tree.insert(
                "",
                "end",
                iid=session.session_id,
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    _short_time(session.started_at),
                    session.loadout_name or session.activity.title(),
                    f"{_return_pct(session):.2f}%",
                    self._money(session.loot_value),
                    self._money(session.hunting_cost),
                    self._money(session.net_value, signed=True),
                    f"{session.events:,}",
                    "  " + self._run_status(session),
                ),
            )
        if hasattr(tree, "sort_state"):
            apply_sort(tree)

        if not sessions:
            self.selected_session_text.set("No saved runs yet.")
            return

        preferred_id = self._session_id_value()
        if preferred_id is None:
            preferred = self._preferred_session()
            preferred_id = preferred.session_id if preferred else None
        session_ids = {session.session_id for session in sessions}
        selected = previous_selection if previous_selection in session_ids else preferred_id if preferred_id in session_ids else sessions[0].session_id
        self.sessions_tree.selection_set(selected)
        self.sessions_tree.focus(selected)
        self._on_session_selected()

    def _refresh_events(self, session_id: str | None) -> None:
        tree = self.events_tree
        kinds = EVENT_FILTERS.get(self.event_filter.get()) if "event_filter" in self.__dict__ else None
        rows = _recent_events(self.store, session_id, limit=80, kinds=kinds) if session_id else []
        ids = {str(row["id"]) for row in rows}
        same = session_id == self._event_context
        selected = tree.selection()
        view = tree.yview()
        tree.delete(*tree.get_children())
        self._event_kinds = {}
        latest_day = str(rows[-1]["timestamp"] or "")[:10] if rows else ""
        for index, row in enumerate(reversed(rows)):  # newest first
            iid = str(row["id"])
            self._event_kinds[iid] = str(row["kind"])
            tag = "even" if index % 2 == 0 else "odd"
            if same and self.running and not self.reduced_motion.get() and iid not in self._event_ids and row["kind"] in {"loot", "skill"}:
                tag = "arrival"
            tree.insert("", "end", iid=iid, tags=(tag,), values=(
                _short_time(row["timestamp"], seconds=True, today=latest_day), _EVENT_TYPES.get(str(row["kind"]), str(row["kind"]).title()),
                _summarize_event(row, self.currency_mode.get())))
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
        if same and view and view[0] > 0.01:
            tree.yview_moveto(view[0])
        self._event_context, self._event_ids = session_id, ids
        self._fade_tree_arrivals(tree, "events")
        if "exclude_loot_button" in self.__dict__:
            self._event_selection_changed()
            left_out = 0
            history = getattr(self.store, "loot_event_history", None)
            if session_id and history is not None:
                left_out = len(history(session_id, excluded=True, limit=100))
            self.corrections_text.set(f"Loot corrections ({left_out} left out)…" if left_out else "Loot corrections…")

    def _fade_tree_arrivals(self, tree: ttk.Treeview, key: str) -> None:
        self.animator.cancel(key)
        if not any("arrival" in tree.item(iid, "tags") for iid in tree.get_children()):
            return

        def fade() -> None:
            self.animator.jobs.pop(key, None)
            for index, iid in enumerate(tree.get_children()):
                tree.item(iid, tags=("even" if index % 2 == 0 else "odd",))
        self.animator.jobs[key] = self.after(1200, fade)

    def _refresh_skills(self, session_id: str | None, gains: list[SkillGainSummary] | None = None) -> None:
        self.skills_tree.delete(*self.skills_tree.get_children())
        if not session_id:
            self._skill_context = None
            self._skill_counts = {}
            self.skill_total_text.set("0.0000 XP")
            self.skill_proc_text.set("0 skill gains")
            self.skill_summary_text.set("No run shown — start or resume a run to see its skill gains.")
            apply_sort(self.skills_tree)
            return

        if gains is None:
            gains = self.store.skill_gains_for_session(session_id)
        total_xp = sum(gain.xp for gain in gains)
        total_procs = sum(gain.procs for gain in gains)
        self.skill_total_text.set(f"{total_xp:.4f} XP")
        self.skill_proc_text.set(f"{total_procs} skill gain{'s' if total_procs != 1 else ''}")
        if gains:
            self.skill_summary_text.set(
                f"{len(gains)} skill{'s' if len(gains) != 1 else ''} · {total_procs:,} procs · {total_xp:.4f} XP this run"
                " · click a column heading to sort"
            )
        else:
            self.skill_summary_text.set("No skill gains recorded for this run yet.")
        same = session_id == self._skill_context
        previous = self._skill_counts
        self._skill_context = session_id
        self._skill_counts = {gain.skill: gain.procs for gain in gains}
        for index, gain in enumerate(gains):
            fresh = same and self.running and not self.reduced_motion.get() and gain.procs > previous.get(gain.skill, 0)
            self.skills_tree.insert(
                "",
                "end",
                tags=("arrival" if fresh else "even" if index % 2 == 0 else "odd",),
                values=(gain.skill, f"{gain.xp:.4f}", f"{gain.procs:,}", f"{gain.proc_pct:.1f}%"),
            )
        apply_sort(self.skills_tree)
        self._fade_tree_arrivals(self.skills_tree, "skills")

    def _refresh_loot_chart(self, session_id: str | None) -> None:
        points = _loot_event_points(self.store, session_id, limit=160) if session_id else []
        previous = getattr(self, "loot_chart_points", [])
        animate = self._chart_context == session_id and self.running and not self.reduced_motion.get() and previous and points != previous
        if self._chart_context != session_id:
            self._loot_chart_max = 0.0
        self._chart_context = session_id
        self.loot_chart_points = points
        self.animator.cancel("loot_chart")
        self._loot_chart_phase = 1.0
        if animate:
            self.animator.tween("loot_chart", self._loot_chart_frame, 400)
        else:
            self._draw_loot_chart()

    def _loot_chart_frame(self, phase: float) -> None:
        self._loot_chart_phase = phase
        self._draw_loot_chart()

    def _refresh_wtb(self) -> None:
        listings = _wtb_listings(self.store, limit=240)
        tree = self.wtb_tree
        selected = tree.selection()
        tree.delete(*tree.get_children())
        tree.sort_keys.clear()
        self._wtb_details = {}
        if not listings:
            self.wtb_summary_text.set("No buy requests seen yet. They appear here when someone posts a WTB in trade chat "
                                      "while PED Hunter is reading your log.")
            self._wtb_selected()
            return
        self.wtb_summary_text.set(
            f"{len(listings)} buy request{'s' if len(listings) != 1 else ''} seen in trade chat, newest first. "
            "Only the 'want to buy' part of each message is listed.")
        any_qty = any(item.get("quantity") is not None for item in listings)
        any_price = any(item.get("price") for item in listings)
        tree.configure(displaycolumns=("time", "player", "want", *(("qty",) if any_qty else ()),
                                       *(("price",) if any_price else ())))
        self._fit_tree(tree)
        for index, item in enumerate(reversed(listings)):
            qty = item.get("quantity")
            iid = f"wtb{index}"
            tree.sort_keys[(iid, "time")] = str(item.get("timestamp") or "")
            self._wtb_details[iid] = (f"{item.get('speaker') or 'Someone'} in {item.get('channel') or 'trade chat'}: "
                                      f"{item.get('message') or item.get('segment') or ''}")
            tree.insert(
                "",
                "end",
                iid=iid,
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    _short_time(item.get("timestamp")),
                    item.get("speaker") or "",
                    item.get("want") or item.get("segment") or "",
                    "" if qty is None else qty,
                    item.get("price") or "",
                ),
            )
        apply_sort(tree)
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
        self._wtb_selected()

    def _draw_loot_chart(self) -> None:
        canvas = self.loot_chart_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        colors = self.colors
        gap = max(3, round(4 * self.display_scale))
        axis_font = self._ui_font(8)
        bold_font = self._ui_font(8, weight="bold")
        axis_height = axis_font.metrics("linespace")
        canvas.create_rectangle(0, 0, width, height, fill=colors["panel_2"], outline="")
        points = getattr(self, "loot_chart_points", [])
        if not points:
            canvas.create_text(
                width / 2, height / 2,
                text="No loot events yet — start a run and loot events will plot here.",
                fill=colors["muted"], font=self._ui_font(),
                width=max(width - 2 * gap, 1),
            )
            return

        count = len(points)
        total = sum(value for _, value, _ in points)
        caption = canvas.create_text(
            width - gap, height - gap,
            text=f"{count} loot events • {format_money(total, self.currency_mode.get())} total • latest: {format_money(points[-1][1], self.currency_mode.get())} {points[-1][2]}",
            anchor="se", justify="right", width=max(width - 2 * gap, 1),
            fill=colors["muted"], font=axis_font,
        )
        caption_box = canvas.bbox(caption)
        caption_height = caption_box[3] - caption_box[1]
        peak = max(value for _, value, _ in points)
        self._loot_chart_max = max(getattr(self, "_loot_chart_max", 0.0), peak)
        max_value = self._loot_chart_max or 1.0
        tick_values = [0, max_value / 2, max_value]
        tick_labels = [format_money(tick, self.currency_mode.get()) for tick in tick_values]
        pad_left = max(axis_font.measure(label) for label in tick_labels) + 2 * gap
        pad_right = max(round(18 * self.display_scale), gap)
        radius = 4 if count < 60 else 3
        pad_top = bold_font.metrics("linespace") + 2 * gap + axis_height / 2 + radius
        pad_bottom = axis_height + caption_height + 3 * gap
        plot_w = max(width - pad_left - pad_right, 1)
        plot_h = max(height - pad_top - pad_bottom, 1)
        if plot_h < 2 * (axis_height + gap):
            tick_values = [0, max_value]
        for tick in tick_values:
            y = pad_top + plot_h - (tick / max_value * plot_h)
            canvas.create_line(pad_left, y, width - pad_right, y, fill="#1f2a3d")
            canvas.create_text(pad_left - gap, y, text=format_money(tick, self.currency_mode.get()),
                               anchor="e", fill=colors["muted"], font=axis_font)
        canvas.create_text(gap, gap, text=currency_name(self.currency_mode.get()),
                           anchor="nw", fill=colors["muted"], font=bold_font)

        step = plot_w / max(count - 1, 1)
        line_points: list[float] = []
        for index, (_timestamp, value, label) in enumerate(points):
            x = pad_left + (step * index if count > 1 else plot_w / 2)
            phase = getattr(self, "_loot_chart_phase", 1.0) if index == count - 1 else 1.0
            previous_value = points[index - 1][1] if index else value
            drawn_value = previous_value + (value - previous_value) * phase
            if index == count - 1 and count > 1:
                x -= step * (1 - phase)
            y = pad_top + plot_h - (drawn_value / max_value * plot_h)
            line_points.extend([x, y])
            canvas.create_line(x, pad_top + plot_h, x, y, fill="#075985", width=2)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                               fill=colors["accent"], outline="#e0f2fe")
            if count <= 28:
                canvas.create_text(x, pad_top + plot_h + gap, text=str(index + 1),
                                   anchor="n", fill=colors["muted"], font=axis_font)
            if value == max_value:
                anchor = "sw" if index == 0 else "se" if index == count - 1 else "s"
                canvas.create_text(x, y - gap, text=format_money(value, self.currency_mode.get()),
                                   anchor=anchor, fill=colors["text"], font=bold_font)
        if len(line_points) >= 4:
            canvas.create_line(*line_points, fill="#7dd3fc", width=2, smooth=False)

    def _search_catalog(self) -> None:
        if "catalog_tree" not in self.__dict__:
            self._build_catalog_tab()
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        query = self.catalog_query.get().strip()
        matches: list[WeaponRecord] = []
        if query:
            exact = self.catalog.find_weapon(query)
            if exact:
                matches.append(exact)
            words = query.casefold().split()
            for weapon in self.catalog.weapons.values():
                if len(matches) >= 200:
                    break
                if weapon in matches:
                    continue
                names = [weapon.name.casefold(), *(alias.casefold() for alias in weapon.aliases)]
                if any(all(word in name for word in words) for name in names):
                    matches.append(weapon)
        else:
            matches = sorted(self.catalog.weapons.values(), key=lambda weapon: _listing_order(weapon.name))[:200]

        for index, weapon in enumerate(matches):
            self.catalog_tree.insert(
                "",
                "end",
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    weapon.name,
                    weapon.category,
                    weapon.ammo,
                    f"{weapon.decay:.5f}",
                    f"{weapon.cost_per_shot:.5f}",
                    ", ".join(weapon.aliases),
                ),
            )
        apply_sort(self.catalog_tree)


def _resize_cursor(windowing_system: str) -> str:
    """Use Tk's backend, not the host OS; Aqua also supports X cursor names."""
    return "size_nw_se" if windowing_system == "win32" else "bottom_right_corner"


def _streamer_work_area(window: tk.Misc) -> tuple[int, int, int, int]:
    """Use the dashboard's monitor work area, excluding Windows taskbars/docks.

    Native coordinates and Tk coordinates share this process's DPI context.
    Other Tk backends (or a failed native lookup) use the Tk screen bounds.
    """
    fallback = (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    if str(window.tk.call("tk", "windowingsystem")) != "win32":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user32.MonitorFromWindow(window.winfo_id(), 2)  # nearest monitor
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            area = info.rcWork
            if area.right > area.left and area.bottom > area.top:
                return area.left, area.top, area.right, area.bottom
    except (AttributeError, OSError, ValueError):
        pass
    return fallback


def _bottom_right_position(bounds: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return max(left, right - width - STREAMER_EDGE_MARGIN), max(top, bottom - height - STREAMER_EDGE_MARGIN)


class LegacyStreamerWindow(tk.Toplevel):
    """Pre-theme widget HUD, retained for rollback/reference; not selected at runtime."""

    def __init__(self, app: PedHunterApp) -> None:
        super().__init__(app)
        self.withdraw()  # fit and place before the first visible frame
        self.app = app
        self._initial_work_area = _streamer_work_area(app)
        self._initial_position_pending = True
        self._user_positioned = False
        self.animator = Animator(self)
        self._feedback_context = None
        self._chart_phase = 1.0
        self._trail_recorder = TrailRecorder(app.store)
        self.kill_trail_histories = {}
        self._trail_error = False
        self._hunt_attribution = HuntAttribution(app.store)
        self._attribution_error = False
        self.title("PED Hunter Streamer UI · Live feedback")
        self.ui_scale = window_ui_scale(app)
        self._content_scale = self.ui_scale
        self._layout_busy = False
        self._layout_geometry_key = None
        self._resize_capture = None
        self.demo = bool(getattr(app, "preview_mode", False))
        self.configure(bg="#0B1220")
        self.attributes("-topmost", True)
        self.overrideredirect(True)
        self.geometry(f"{int(STREAMER_DEFAULT_WIDTH * self.ui_scale)}x{int(STREAMER_DEFAULT_HEIGHT * self.ui_scale)}")
        self.minsize(int(STREAMER_MIN_WIDTH * self.ui_scale), int(STREAMER_MIN_HEIGHT * self.ui_scale))
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int, int, int, int, str] | None = None
        self.vars = {
            "state": tk.StringVar(value="IDLE"),
            "net_big": tk.StringVar(value="—"),
            "return": tk.StringVar(value="0.00% Return"),
            "loot": tk.StringVar(value="$0.00"),
            "cost": tk.StringVar(value="$0.00"),
            "kills": tk.StringVar(value="0"),
            "input": tk.StringVar(value="Manufacturing input —"),
            "damage": tk.StringVar(value="Damage 0.0"),
            "loadout": tk.StringVar(value="No active loadout"),
            "durability": tk.StringVar(value="Durability —"),
            "kill_trend": tk.StringVar(value="Kill trend will appear after the first loot."),
            "kill_trend_badge": tk.StringVar(value="Waiting for first kill"),
        }
        self.durability_pct = 0.0
        self.durability_color = "#111827"
        self.durability_known = False
        self.durability_label = "Equipment"
        self._fit_after_id: str | None = None
        self._user_resized = False
        self._build()
        for key in self._fixed_labels:
            self.vars[key].trace_add("write", lambda *_args, key=key: self._on_metric_change(key))
        self.vars["loadout"].trace_add("write", lambda *_args: self._update_loadout_text())
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _event: setattr(self, "_drag_origin", None))
        for key in ("Left", "Right", "Up", "Down"):
            self.bind(f"<{key}>", self._keyboard_geometry)
            self.bind(f"<Shift-{key}>", self._keyboard_geometry)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        # Opening must not depend on the event loop becoming idle. Live log
        # traffic (or a busy desktop/game) can delay idle callbacks indefinitely,
        # leaving an otherwise valid overlay withdrawn and invisible.
        self._fit_to_contents()
        self.lift()

    def _build(self) -> None:
        bg, surface, primary, secondary = "#0B1220", "#131E2E", "#F1F5F9", "#B8C5D6"
        self.content_width = int((STREAMER_DEFAULT_WIDTH - 18) * self.ui_scale)
        self._fonts = {}
        self.outer = tk.Frame(self, bg=bg, highlightbackground="#334155", highlightthickness=1,
                              padx=round(8 * self.ui_scale), pady=round(6 * self.ui_scale))
        self.outer.pack(fill="both", expand=True)
        self._fixed_labels, self._fixed_fonts, self._fixed_widths = {}, {}, {}

        def label(parent, *, size=9, bold=False, fg=secondary, **kwargs):
            return tk.Label(parent, bg=parent.cget("bg"), fg=fg,
                            font=self._font(size, bold), **kwargs)

        def fixed_text(parent, key, *, size=9, bold=False, fg=secondary, anchor="w"):
            # Independent fonts can fit exceptionally large values without
            # changing another field's font, row height, or column allocation.
            font = self._font(size, bold).copy()
            widget = tk.Label(parent, textvariable=self.vars[key], width=1, anchor=anchor,
                              justify="left", font=font, bg=parent.cget("bg"), fg=fg)
            self._fixed_labels[key] = widget
            self._fixed_fonts[key] = (font, self._fonts[size, bold][1])
            return widget

        self.header = top = tk.Frame(self.outer, bg=bg)
        top.pack(fill="x")
        top.pack_propagate(False)
        self.brand_label = label(top, text="PED HUNTER", bold=True, fg="#5EEAD4", cursor="fleur", takefocus=True)
        self.brand_label.pack(side="left")
        self.close_button = tk.Button(top, text="×", command=self.destroy, bg=surface, fg=primary,
                                     activebackground="#334155", activeforeground="#FDA4AF",
                                     font=self._font(12), bd=0, padx=5, pady=0, takefocus=True,
                                     highlightthickness=1, highlightcolor="#5EEAD4", cursor="hand2")
        self.close_button.pack(side="right", padx=(8, 0))
        self.close_button.bind("<Return>", lambda _event: self.destroy())
        self.state_label = label(top, textvariable=self.vars["state"], bold=True, fg="#5EEAD4")
        self.state_label.pack(side="right")

        self.hero = tk.Frame(self.outer, bg=bg)
        self.hero.pack(fill="x", pady=(4, 4))
        self.hero.grid_propagate(False)
        self.hero.columnconfigure(0, weight=3, uniform="hero")
        self.hero.columnconfigure(2, weight=2, uniform="hero")
        self.setup_slot_frame = tk.Frame(self.hero, bg=bg)
        self.setup_slot_frame.grid(row=0, column=1, rowspan=2, sticky="nsew")
        self.setup_slot_frame.grid_propagate(False)
        self.setup_buttons = []
        self._setup_slot_ids = [None] * 4
        self._setup_slot_labels = [""] * 4
        self._setup_slot_text_width = round(52 * self._content_scale)
        for index, color in enumerate(HUD_SETUP_COLORS):
            self.setup_slot_frame.columnconfigure(index % 2, weight=1, uniform="setup")
            self.setup_slot_frame.rowconfigure(index // 2, weight=1, uniform="setup")
            button = tk.Button(
                self.setup_slot_frame, width=1, anchor="w", font=self._font(9, True),
                bg=surface, fg=color, activebackground="#334155", activeforeground=color,
                disabledforeground="#69788C", bd=0, highlightthickness=1,
                highlightbackground="#334155", highlightcolor=color, padx=2, pady=0,
                takefocus=True, cursor="hand2", command=lambda index=index: self._activate_setup_slot(index))
            button.grid(row=index // 2, column=index % 2, sticky="nsew", padx=1, pady=1)
            button.bind("<Return>", lambda _event, button=button: (button.invoke(), "break")[1])
            self.setup_buttons.append(button)
        self.refresh_setup_slots(self.app.store.list_loadouts())
        self.net_caption = label(self.hero, text="NET PROFIT", bold=True, width=1, anchor="w")
        self.net_label = fixed_text(self.hero, "net_big", size=24, bold=True, fg=primary)
        self.return_caption = label(self.hero, text="RETURN", bold=True, width=1, anchor="e")
        self.return_label = fixed_text(self.hero, "return", size=16, bold=True, fg=primary, anchor="e")
        self.net_caption.grid(row=0, column=0, sticky="nsew")
        self.net_label.grid(row=1, column=0, sticky="nsew")
        self.return_caption.grid(row=0, column=2, sticky="nsew")
        self.return_label.grid(row=1, column=2, sticky="nsew")

        self.stats = tk.Frame(self.outer, bg=surface, padx=6, pady=3)
        self.stats.pack(fill="x")
        self.stats.grid_propagate(False)
        self.stats.rowconfigure(0, weight=1)
        self.stat_cells = []
        for index, (key, title) in enumerate((("loot", "Loot"), ("cost", "Cost"), ("kills", "Kills*"))):
            self.stats.columnconfigure(index, weight=1, uniform="metric")
            cell = tk.Frame(self.stats, bg=surface)
            cell.pack_propagate(False)
            cell.grid(row=0, column=index, sticky="nsew")
            title_label = label(cell, text=title, bold=True, fg="#FDBA74" if key == "cost" else secondary)
            title_label.pack(anchor="w")
            setattr(self, {"loot": "loot_title", "cost": "cost_title", "kills": "count_title"}[key], title_label)
            fixed_text(cell, key, size=12, bold=True, fg=primary).pack(fill="both", expand=True)
            self.stat_cells.append(cell)
        self.input_slot = tk.Frame(self.outer, bg=bg)
        self.input_slot.pack_propagate(False)
        self.input_label = fixed_text(self.input_slot, "input")

        self.trend = trend = tk.Frame(self.outer, bg=bg)
        trend.pack(fill="both", expand=True, pady=(4, 0))
        trend.pack_propagate(False)
        self.chart_header = chart_header = tk.Frame(trend, bg=bg)
        chart_header.pack(fill="x")
        chart_header.pack_propagate(False)
        self.chart_note = label(chart_header, text=f"* Inferred clusters · {currency_name(self.app.currency_mode.get())}", fg=secondary)
        self.chart_note.pack(side="left")
        legend = tk.Frame(chart_header, bg=bg)
        legend.pack(side="right")
        for text, tint in (("— Cost", "#FDBA74"), ("- - Avg", "#7DD3FC"), ("● Returns", "#86EFAC")):
            label(legend, text=text, fg=tint).pack(side="left", padx=(4, 0))
        self.kill_chart = tk.Canvas(trend, width=1, height=round(54 * self.ui_scale), bg=surface, highlightthickness=0)
        self.kill_chart.pack(fill="both", expand=True, pady=(4, 0))
        self.kill_chart.bind("<Configure>", lambda _event: self._draw_kill_trend_chart())
        self.summary_slot = tk.Frame(trend, bg=bg)
        self.summary_slot.pack(fill="x", pady=(4, 0))
        self.summary_slot.pack_propagate(False)
        fixed_text(self.summary_slot, "kill_trend_badge", anchor="nw").pack(fill="both", expand=True)

        self.durability_section = durability = tk.Frame(self.outer, bg=bg)
        durability.pack(fill="x", pady=(4, 0))
        durability.pack_propagate(False)
        self.durability_text_slot = tk.Frame(durability, bg=bg)
        self.durability_text_slot.pack(fill="x")
        self.durability_text_slot.pack_propagate(False)
        fixed_text(self.durability_text_slot, "durability").pack(fill="both", expand=True)
        self.durability_bar = tk.Canvas(durability, width=1, height=round(6 * self.ui_scale), bg=surface, highlightthickness=0)
        self.durability_bar.pack(fill="x", pady=(4, 0))
        self.durability_bar.bind("<Configure>", lambda _event: self._draw_durability_bar())

        self.footer = bottom = tk.Frame(self.outer, bg=bg)
        bottom.pack(fill="x", pady=(4, 0))
        bottom.pack_propagate(False)
        self.resize_handle = label(bottom, text="◢", fg="#5EEAD4", takefocus=True,
                                  cursor=_resize_cursor(str(self.tk.call("tk", "windowingsystem"))))
        self.resize_handle.pack(side="right", ipadx=4, ipady=2)
        self.loadout_label = label(bottom, text="No active session", anchor="w", width=1, takefocus=True, cursor="hand2")
        self.loadout_label.pack(side="left", fill="both", expand=True)
        self.resize_handle.bind("<ButtonPress-1>", self._start_resize)
        self.resize_handle.bind("<B1-Motion>", self._resize)
        self.resize_handle.bind("<ButtonRelease-1>", self._stop_resize)
        self._loadout_tip = None
        # Chart motion must never cover gameplay with automatic explanations.
        # Current context is available deliberately from the footer instead.
        for event in ("<Button-1>", "<Return>", "<space>"):
            self.loadout_label.bind(event, self._show_context_details)
        for widget in (self.brand_label, self.loadout_label, self.resize_handle):
            widget.bind("<FocusIn>", lambda event: event.widget.configure(highlightthickness=1, highlightbackground="#5EEAD4"), add="+")
            widget.bind("<FocusOut>", lambda event: event.widget.configure(highlightthickness=0), add="+")
        self._pack_spacing = []
        def remember_spacing(parent):
            for widget in parent.winfo_children():
                if widget.winfo_manager() == "pack":
                    info = widget.pack_info()
                    values = {}
                    for key in ("padx", "pady", "ipadx", "ipady"):
                        value = info[key]
                        parts = value if isinstance(value, tuple) else self.tk.splitlist(str(value))
                        values[key] = tuple(int(part) for part in parts)
                    self._pack_spacing.append((widget, values))
                remember_spacing(widget)
        remember_spacing(self.outer)
        self._build_resize_edges()
        self._font(8)  # Chart tick font is part of the template, even before the first kill.
        self.bind("<Configure>", self._on_overlay_configure, add="+")
        self.outer.bind("<Configure>", self._on_overlay_configure, add="+")

    def refresh_setup_slots(self, loadouts: list[LoadoutRecord]) -> None:
        """The first four persisted saved entries, not the active-first order."""
        self._setup_active_name = next((loadout.name for loadout in loadouts if loadout.active), None)
        for index, button in enumerate(self.setup_buttons):
            loadout = loadouts[index] if index < len(loadouts) else None
            self._setup_slot_ids[index] = loadout.id if loadout else None
            self._setup_slot_labels[index] = (
                f"{index + 1} {'✓ ' if loadout.active else ''}{loadout.name}" if loadout else f"{index + 1} Empty")
            button.configure(state="normal" if loadout else "disabled",
                             bg="#253C42" if loadout and loadout.active else "#131E2E",
                             highlightbackground=HUD_SETUP_COLORS[index] if loadout and loadout.active else "#334155",
                             cursor="hand2" if loadout else "")
        self._fit_setup_labels()

    def _fit_setup_labels(self) -> None:
        font = self._font(9, True)
        for button, full in zip(self.setup_buttons, self._setup_slot_labels):
            text = full
            while len(text) > 3 and font.measure(text) > self._setup_slot_text_width:
                text = text[:-2].rstrip("…") + "…"
            button.configure(text=text)

    def _activate_setup_slot(self, index: int) -> None:
        loadout_id = self._setup_slot_ids[index]
        if loadout_id is not None:
            active = self.app.store.get_active_loadout()
            if active is None or active.id != loadout_id:
                self.app._activate_saved_loadout(loadout_id)

    def _show_setup_slot(self, index: int) -> None:
        if self._setup_slot_ids[index] is not None:
            self._show_loadout(text=self._setup_slot_labels[index])

    def _fit_hero_captions(self) -> None:
        budgets = (self._fixed_widths.get("net_big", 0), self._fixed_widths.get("return", 0))
        if min(budgets) <= 0 or self.__dict__.get("_caption_fit_key") == budgets:
            return
        self._caption_fit_key = budgets
        # Share stable DPI fonts. A fixed two-line lane accommodates all modes;
        # first cost/return arrivals must not grow or shrink the typography.
        self.net_caption.configure(wraplength=budgets[0], justify="left")
        self.return_caption.configure(wraplength=budgets[1], justify="right")


    def _font(self, size=9, bold=False):
        import tkinter.font as tkfont
        key = (size, bold)
        if key not in self._fonts:
            pixels = {8: 9, 9: 10, 12: 14, 16: 18, 24: 26}.get(size, size)
            self._fonts[key] = (tkfont.Font(self, family="Segoe UI", size=-round(pixels * self._content_scale),
                                          weight="bold" if bold else "normal"), pixels)
        return self._fonts[key][0]

    def _on_overlay_configure(self, event) -> None:
        if event.widget in (self, self.outer) and not self._layout_busy:
            if not self._initial_position_pending:
                scale = window_ui_scale(self)
                if abs(scale - self.ui_scale) > 0.01:
                    # A monitor change needs fresh pixel fonts even when the
                    # window's dimensions have not changed. Never change Tk's
                    # global scaling (which also affects the dashboard).
                    self.ui_scale = scale
                    self._layout_geometry_key = None
            # Moving the window, or changing text in it, is not a resize.
            size = (self.winfo_width(), self.winfo_height())
            if not self._layout_geometry_key or self._layout_geometry_key[:2] != size:
                self._schedule_fit_to_contents()

    def _on_metric_change(self, key) -> None:
        if not self._layout_busy:
            if key == "kill_trend_badge":
                if self._summary_lines() != getattr(self, "_summary_layout_lines", 2):
                    self._layout_geometry_key = None
                    self._schedule_fit_to_contents()
            else:
                self._fit_fixed_text(key)

    def _summary_lines(self) -> int:
        return max(2, _wrapped_line_count(self.vars["kill_trend_badge"].get(),
                                         self._fixed_fonts["kill_trend_badge"][0],
                                         max(1, self.content_width - 4)))

    def _fit_fixed_text(self, key, sample: str | None = None) -> None:
        if key == "kill_trend_badge":
            return  # Meaningful warnings wrap; never shrink them into unreadable text.
        available = self._fixed_widths.get(key, 0)
        if available <= 0:
            return
        font, _ = self._fixed_fonts[key]
        lines = (sample if sample is not None else self.vars[key].get()).splitlines() or [""]
        size = abs(int(font.cget("size")))
        # Only an actual overflow can shrink this field. Do not grow it again
        # on the next animation frame/value; reset its budget on a real resize.
        while size > 1 and max(font.measure(line) for line in lines) > available:
            size -= 1
            font.configure(size=-size)

    def _set_content_scale(self, factor) -> None:
        scale = self.ui_scale * factor
        if getattr(self, "_scale_initialized", False) and abs(scale - self._content_scale) < 0.001:
            return
        self._scale_initialized = True
        self._content_scale = scale
        for font, pixels in self._fonts.values():
            font.configure(size=-max(6, round(pixels * scale)))
        for widget, values in self._pack_spacing:
            if widget.winfo_manager() == "pack":
                widget.pack_configure(**{key: tuple(round(part * scale) for part in parts)
                                         for key, parts in values.items()})
        self.outer.configure(padx=round(8 * scale), pady=round(6 * scale))
        self.stats.configure(padx=round(6 * scale), pady=round(3 * scale))
        self.kill_chart.configure(height=max(24, round(54 * scale)))
        self.durability_bar.configure(height=max(3, round(4 * scale)))

    def _layout_values(self) -> None:
        width = self.winfo_width() if self.winfo_width() > 1 else round(STREAMER_DEFAULT_WIDTH * self.ui_scale)
        height = self.winfo_height() if self.winfo_height() > 1 else round(STREAMER_DEFAULT_HEIGHT * self.ui_scale)
        factor = min(1.0, width / (STREAMER_DEFAULT_WIDTH * self.ui_scale), height / (STREAMER_DEFAULT_HEIGHT * self.ui_scale))
        # Fit a fixed template, never the current strings. No idle-event pumping
        # here: intermediate font/layout candidates must not become visible.
        for _ in range(4):
            factor = max(0.55, factor)
            self._set_content_scale(factor)
            self.content_width = max(40, width - 2 * round(8 * self._content_scale) - 2)
            required = self._layout_metrics(self.content_width)
            if required <= height or factor <= 0.55:
                break
            factor *= (height - 4) / required
        if required > height:
            # Never trade away warning meaning or the footer/resize controls.
            # This is a content minimum, not a remembered user enlargement;
            # _fit_to_contents resets it before each real layout/width change.
            self.minsize(round(STREAMER_MIN_WIDTH * self.ui_scale),
                         max(round(STREAMER_MIN_HEIGHT * self.ui_scale), required))
        for key in self._fixed_labels:
            self._fit_fixed_text(key)
        self._update_loadout_text()
        self._fit_hero_captions()
        self._draw_kill_trend_chart()

    def _layout_metrics(self, width) -> int:
        scale = self._content_scale
        caption_height = self._font(9, True).metrics("linespace") + 2
        body_height = self._font().metrics("linespace") + 2
        net_height = self._font(24, True).metrics("linespace") + 2
        metric_height = self._font(12, True).metrics("linespace") + 2
        self.header.configure(height=max(caption_height, self._font(12).metrics("linespace") + 4))
        slot_width = min(round(138 * scale), width * 2 // 5)
        self.hero.columnconfigure(1, minsize=slot_width + 2 * round(3 * scale), weight=0)
        self.setup_slot_frame.grid_configure(padx=round(3 * scale))
        self._setup_slot_text_width = max(12, slot_width // 2 - round(6 * scale))
        self._fit_setup_labels()
        hero_caption_height = 2 * self._font(9, True).metrics("linespace") + 6
        self.hero.configure(height=hero_caption_height + net_height)
        self.hero.rowconfigure(0, minsize=hero_caption_height, weight=0)
        self.hero.rowconfigure(1, minsize=net_height, weight=1)
        self.stats.configure(height=caption_height + metric_height + 2 * round(3 * scale))
        for cell in self.stat_cells:
            cell.grid_configure(padx=(0, round(4 * scale)))
        self.input_slot.configure(height=body_height)
        if self.input_slot.winfo_manager():
            self.input_slot.pack_configure(pady=(round(4 * scale), 0))
        self.chart_header.configure(height=body_height)
        for key, (font, pixels) in self._fixed_fonts.items():
            # Warnings retain the default readable body size even at minimum
            # geometry; whole-overlay fitting must not undo wrapped readability.
            font_scale = self.ui_scale if key == "kill_trend_badge" else scale
            minimum = 10 if key == "kill_trend_badge" else 6
            font.configure(size=-max(minimum, round(pixels * font_scale)))
        self._summary_layout_lines = self._summary_lines()
        self._fixed_labels["kill_trend_badge"].configure(
            wraplength=max(1, width - 4), justify="left", bd=0, highlightthickness=0, padx=0, pady=0)
        summary_font = self._fixed_fonts["kill_trend_badge"][0]
        self.summary_slot.configure(height=self._summary_layout_lines * summary_font.metrics("linespace") + 2)
        self.trend.configure(height=body_height + int(self.kill_chart.cget("height")) +
                             int(self.summary_slot.cget("height")) + 2 * round(4 * scale))
        self.durability_text_slot.configure(height=body_height)
        self.durability_section.configure(height=body_height + int(self.durability_bar.cget("height")) + round(4 * scale))
        self.footer.configure(height=body_height + 2 * round(2 * scale))
        self._fixed_widths = {key: width - 4 for key in self._fixed_labels}
        metric_width = max(10, width - slot_width - 2 * round(3 * scale))
        self._fixed_widths.update(net_big=metric_width * 3 // 5 - 4, **{"return": metric_width * 2 // 5 - 4})
        for key in ("loot", "cost", "kills"):
            self._fixed_widths[key] = (width - 2 * round(6 * scale)) // 3 - round(4 * scale) - 4
        # Reserve ordinary signed money/return widths before the first value.
        # The shortcuts narrow these columns; 99→100 must not change typography.
        self._fit_fixed_text("net_big", "+999.99 PED\n-$999.99")
        self._fit_fixed_text("return", "999.99%")
        self._loadout_width = max(10, width - round(26 * scale))
        required = 2 * round(6 * scale) + 2  # outer padding and border
        for widget in self.outer.pack_slaves():
            required += int(widget.cget("height"))
            info = widget.pack_info()
            for key in ("pady", "ipady"):
                value = info[key]
                parts = value if isinstance(value, tuple) else self.tk.splitlist(str(value))
                required += int(parts[0]) + int(parts[-1])
        return required

    def _update_loadout_text(self) -> None:
        font = self._font()
        available = getattr(self, "_loadout_width", self.content_width - round(26 * self._content_scale))
        name = " ".join(self.vars["loadout"].get().split())
        text = name
        while text and font.measure(text + ("…" if text != name else "")) > available:
            text = text[:-1]
        visible = text + ("…" if text != name else "")
        if self.loadout_label.cget("text") != visible:
            self.loadout_label.configure(text=visible)

    def _show_loadout(self, _event=None, *, text: str | None = None) -> None:
        # Compatibility for old callers: no automatic popup, including names.
        # Current context is available only by deliberate footer activation.
        self._hide_loadout()

    def _show_context_details(self, _event=None) -> str:
        self._hide_loadout()
        messagebox.showinfo("Current chart context", getattr(self, "_footer_detail", "No active session"), parent=self)
        return "break"  # Do not also start dragging the overlay.

    def _hide_loadout(self, _event=None) -> None:
        if self._loadout_tip is not None:
            self._loadout_tip.destroy()
            self._loadout_tip = None

    def _keyboard_geometry(self, event) -> str:
        dx, dy = {"Left": (-10, 0), "Right": (10, 0), "Up": (0, -10), "Down": (0, 10)}[event.keysym]
        left, top, right, bottom = _streamer_work_area(self)
        self._user_positioned = True
        if event.state & 1:
            self._user_resized = True
            min_w, min_h = self.minsize()
            width = max(min_w, min(right - left, self.winfo_width() + dx))
            height = max(min_h, min(bottom - top, self.winfo_height() + dy))
            x = max(left, min(self.winfo_x(), right - width))
            y = max(top, min(self.winfo_y(), bottom - height))
            self.geometry(f"{width}x{height}+{x}+{y}")
        else:
            x = max(left, min(self.winfo_x() + dx, right - self.winfo_width()))
            y = max(top, min(self.winfo_y() + dy, bottom - self.winfo_height()))
            self.geometry(f"+{x}+{y}")
        return "break"

    def update_from_session(self, session: SessionSummary | None) -> None:
        metrics = streamer_metrics(session)
        mode = self.app.currency_mode.get()
        unit = currency_name(mode)
        self._trend_error = False
        # Main refreshes and display changes often repeat the same snapshot.
        # Never replay the entire event history twice for identical totals.
        trend_key = (session.session_id, session.events, session.accounting_revision) if session else (None, 0, 0)
        try:
            if getattr(self, "_kill_trend_key", None) == trend_key:
                kill_points = self._raw_kill_trend_points
            else:
                previous_key = getattr(self, "_kill_trend_key", None)
                if (previous_key is None or previous_key[0] != trend_key[0] or
                        trend_key[1] < previous_key[1] or previous_key[2] != trend_key[2]):
                    self._trend_builder = ActivityTrendBuilder(KILL_CLUSTER_WINDOW_SECONDS)
                    self._chart_scales = None
                    if previous_key is not None:
                        self._hunt_attribution = HuntAttribution(self.app.store)
                kill_points = _kill_trend_points(self.app.store, session.session_id if session else None, 16, self._trend_builder)
                self._raw_kill_trend_points = kill_points
                self._kill_trend_key = trend_key
        except Exception:
            # A malformed/very large historical session must not take down live
            # log ingestion or the main dashboard just because the overlay chart
            # cannot be refreshed.
            self._trend_error = True
            self._kill_trend_key = None
            kill_points = []
        kill_count = len(kill_points)
        live_chart = bool(session and not session.ended_at and self.app.running and
                          self.app.session_id == session.session_id and not self._trend_error)
        try:
            kill_points = self._hunt_attribution.update(
                session.session_id if session else None, kill_points, live=live_chart,
            )
            self._attribution_error = False
        except Exception:
            # Estimation is a separate projection, never an ingestion or billing
            # dependency. A failed write must not masquerade as a saved estimate.
            self._attribution_error = True
        try:
            self.kill_trail_histories = self._trail_recorder.update(
                session.session_id if session else None, kill_points,
                live_chart and not self._attribution_error,
                revised_event_ids=getattr(kill_points, "attribution_changed_ids", ()),
            )
            self._trail_error = False
        except Exception:
            # Optional visual history must never hide the accounting chart.
            self.kill_trail_histories = {}
            self._trail_error = True
        plan = self.manufacturing_plan = getattr(kill_points, "manufacturing", None)
        has_crafting = (getattr(kill_points, "has_crafting", False) or
                        int(float(metrics.get("manufacturing_attempt", 0))) > 0)
        unpriced_groups = getattr(kill_points, "unknown_craft_groups", 0)
        craft_active = bool(plan and plan.active)
        display = self._cost_display = crafting_cost_display(
            float(metrics['cost']), float(metrics['loot']), kill_points, projection_unavailable=self._trend_error)
        incomplete_cost = display.incomplete
        self._has_crafting = has_crafting
        self.net_caption.configure(text="NET" if display.marker else "BOOKED SESSION NET" if has_crafting else "NET PROFIT")
        self.loot_title.configure(text="Returns" if has_crafting else "Loot")
        self.cost_title.configure(text="Cost")
        self.count_title.configure(text="Groups*")
        self.chart_note.configure(text=f"* Prior groups · {unit}" if craft_active and not plan.observed and kill_points else
                                  f"* Observed groups · {unit}" if has_crafting else f"* Inferred clusters · {unit}")
        if self._trail_error:
            self.chart_note.configure(text=f"* Trails unavailable · {unit}")
        elif any(len(samples) > 1 for samples in self.kill_trail_histories.values()):
            self.chart_note.configure(text=f"* Observed · {unit} · fans" if has_crafting else
                                      f"* Inferred · {unit} · fans")
        if self._attribution_error:
            self.chart_note.configure(text=f"* Allocation unavailable · {unit}")
        elif any(point.get("cost_status") for point in kill_points[-16:]):
            self.chart_note.configure(text=(f"* Neighbor estimates · {unit}" if any(
                point.get("cost_basis") == "neighbors" for point in kill_points[-16:]
            ) else f"* Estimated/shared · {unit}") +
                (" · fans" if any(len(s) > 1 for s in self.kill_trail_histories.values()) else ""))
        elif any(point.get("healing_cost", 0) for point in kill_points[-16:]):
            self.chart_note.configure(text=f"* Selected healing estimates · {unit}" +
                                      (" · fans" if any(len(s) > 1 for s in self.kill_trail_histories.values()) else ""))
        net = display.net
        return_pct = display.return_pct
        color = "#B8C5D6" if incomplete_cost or net is None else "#86EFAC" if net > 0 else "#FDA4AF" if net < 0 else "#F1F5F9"
        state = ("STOPPED" if session and session.ended_at else
                 "TRACKING" if session and self.app.running else
                 "PAUSED" if session else "WAITING FOR RUN" if self.app.running else "IDLE")
        live = state == "TRACKING"
        self.vars["state"].set(f"{'DEMO · ' if self.demo else ''}{state}")
        self.state_label.configure(fg="#5EEAD4" if live else "#B8C5D6")
        context = (session.session_id if session else None, self.app.currency_mode.get(), live,
                   session.accounting_revision if session else 0, display.marker)
        if context != self._feedback_context and self.__dict__.get("_loadout_tip") is not None:
            self._hide_loadout()  # Never leave an open monetary tooltip in an old unit/revision.
        animate = context == self._feedback_context and live and not self.app.reduced_motion.get()
        self._feedback_context = context
        for key, value, signed in (("net_big", net if session else None, True),
                                   ("loot", float(metrics['loot']), False), ("cost", display.cost, False)):
            if value is None:
                self.animator.cancel(key)
                self.vars[key].set("—")
                continue
            marker = display.marker if key != "loot" else ""
            self.animator.number(key, value, lambda amount, key=key, signed=signed, marker=marker:
                                 self.vars[key].set(("≈" if marker == "≈" else "") +
                                     format_money(amount, self.app.currency_mode.get(), signed=signed) +
                                     ("*" if marker == "*" else "")), animate)
        if session and return_pct is not None:
            self.return_caption.configure(text="RETURN" if display.marker else "BOOKED RETURN" if has_crafting else "RETURN")
            self.animator.number("return", return_pct, lambda value: self.vars["return"].set(
                ("≈" if display.marker else "") + f"{value:.2f}%"), animate)
        else:
            self.animator.cancel("return")
            self.vars["return"].set("—")
            self.return_caption.configure(text="RETURN*" if incomplete_cost else "RETURN · NO COST YET" if session else "RETURN")
        self._fit_hero_captions()
        self.vars["kills"].set("—" if self._trend_error else f"{kill_count}")
        if has_crafting:
            if plan:
                price = "unknown" if plan.per_attempt is None else format_chart_money(plan.per_attempt, mode)
                self.vars["input"].set(f"{'TT input' if craft_active else 'Last TT input'} {price} · {plan.observed}/{plan.attempts} observed*")
            elif int(float(metrics.get("manufacturing_attempt", 0))) > 0:
                self.vars["input"].set(f"Manufacturing input {format_chart_money(float(metrics['last_input_cost']), mode)} • attempt {int(float(metrics['manufacturing_attempt']))}/{int(float(metrics['manufacturing_attempts_total']))}")
            else:
                self.vars["input"].set("Craft inputs unknown — add blueprint + attempts")
            if not self.input_slot.winfo_manager():
                self.input_slot.pack(after=self.stats, fill="x", pady=(4, 0))
                self.input_label.pack(fill="both", expand=True)
                self._schedule_fit_to_contents()
        else:
            self.vars["input"].set("Manufacturing input —")
            if self.input_slot.winfo_manager():
                self.input_label.pack_forget()
                self.input_slot.pack_forget()
                self._schedule_fit_to_contents()
        self.vars["damage"].set(f"Dmg {float(metrics['damage']):.1f}")
        displayed_loadout = str(metrics["loadout"]) if session else (self._setup_active_name or str(metrics["loadout"]))
        self.vars["loadout"].set(displayed_loadout)
        durability = repair_durability_status(session)
        self.durability_pct = durability["percent"]
        self.durability_known = durability["label"] != "Durability"
        self.durability_label = str(durability["label"])
        self.durability_color = ("#FDA4AF" if self.durability_pct <= 10 else
                                 "#FDBA74" if self.durability_pct <= 30 else "#86EFAC")
        durability_text = str(durability["streamer_text"])
        if self.durability_known and self.durability_pct <= 0:
            durability_text += " · EMPTY"
        if plan and craft_active:
            self.vars["loadout"].set(f"Crafting: {plan.blueprint}")
            self.vars["damage"].set(f"{plan.observed}/{plan.attempts}*")
            batch = crafting_cost_display(plan.booked_cost, plan.returns,
                                          (p for p in kill_points if p.get('plan_event_id') == plan.event_id),
                                          unpriced_plans=int(plan.per_attempt is None))
            batch_cost = "—" if batch.cost is None else format_chart_money(batch.cost, mode)
            batch_net = "—" if batch.net is None else format_chart_money(batch.net, mode, signed=True)
            durability_text = (f"Return {format_chart_money(plan.returns, mode)} · "
                               f"Cost {batch.marker}{batch_cost} · Net {batch.marker}{batch_net}")
            self.durability_known = False
        self._footer_detail = _context_details(
            displayed_loadout, kill_points, mode, plan=plan, unpriced_groups=unpriced_groups,
            allocation_error=self._attribution_error, trail_error=self._trail_error, cost_display=display)
        self.vars["durability"].set(durability_text)
        self.vars["kill_trend"].set(_kill_trend_summary(kill_points, mode))
        summary = ("Trend unavailable · totals still shown" if self._trend_error else
                   "Allocation unavailable · booked totals unchanged\nChart shows raw receipt-interval input, not exact creature cost" if self._attribution_error else
                   (f"Craft setup armed · 0/{plan.attempts} observed\nBatch {format_chart_money(plan.booked_cost, mode)} booked"
                    + (" · " + _healing_status_text(kill_points, mode) if _healing_status_text(kill_points, mode) else "; waiting for receipts"))
                   if craft_active and not plan.observed else
                   _streamer_trend_detail(kill_points, mode) if kill_points or getattr(kill_points, "healing_booked", 0) or getattr(kill_points, "unpriced_heals", 0) else
                   "No observed craft groups yet · inputs booked" if has_crafting else "No kill points yet")
        self.vars["kill_trend_badge"].set(summary)
        previous_points = getattr(self, "kill_trend_points", [])
        self.kill_trend_points = kill_points
        self._draw_durability_bar()
        if animate and previous_points and previous_points != kill_points:
            self.animator.tween("chart", self._chart_frame, 400)
        elif not animate:
            self.animator.cancel("chart")
            self._chart_phase = 1.0
            self._draw_kill_trend_chart()
        else:
            self._draw_kill_trend_chart()
        self.net_label.configure(fg=color)
        self.return_label.configure(fg="#F1F5F9")

    def _draw_durability_bar(self) -> None:
        canvas = self.durability_bar
        if not hasattr(self, "durability_bar"):
            return
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        pct = max(0.0, min(100.0, self.durability_pct))
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#111827", outline="#1f2937", width=1)
        fill_width = int((width - 2) * pct / 100.0) if self.durability_known else 0
        if fill_width > 0:
            canvas.create_rectangle(1, 1, fill_width + 1, height - 1, fill=self.durability_color, outline="")


    def _chart_frame(self, phase: float) -> None:
        self._chart_phase = phase
        self._draw_kill_trend_chart()

    def _draw_kill_trend_chart(self) -> None:
        if not hasattr(self, "kill_chart"):
            return
        canvas = self.kill_chart
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        points = getattr(self, "kill_trend_points", [])
        display_points = points[-16:]
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#131E2E", outline="")
        if not display_points:
            canvas.create_text(
                width / 2, height / 2,
                text=("Trend unavailable · totals still shown" if getattr(self, "_trend_error", False) else
                      "Waiting for crafting receipts\nInputs are booked; silent attempts are unobservable" if getattr(self, "_has_crafting", False) else
                      "No kill points yet\nCombat and loot events build this trend"),
                fill="#B8C5D6", font=self._font(), width=max(width - 16, 60), justify="center",
            )
            return

        scales = plan_chart_scales(display_points, getattr(self, "kill_trail_histories", {}),
                                   previous=getattr(self, "_chart_scales", None))
        self._chart_scales = scales
        axis_font = self._font(8)
        local_font = self._font(5) if scales.local_indices else None
        ordinary = scales.ordinary
        mode = self.app.currency_mode.get()
        tick_values = [ordinary.low, (ordinary.low + ordinary.high) / 2, ordinary.high]
        pad_left = max(round(30 * self._content_scale),
                       max(axis_font.measure(format_chart_money(value, mode, signed=True)) for value in tick_values) + 8)
        pad_right = round(10 * self._content_scale)
        pad_top = axis_font.metrics("linespace") // 2 + 3
        if scales.local_indices:
            pad_top = max(pad_top, local_font.metrics("linespace") + 4)
        pad_bottom = axis_font.metrics("linespace") + 5
        plot_w = max(width - pad_left - pad_right, 1)
        plot_h = max(height - pad_top - pad_bottom, 1)
        cell_w = plot_w / len(display_points)

        def base_x(index):
            return pad_left + cell_w * (index + 0.5)

        def x_for(index):
            drawn_index = index
            if index == len(display_points) - 1 and index and scales.connected(index - 1, index):
                drawn_index -= 1 - self._chart_phase
            return base_x(drawn_index)

        def value_for(index, key):
            current = display_points[index]
            if current[key] is None:
                return None
            value = float(current[key])
            if index == len(display_points) - 1 and index and scales.connected(index - 1, index):
                previous = display_points[index - 1]
                if (previous[key] is not None and previous.get("kind") == current.get("kind") and
                        previous.get("plan_event_id") == current.get("plan_event_id")):
                    return float(previous[key]) + (value - float(previous[key])) * self._chart_phase
            return value

        def y_for(index, value):
            axis = ordinary if index is None else scales.events[index]
            return pad_top + plot_h * (1 - axis.fraction(value))

        sections = []
        for index in sorted(scales.local_indices):
            x = base_x(index)
            half = cell_w * 0.44
            sections.append((index, x - half, x + half))

        # A shared grid or connecting segment must not imply equal heights mean
        # equal PED inside an independently scaled event column.
        ordinary_spans = []
        start = pad_left
        for _, left, right in sections:
            if left > start:
                ordinary_spans.append((start, left))
            start = right
        if start < width - pad_right:
            ordinary_spans.append((start, width - pad_right))
        for tick in tick_values:
            y = y_for(None, tick)
            for left, right in ordinary_spans:
                canvas.create_line(left, y, right, y, fill="#334155", tags=("ordinary-grid",))
            canvas.create_text(pad_left - 5, y, text=format_chart_money(tick, mode, signed=True), anchor="e",
                               fill="#B8C5D6", font=axis_font, tags=("money-axis", "axis:ordinary"))
        for left, right in ordinary_spans:
            zero_y = y_for(None, 0)
            canvas.create_line(left, zero_y, right, zero_y, fill="#B8C5D6",
                               dash=(3, 2), tags=("ordinary-zero",))

        for index, left, right in sections:
            point = display_points[index]
            axis = scales.events[index]
            tags = ("outlier-section", f"outlier:{point['event_id']}")
            canvas.create_rectangle(left, 1, right, pad_top + plot_h, fill="#1B293C",
                                    outline="#D9A75A", width=1, tags=tags)
            canvas.create_text(base_x(index), pad_top - 3, text="local", anchor="s",
                               fill="#FCD58A", font=local_font, tags=tags)
            extreme = max((axis.low, axis.high), key=abs)
            label = format_chart_money(extreme, mode)
            canvas.create_text(left + local_font.metrics("linespace") / 2 + 1,
                               pad_top + plot_h / 2, text=label, angle=90,
                               fill="#FCD58A", font=local_font, tags=(*tags, "money-axis", "axis:local"))
            for value in (axis.low, axis.high):
                y = y_for(index, value)
                canvas.create_line(left, y, right, y, fill="#D9A75A", tags=tags)

        self._draw_kill_trail_fans(display_points, x_for, y_for, value_for, scales)
        for key, color, dash in (("cost", "#FDBA74", ()), ("avg_cost", "#7DD3FC", (4, 2)),
                                 ("loot", "#86EFAC", ())):
            # Keep both series visible at equal values: a thin dashed average
            # overlays the wider cost stroke without displacing either value.
            line_width = 3 if key == "cost" else 1 if key == "avg_cost" else 2
            coords = []
            for index, point in enumerate(display_points):
                value = value_for(index, key)
                if value is None or (index and not scales.connected(index - 1, index)):
                    if len(coords) >= 4:
                        canvas.create_line(*coords, fill=color, width=line_width, dash=dash,
                                           tags=("series", f"series:{key}"))
                    coords = []
                if value is not None:
                    coords.extend((x_for(index), y_for(index, value)))
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=line_width, dash=dash,
                                   tags=("series", f"series:{key}"))
            for index, point in enumerate(display_points):
                value = value_for(index, key)
                if value is None:
                    continue
                x, y = x_for(index), y_for(index, value)
                if key == "loot":
                    cost = value_for(index, "cost")
                    fill = "#B8C5D6" if cost is None else "#86EFAC" if value >= cost else "#FDA4AF"
                    radius = 3 if index == len(display_points) - 1 else 2
                    estimated = bool(point.get("cost_status") or point.get("healing_cost", 0))
                    tags = (key, "estimate", f"estimate:{point['event_id']}") if estimated else (key,)
                    canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill,
                                       outline="#FBBF24" if estimated else "", width=1, tags=tags)
                else:
                    radius = 2 if index == len(display_points) - 1 else 1
                    canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                       fill=color, outline="", tags=(key,))
        for index in (0, len(display_points) - 1):
            canvas.create_text(base_x(index), height - 2,
                               text=display_points[index].get("label", f"K{display_points[index]['kill']}"),
                               anchor="s", fill="#B8C5D6", font=axis_font)
            if len(display_points) == 1:
                break

    def _draw_kill_trail_fans(self, points, x_for, y_for, value_for, scales) -> None:
        """Reproject samples on their event's axis; never bridge scale boundaries."""
        canvas = self.kill_chart
        histories = getattr(self, "kill_trail_histories", {})
        for index, point in enumerate(points):
            event_id = point.get("event_id")
            samples = histories.get(event_id, ())
            if len(samples) < 2:
                continue
            previous = points[index - 1] if index else None
            same_scope = bool(previous and scales.connected(index - 1, index) and
                              previous.get("kind") == point.get("kind") and
                              previous.get("plan_event_id") == point.get("plan_event_id"))
            x = x_for(index)
            for column, key, shade, ghost in (
                (0, "cost", "#3C342E", "#8C7355"),
                (1, "avg_cost", "#203244", "#466478"),
                (2, "loot", "#233B32", "#4A956C"),
            ):
                observed = [(i, float(s[column])) for i, s in enumerate(samples) if s[column] is not None]
                if not observed:
                    continue
                low = min(observed, key=lambda item: item[1])
                high = max(observed, key=lambda item: item[1])
                if math.isclose(low[1], high[1], abs_tol=1e-12):
                    continue
                tags = ("trail", f"trail:{event_id}", f"trail:{key}")
                anchor = value_for(index - 1, key) if same_scope else None

                def projected(value):
                    # Animation changes geometry only, never the saved samples.
                    if anchor is not None and index == len(points) - 1:
                        value = anchor + (value - anchor) * self._chart_phase
                    return y_for(index, value)

                if key == "loot":
                    outcomes = {None if s[0] is None else s[2] >= s[0] for s in samples}
                    shade = ("#233B32" if outcomes == {True} else "#402D38" if outcomes == {False}
                             else "#293642")
                if anchor is not None:
                    start = (x_for(index - 1), y_for(index - 1, anchor))
                    canvas.create_polygon(*start, x, projected(low[1]), x, projected(high[1]),
                                          fill=shade, outline="", tags=tags)
                else:
                    # The first visible group has no visible incoming segment.
                    # Show its observed endpoint range, not an invented anchor.
                    start = None
                    canvas.create_line(x, projected(low[1]), x, projected(high[1]),
                                       fill=ghost, width=3, tags=tags)
                # First/latest and extrema suffice to read the envelope without
                # turning each group into a dense stack of 24 lines.
                chosen = sorted({observed[0][0], observed[-1][0], low[0], high[0]})
                for sample_index in chosen:
                    sample = samples[sample_index]
                    color, outcome = ghost, "neutral"
                    if key == "loot":
                        outcome = "unknown" if sample[0] is None else "profit" if sample[2] >= sample[0] else "loss"
                        color = {"unknown": "#69788C", "profit": "#4A956C", "loss": "#A36579"}[outcome]
                    y = projected(float(sample[column]))
                    if start is not None:
                        canvas.create_line(*start, x, y, fill=color, width=1,
                                           tags=(*tags, f"trail:{outcome}"))
                    else:
                        canvas.create_line(x - 2, y, x + 2, y, fill=color, width=1,
                                           tags=(*tags, f"trail:{outcome}"))



    def _build_resize_edges(self) -> None:
        backend = str(self.tk.call("tk", "windowingsystem"))
        thickness = max(3, round(4 * self.ui_scale))
        corner = max(6, round(6 * self.ui_scale))
        self.resize_edges = {}
        positions = {
            "n": dict(relx=0, rely=0, relwidth=1, height=thickness, anchor="nw"),
            "s": dict(relx=0, rely=1, relwidth=1, height=thickness, anchor="sw"),
            "w": dict(relx=0, rely=0, relheight=1, width=thickness, anchor="nw"),
            "e": dict(relx=1, rely=0, relheight=1, width=thickness, anchor="ne"),
            "nw": dict(relx=0, rely=0, width=corner, height=corner, anchor="nw"),
            "ne": dict(relx=1, rely=0, width=corner, height=corner, anchor="ne"),
            "sw": dict(relx=0, rely=1, width=corner, height=corner, anchor="sw"),
            "se": dict(relx=1, rely=1, width=corner, height=corner, anchor="se"),
        }
        for edge, position in positions.items():
            if edge in ("n", "s"):
                cursor = "size_ns" if backend == "win32" else "sb_v_double_arrow"
            elif edge in ("w", "e"):
                cursor = "size_we" if backend == "win32" else "sb_h_double_arrow"
            elif edge in ("nw", "se"):
                cursor = _resize_cursor(backend)
            else:
                cursor = "size_ne_sw" if backend == "win32" else "bottom_left_corner"
            target = tk.Frame(self, bg="#0B1220", cursor=cursor)
            target.place(**position)
            target.bind("<ButtonPress-1>", lambda event, edge=edge: self._start_resize(event, edge))
            target.bind("<B1-Motion>", self._resize)
            target.bind("<ButtonRelease-1>", self._stop_resize)
            self.resize_edges[edge] = target

    def _start_drag(self, event) -> None:
        if getattr(event, "widget", None) in (self.close_button, self.resize_handle,
                                            *self.resize_edges.values(), *getattr(self, "setup_buttons", ())):
            return
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y()) if hasattr(event, "widget") else (event.x, event.y)

    def _drag(self, event) -> None:
        if not self._drag_origin:
            return
        dx, dy = self._drag_origin
        self._user_positioned = True
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _start_resize(self, event, edges="se") -> str:
        self._drag_origin = None
        self._resize_origin = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y(),
                               self.winfo_width(), self.winfo_height(), edges)
        self._resize_capture = getattr(event, "widget", self.resize_handle)
        if self._resize_capture.winfo_viewable():
            self._resize_capture.grab_set()
        return "break"

    def _resize(self, event) -> str:
        if not self._resize_origin:
            return "break"
        start_x, start_y, x, y, width, height, edges = self._resize_origin
        dx, dy = event.x_root - start_x, event.y_root - start_y
        min_width, min_height = self.minsize()
        if "w" in edges:
            resized = max(min_width, width - dx)
            x += width - resized
            width = resized
        elif "e" in edges:
            width = max(min_width, width + dx)
        if "n" in edges:
            resized = max(min_height, height - dy)
            y += height - resized
            height = resized
        elif "s" in edges:
            height = max(min_height, height + dy)
        self._user_resized = True
        self._user_positioned = True
        self.geometry(f"{width}x{height}+{x}+{y}")
        return "break"

    def _stop_resize(self, _event) -> str:
        self._resize_origin = None
        if self._resize_capture is not None:
            if self.grab_current() == self._resize_capture:
                self._resize_capture.grab_release()
            self._resize_capture = None
        return "break"

    def _schedule_fit_to_contents(self) -> None:
        if not self.winfo_exists() or self._layout_busy:
            return
        if self._fit_after_id is not None:
            try:
                self.after_cancel(self._fit_after_id)
            except tk.TclError:
                pass
        self._fit_after_id = self.after_idle(self._fit_to_contents)

    def _fit_to_contents(self) -> None:
        self._fit_after_id = None
        if not self.winfo_exists() or self._layout_busy:
            return
        width = self.winfo_width() if self.winfo_width() > 1 else round(STREAMER_DEFAULT_WIDTH * self.ui_scale)
        height = self.winfo_height() if self.winfo_height() > 1 else round(STREAMER_DEFAULT_HEIGHT * self.ui_scale)
        key = (width, height, bool(self.input_slot.winfo_manager()))
        if not self._initial_position_pending and key == self._layout_geometry_key:
            return
        self._layout_busy = True
        try:
            # A content floor must survive the Configure event it caused.
            # Resetting on that height-only event lets Windows restore the
            # smaller requested rectangle, producing an endless shrink/grow
            # loop. Width/content changes may legitimately lower the floor.
            floor_key = (width, bool(self.input_slot.winfo_manager()),
                         self.vars["kill_trend_badge"].get(), self.ui_scale)
            if floor_key != getattr(self, "_content_floor_key", None):
                self.minsize(round(STREAMER_MIN_WIDTH * self.ui_scale), round(STREAMER_MIN_HEIGHT * self.ui_scale))
                self._content_floor_key = floor_key
            self._layout_values()
            self._layout_geometry_key = key
            if self._initial_position_pending:
                width = round(STREAMER_DEFAULT_WIDTH * self.ui_scale)
                height = round(STREAMER_DEFAULT_HEIGHT * self.ui_scale)
                x, y = _bottom_right_position(self._initial_work_area, width, height)
                self.geometry(f"{width}x{height}+{x}+{y}")
                self._initial_position_pending = False
                self.deiconify()
            # Subsequent updates lay out within the user's rectangle. They
            # never rewrite its geometry or snap it back to the default size.
        finally:
            self._layout_busy = False

    def destroy(self) -> None:
        self._stop_resize(None)
        self._hide_loadout()
        self.animator.cancel_all()
        if self._fit_after_id is not None:
            try:
                self.after_cancel(self._fit_after_id)
            except tk.TclError:
                pass
            self._fit_after_id = None
        self.app.streamer_window = None
        super().destroy()


def _default_chat_log_path() -> Path:
    import sys

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        try:
            # Ask the shell for the current Documents location, including
            # OneDrive/domain redirection, rather than constructing it at home.
            get_folder = ctypes.WinDLL("shell32", use_last_error=True).SHGetFolderPathW
            get_folder.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                                   wintypes.DWORD, wintypes.LPWSTR]
            get_folder.restype = ctypes.c_long
            buffer = ctypes.create_unicode_buffer(32768)
            if get_folder(None, 5, None, 0, buffer) == 0 and buffer.value:
                return Path(buffer.value) / "Entropia Universe" / "chat.log"
        except (OSError, AttributeError):
            pass
    return Path.home() / "Documents" / "Entropia Universe" / "chat.log"


def _wtb_listings(store: Store, limit: int = 240) -> list[dict[str, object]]:
    """The latest ``limit`` WTB listings from trade-channel chat, oldest first.

    Looks back through enough recent chat that busy non-trade channels do not
    push the listings out.
    """
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.timestamp, e.kind, e.payload
            FROM events e
            WHERE e.kind = 'chat'
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (limit * 25,),
        ).fetchall()

    listings: list[dict[str, object]] = []
    for row in reversed(rows):
        try:
            payload = json.loads(str(row["payload"] or "{}"))
        except json.JSONDecodeError:
            continue
        channel = str(payload.get("channel") or "")
        if not _is_trade_channel(channel):
            continue
        speaker = str(payload.get("speaker") or "")
        message = str(payload.get("message") or "")
        for item in extract_wtb_segments(channel, speaker, message):
            item["timestamp"] = str(row["timestamp"] or "")
            item.setdefault("message", message)
            listings.append(item)
    return listings[-limit:]


def _is_trade_channel(channel: str) -> bool:
    normalized = channel.casefold().lstrip("#")
    return "trade" in normalized or normalized in {"barter", "buy", "sell"}


def _recent_events(store: Store, session_id: str, limit: int = 50,
                   kinds: tuple[str, ...] | None = None) -> list[dict[str, object]]:
    """The run's latest counted events, oldest first; ``kinds`` narrows them (never chat)."""
    wanted = "kind != 'chat'" if not kinds else f"kind IN ({','.join('?' * len(kinds))})"
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, timestamp, kind, raw_message, payload
            FROM accounted_events
            WHERE session_id = ? AND {wanted}
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, *(kinds or ()), limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _loot_event_points(store: Store, session_id: str | None, limit: int = 160) -> list[tuple[str, float, str]]:
    """Return chronological loot values suitable for plotting.

    Each point is (timestamp, value_in_ped, short_item_label). Malformed legacy
    payloads and loot rows without a numeric value are skipped so the chart keeps
    drawing even when older local data contains bad JSON.
    """
    if not session_id:
        return []
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, payload
            FROM accounted_events
            WHERE session_id = ? AND kind = 'loot'
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    points: list[tuple[str, float, str]] = []
    for row in reversed(rows):
        try:
            payload = json.loads(str(row["payload"] or "{}"))
            value = float(payload.get("value", 0) or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if value <= 0:
            continue
        item_name = str(payload.get("item_name") or "loot")
        if is_conversion_output_item(item_name):
            continue
        label = f"({item_name})" if item_name and item_name != "loot" else ""
        points.append((str(row["timestamp"] or ""), value, label))
    return points


def _summarize_event(row: dict[str, object], mode: str = "USD") -> str:
    try:
        payload = json.loads(str(row.get("payload") or "{}"))
    except json.JSONDecodeError:
        payload = {}
    kind = row.get("kind")
    if kind == "loot":
        description = f"{payload.get('quantity', 1)} x {payload.get('item_name', '?')} — {_small_money(float(payload.get('value', 0) or 0), mode)}"
        if payload.get("input_cost") is not None:
            attempt = int(payload.get("attempt_number", 0) or 0)
            total = int(payload.get("attempts_total", 0) or 0)
            progress = f" • Craft {attempt}/{total}" if attempt and total else ""
            description += f" • Input {float(payload['input_cost'] or 0):.2f} PED{progress}"
        return description
    if kind == "combat":
        cost = f" • {format_money(float(payload['shot_cost']), mode, decimals=5)}" if "shot_cost" in payload else ""
        if "damage" in payload:
            return f"Damage dealt: {payload['damage']}{cost}"
        if payload.get("dodged"):
            return f"Dodged your attack{cost}"
        if payload.get("outgoing_miss"):
            return f"Outgoing miss{cost}"
        if "damage_taken" in payload:
            return f"Damage taken: {payload['damage_taken']}"
        if "healed" in payload:
            target = payload.get("healing_target", "self")
            text = f"Healed {target}: {payload['healed']}"
            if payload.get("healing_cost_status") == "duplicate":
                return text + " · duplicate message, not charged"
            if "healing_cost" in payload:
                return text + f" · {format_money(float(payload['healing_cost']), mode, decimals=5)} (selected tool estimate)"
            return text + (" · healing unpriced" if "healing_cost_status" in payload else "")
        return (", ".join(k for k, v in payload.items() if v and k not in {"shot_cost", "loadout"}) or "Combat event") + cost
    if kind == "weapon":
        return f"Equipped {payload.get('weapon', '?')}"
    if kind == "skill":
        return f"{payload.get('skill', '?')} +{payload.get('xp', '?')} XP"
    if kind == "craft":
        if "total_cost" in payload:
            attempts = int(payload.get("attempts", 1) or 1)
            blueprint = payload.get("blueprint", "Blueprint")
            return f"Crafting input: {attempts:,} x {blueprint} — {format_money(float(payload.get('total_cost', 0) or 0), mode)}"
        return f"{payload.get('result', '?')} {payload.get('item', '')}".strip()
    if kind == "global" and payload.get("message"):
        return str(payload["message"])
    return str(row.get("raw_message") or "")


def _small_money(value_ped: float, mode: str) -> str:
    """Two decimals, or four when two would show a real amount as zero."""
    shown = format_money(value_ped, mode)
    if value_ped and format_money(0.0, mode) == shown:
        return format_money(value_ped, mode, decimals=4)
    return shown


def _listing_order(name: str) -> tuple[bool, str]:
    """A to Z, with code-like names (0x…) after the real ones."""
    return name.casefold().startswith("0x"), name.casefold()


def _short_time(value: object, *, seconds: bool = False, today: str = "") -> str:
    """A stored ISO timestamp as "Nov 14 03:09"; only the clock on the ISO day ``today``."""
    text = str(value or "")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return text
    clock = when.strftime("%H:%M:%S" if seconds else "%H:%M")
    if today and text[:10] == today:
        return clock
    today = datetime.now()
    year = "" if when.year == today.year else f" {when.year}"
    return f"{when.strftime('%b')} {when.day}{year} {clock}"


def calculate_loadout_cost(
    *,
    catalog: Catalog,
    weapon_name: str,
    amp: str = "",
    scope: str = "",
    sight_1: str = "",
    sight_2: str = "",
    damage_enhancers: int = 0,
    economy_enhancers: int = 0,
) -> tuple[int, float]:
    weapon = catalog.find_weapon(weapon_name)
    if not weapon:
        raise ValueError(f"Unknown weapon: {weapon_name}")
    if weapon.category.startswith("Mining Laser") and (
        any((amp, scope, sight_1, sight_2)) or damage_enhancers or economy_enhancers
    ):
        raise ValueError("Mining laser attachment/enhancer compatibility is unverified; choose None and zero enhancers")
    multiplier = (1 + (0.1 * damage_enhancers)) * (1 - (0.01 * economy_enhancers))
    ammo = weapon.ammo * multiplier
    decay = weapon.decay * multiplier
    for part in (amp, scope, sight_1, sight_2):
        if not part:
            continue
        attachment = catalog.attachments.get(part)
        if attachment:
            ammo += attachment.ammo
            decay += attachment.decay
    return int(ammo), float(decay)


def calculate_gun_amp_repair_decay(
    *,
    catalog: Catalog,
    weapon_name: str,
    amp: str = "",
    damage_enhancers: int = 0,
    economy_enhancers: int = 0,
) -> float:
    """Return repair-terminal decay per outgoing shot for the gun + amp only."""
    return sum(float(item["decay_per_shot"]) for item in calculate_gun_amp_repair_items(
        catalog=catalog,
        weapon_name=weapon_name,
        amp=amp,
        damage_enhancers=damage_enhancers,
        economy_enhancers=economy_enhancers,
    ))


def calculate_gun_amp_repair_items(
    *,
    catalog: Catalog,
    weapon_name: str,
    amp: str = "",
    damage_enhancers: int = 0,
    economy_enhancers: int = 0,
) -> list[dict[str, object]]:
    """Return per-item repair budgets so the lowest-durability item can be shown."""
    weapon = catalog.find_weapon(weapon_name)
    if not weapon:
        raise ValueError(f"Unknown weapon: {weapon_name}")
    multiplier = (1 + (0.1 * damage_enhancers)) * (1 - (0.01 * economy_enhancers))
    items: list[dict[str, object]] = [
        {
            "role": "Gun",
            "name": weapon.name,
            "decay_per_shot": float(weapon.decay * multiplier),
            "budget": max(0.0, weapon.max_tt - weapon.min_tt) if weapon.max_tt is not None else 0.0,
            "budget_known": weapon.max_tt is not None,
        }
    ]
    attachment = catalog.attachments.get(amp) if amp else None
    if attachment:
        items.append(
            {
                "role": "Amp",
                "name": attachment.name,
                "decay_per_shot": float(attachment.decay),
                "budget": max(0.0, attachment.max_tt - attachment.min_tt) if attachment.max_tt is not None else 0.0,
                "budget_known": attachment.max_tt is not None,
            }
        )
    return items


def calculate_gun_amp_repair_budget(catalog: Catalog, weapon_name: str, amp: str = "") -> tuple[float, bool]:
    """Return the full-to-min TT buffer for the gun + amp when catalog TT data is known."""
    items = calculate_gun_amp_repair_items(catalog=catalog, weapon_name=weapon_name, amp=amp)
    return sum(float(item["budget"]) for item in items), all(bool(item["budget_known"]) for item in items)


def with_repair_estimates(catalog: Catalog, loadout: LoadoutRecord) -> LoadoutRecord:
    loadout.repair_items = calculate_gun_amp_repair_items(
        catalog=catalog,
        weapon_name=loadout.weapon,
        amp=loadout.amp,
        damage_enhancers=loadout.damage_enhancers,
        economy_enhancers=loadout.economy_enhancers,
    )
    loadout.repair_decay_per_shot = sum(float(item["decay_per_shot"]) for item in loadout.repair_items)
    loadout.repair_budget = sum(float(item["budget"]) for item in loadout.repair_items)
    loadout.repair_budget_known = all(bool(item["budget_known"]) for item in loadout.repair_items)
    return loadout


def repair_durability_status(session: SessionSummary | None) -> dict[str, object]:
    if not session:
        return {"percent": 0.0, "label": "Durability", "streamer_text": "Durability —", "shots_left": None}
    snapshot = session.loadout_snapshot or {}
    starting_shots = int(snapshot.get("repair_shots", 0) or 0)
    total_shots = starting_shots + max(0, int(session.repair_shots or 0))
    items = snapshot.get("repair_items") or []
    if not isinstance(items, list):
        items = []
    statuses: list[dict[str, object]] = []
    used_decay = 0.0
    for item in items:
        if not isinstance(item, dict) or not item.get("budget_known"):
            continue
        budget = float(item.get("budget", 0) or 0)
        decay = float(item.get("decay_per_shot", 0) or 0)
        if budget <= 0 or decay <= 0:
            continue
        used = total_shots * decay
        remaining = max(0.0, budget - used)
        used_decay += used
        statuses.append(
            {
                "role": str(item.get("role") or "Item"),
                "name": str(item.get("name") or "Item"),
                "percent": remaining / budget * 100.0,
                "remaining": remaining,
                "budget": budget,
                "shots_left": max(0, math.ceil(remaining / decay)),
            }
        )
    if not statuses:
        budget = float(snapshot.get("repair_budget", 0) or 0)
        known = bool(snapshot.get("repair_budget_known")) and budget > 0
        if known:
            per_shot = float(snapshot.get("repair_decay_per_shot", 0) or 0)
            used_decay = total_shots * per_shot
            remaining = max(0.0, budget - used_decay)
            pct = remaining / budget * 100.0
            return {
                "percent": pct,
                "label": "Loadout",
                "streamer_text": f"Loadout durability {pct:.1f}%",
                "shots_left": None,
                "used_shots": total_shots,
                "used_decay": used_decay,
            }
        return {
            "percent": 0.0,
            "label": "Durability",
            "streamer_text": f"Durability unknown • {total_shots:,} shots tracked",
            "shots_left": None,
            "used_shots": total_shots,
            "used_decay": used_decay,
        }
    bottleneck = min(statuses, key=lambda item: float(item["percent"]))
    pct = float(bottleneck["percent"])
    role = str(bottleneck["role"])
    shots_left = int(bottleneck["shots_left"])
    return {
        "percent": pct,
        "label": role,
        "streamer_text": f"{role} durability {pct:.1f}% • ~{shots_left:,} shots left",
        "shots_left": shots_left,
        "items": statuses,
        "used_shots": total_shots,
        "used_decay": used_decay,
    }


def format_repair_radar(session: SessionSummary | None, mode: str = "USD") -> str:
    if not session:
        return "Repair radar: start a fresh run at 100% gun + amp TT"
    snapshot = session.loadout_snapshot or {}
    status = repair_durability_status(session)
    used = float(status.get("used_decay", 0.0) or 0.0)
    shots = int(status.get("used_shots", session.repair_shots) or 0)
    if "items" in status:
        shown = str(status['streamer_text']).replace("~", "")
        return f"Repair radar: {shown} • {format_money(used, mode, decimals=4)} gun+amp decay across {shots:,} shots"
    budget = float(snapshot.get("repair_budget", 0) or 0)
    known = bool(snapshot.get("repair_budget_known")) and budget > 0
    if not known:
        return f"Repair radar: {shots:,} shots since 100% • {format_money(used, mode, decimals=4)} gun+amp decay accrued"
    remaining = max(0.0, budget - used)
    remaining_pct = remaining / budget * 100.0
    per_shot = float(snapshot.get("repair_decay_per_shot", 0) or 0)
    shots_left = max(0, math.ceil(remaining / per_shot)) if per_shot > 0 else 0
    return (
        f"Repair radar: {remaining_pct:.1f}% gun+amp TT left • "
        f"{format_money(remaining, mode)}/{format_money(budget, mode)} remaining • {shots_left:,} shots to empty"
    )


def calculate_blueprint_material_cost(catalog: Catalog, blueprint_name: str) -> tuple[float, list[tuple[str, int, float, float]]]:
    blueprint = catalog.find_blueprint(blueprint_name)
    if not blueprint:
        raise ValueError("choose a known blueprint")
    materials: list[tuple[str, int, float, float]] = []
    missing: list[str] = []
    total = 0.0
    for material_name, quantity in blueprint.materials:
        resource = catalog.resources.get(material_name)
        if resource is None:
            missing.append(material_name)
            continue
        line_total = quantity * resource.tt_value
        materials.append((material_name, quantity, resource.tt_value, line_total))
        total += line_total
    if missing:
        names = ", ".join(missing[:5])
        if len(missing) > 5:
            names += f", +{len(missing) - 5} more"
        raise ValueError(f"missing TT values for blueprint materials: {names}")
    return total, materials


def _positive_int(value: str) -> int:
    try:
        parsed = int(value.replace(",", "").strip())
    except ValueError:
        raise ValueError("attempts must be a whole number") from None
    if parsed < 1:
        raise ValueError("attempts must be at least 1")
    return parsed

def _clean_part(value: str) -> str:
    value = value.strip()
    return "" if value.casefold() in {"", "none", "unamped", "—"} else value


def _bounded_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(0, min(10, parsed))


def _event_consumes_shot(event: ParsedEvent) -> bool:
    if event.kind != "combat":
        return False
    # Outgoing hits, misses and creature dodges consume one selected-setup use,
    # irrespective of damage magnitude. Incoming misses/damage/heals do not.
    return "damage" in event.payload or bool(event.payload.get("dodged") or event.payload.get("outgoing_miss"))


def _is_duplicate_log_line(previous_line: str | None, raw_line: str) -> bool:
    normalized = raw_line.strip()
    return bool(normalized) and normalized == previous_line


def _typeahead_match(values: tuple[str, ...] | list[str], prefix: str) -> str | None:
    """Return the first combobox value whose visible text starts with prefix."""
    normalized = prefix.casefold().strip()
    if not normalized:
        return None
    for value in values:
        if value.casefold().startswith(normalized):
            return value
    return None


def _attachment_names_by_category(catalog: Catalog, categories: set[str]) -> list[str]:
    return sorted(
        attachment.name
        for attachment in catalog.attachments.values()
        if attachment.category in categories
    )


def _attachment_in_categories(catalog: Catalog, name: str, categories: set[str]) -> bool:
    cleaned = _clean_part(name)
    if not cleaned:
        return True
    attachment = catalog.attachments.get(cleaned)
    return bool(attachment and attachment.category in categories)


def _return_pct(session: SessionSummary | None) -> float:
    if not session or session.hunting_cost <= 0:
        return 0.0
    return session.loot_value / session.hunting_cost * 100.0


def _lifetime_run_label(session: SessionSummary | None, mode: str = "USD") -> str:
    if not session:
        return "—"
    setup = session.loadout_name or session.activity.title()
    return f"{format_money(session.net_value, mode, signed=True)} · {setup}"


def _profit_state(net_value: float) -> str:
    if net_value > 0:
        return "good"
    if net_value < 0:
        return "bad"
    return "neutral"


def streamer_metrics(session: SessionSummary | None) -> dict[str, float | str]:
    if not session:
        return {
            "return_pct": 0.0,
            "loot": 0.0,
            "cost": 0.0,
            "net": 0.0,
            "damage": 0.0,
            "events": 0.0,
            "loadout": "No active session",
            "last_input_cost": 0.0,
            "manufacturing_attempt": 0.0,
            "manufacturing_attempts_total": 0.0,
        }
    return_pct = _return_pct(session)
    loadout = (session.loadout_name or "No loadout snapshot").splitlines()[0].strip()
    return {
        "return_pct": return_pct,
        "loot": session.loot_value,
        "cost": session.hunting_cost,
        "net": session.net_value,
        "damage": session.combat_damage,
        "events": float(session.events),
        "loadout": loadout,
        "last_input_cost": session.last_input_cost,
        "manufacturing_attempt": float(session.manufacturing_attempt),
        "manufacturing_attempts_total": float(session.manufacturing_attempts_total),
    }


def _parse_event_timestamp(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _kill_trend_points(store: Store, session_id: str | None, limit: int = 16, builder=None) -> list[dict[str, object]]:
    if not session_id:
        return []
    builder = builder if builder is not None else ActivityTrendBuilder(KILL_CLUSTER_WINDOW_SECONDS)
    with store.connect() as conn:
        conn.execute("BEGIN")
        revision = store._accounting_revision(conn, session_id)
        if builder.accounting_revision != revision:
            builder.reset(revision)
        # Stream one coherent membership revision; an append cursor alone cannot
        # notice a restored receipt whose original ID precedes that cursor.
        builder.extend(conn.execute(
            """SELECT id, timestamp, kind, payload FROM accounted_events
               WHERE session_id = ? AND id > ?
                 AND kind IN ('combat', 'loot', 'craft', 'manufacturing_offset', 'weapon', 'repair')
               ORDER BY id""", (session_id, builder.last_event_id),
        ))
    return builder.snapshot()


def _resume_revision(store: Store) -> tuple:
    """Cheap normal-writer guard; recorded events are append-only."""
    with store.connect() as conn:
        sessions = tuple(tuple(row) for row in conn.execute("SELECT * FROM sessions ORDER BY id"))
        events = tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM events").fetchone())
        accounting = tuple(tuple(row) for row in conn.execute("SELECT session_id,revision FROM event_accounting ORDER BY session_id"))
    return sessions, events, accounting


def _load_resume_view(store: Store, session_id: str) -> _ResumeView:
    with _held(store):
        return _read_resume_view(store, session_id)


def _read_resume_view(store: Store, session_id: str) -> _ResumeView:
    revision = _resume_revision(store)
    sessions = store.list_all_sessions()
    session = next((item for item in sessions if item.session_id == session_id), None)
    if session is None:
        raise ValueError("The resumed session no longer exists")
    skills = store.skill_gains_for_session(session_id)
    builder = ActivityTrendBuilder(KILL_CLUSTER_WINDOW_SECONDS)
    try:
        points = _kill_trend_points(store, session_id, builder=builder)
    except Exception:
        # Chart failures remain separate from ingestion/accounting, as on live ticks.
        logging.exception("Historical trend preparation failed")
        builder, points = None, []
    return _ResumeView(revision, session, sessions, store.lifetime_totals(sessions), skills, builder, points)


def _automatic_context_summary(name: str, *, details: bool = False) -> str:
    """Automatic popups are name previews, never chart documentation."""
    name = " ".join(str(name).split())
    name = name if len(name) <= 96 else name[:95].rstrip() + "…"
    return name + ("\nClick / Enter for current details" if details else "")


def _wrapped_line_count(text, font, width):
    """Reserve all wrapped lines without mapping/updating a native window."""
    lines = 0
    for paragraph in text.split("\n"):
        remaining = paragraph
        lines += 1
        while remaining and font.measure(remaining) > width:
            cut = 1
            while cut < len(remaining) and font.measure(remaining[:cut + 1]) <= width:
                cut += 1
            space = remaining.rfind(" ", 0, cut + 1)
            if space > 0:
                cut = space
            remaining = remaining[cut:].lstrip()
            lines += bool(remaining)
    return lines


def _healing_status_text(points, mode):
    parts = []
    pending = float(getattr(points, "healing_pending", 0.0))
    if pending:
        reasons = getattr(points, "healing_pending_reasons", {})
        legacy = any("legacy" in reason.casefold() or "no saved allocation context" in reason.casefold()
                     for reason in reasons)
        parts.append(f"Heal unassigned {format_chart_money(pending, mode)} — already in totals.")
        parts.append("Legacy included: no saved allocation context; not backfilled." if legacy else
                     "Allocation uncertain; not assigned to a loot group.")
        parts.append("No new charge or lost data.")
    unpriced = int(getattr(points, "unpriced_heals", 0))
    if unpriced:
        parts.append(f"{unpriced} heal obs. unpriced (cost unknown)")
    return "\n".join(parts)


def _healing_pending_detail(points, mode):
    """Group current reasons into a short, amount-preserving deliberate detail."""
    groups = {}
    for reason, amount in getattr(points, "healing_pending_reasons", {}).items():
        key = reason.casefold()
        if "legacy" in key or "no saved allocation context" in key:
            label = "Legacy context missing (no backfill)"
        elif "upcoming loot" in key:
            label = "Awaiting upcoming loot"
        elif "external" in key or "overlapping effect" in key:
            label = "External/overlapping effect evidence"
        elif "timing" in key or "timestamp" in key:
            label = "Incomplete/out-of-order timing"
        elif "boundary" in key or "equipment" in key:
            label = "Setup/accounting boundary"
        elif "shared" in key or "overlapping receipt" in key:
            label = "Shared receipt; no unique recipient"
        elif "hunting" in key or "activation context" in key:
            label = "No supported hunting recipient"
        else:
            label = "Other unresolved activation evidence"
        groups.setdefault(label, []).append(float(amount))
    return "\n".join(f"• {label}: {format_chart_money(math.fsum(amounts), mode)}"
                     for label, amounts in groups.items())


def _context_details(loadout, points, mode, *, plan=None, unpriced_groups=0,
                     allocation_error=False, trail_error=False, cost_display=None):
    """Deliberate, structured current facts instead of an automatic manual."""
    sections = ["SETUP\n" + _automatic_context_summary(loadout)]
    if plan:
        price = "unknown" if plan.per_attempt is None else format_chart_money(plan.per_attempt, mode)
        sections.append(
            f"CRAFT BATCH\n{_automatic_context_summary(plan.blueprint)}\n"
            f"{plan.observed}/{plan.attempts} groups observed · {price}/attempt\n"
            f"Booked {format_chart_money(plan.booked_cost, mode)} TT once; "
            f"returns {format_chart_money(plan.returns, mode)}.\n"
            f"{unpriced_groups} historical craft groups have no attributed input price.")
    if cost_display and cost_display.marker:
        sections.append(
            f"DISPLAY INPUTS\nBooked {format_chart_money(cost_display.booked, mode)} unchanged; "
            f"supported excess estimate {format_chart_money(cost_display.excess_estimate, mode)} added once for display only. "
            + (("* Partial: " + "; ".join(cost_display.incomplete_reasons) + ". "
                "Displayed net is before unresolved inputs, not final profit; overall rate unknown. ")
               if cost_display.incomplete else "≈ Uses saved input snapshots and matching recipe evidence. ")
            + "Observed groups do not reveal silent failures or exact clicks. No new charge.")
    sections.append("CHART\nInferred loot groups, not confirmed kills/clicks. "
                    "Cost/average use assigned inputs; fans show recorded estimates, not predictions. "
                    "Amber sections use their labeled local range.")
    pending = float(getattr(points, "attribution_pending", 0))
    if pending:
        sections.append(f"UNASSIGNED INPUT\n{format_chart_money(pending, mode)} already in totals, including unassigned healing.")
    healing = _healing_status_text(points, mode)
    if healing:
        breakdown = _healing_pending_detail(points, mode)
        sections.append("HEALING\n" + healing + ("\n" + breakdown if breakdown else ""))
    errors = []
    if allocation_error:
        errors.append("Allocation unavailable; chart uses raw receipt-interval inputs. Booked totals unchanged.")
    if trail_error:
        errors.append("Trail storage unavailable; totals and ordinary chart series unchanged.")
    if errors:
        sections.append("UNAVAILABLE\n" + "\n".join(errors))
    return "\n\n".join(sections)


def _streamer_trend_detail(points: list[dict[str, object]], mode: str = "PED") -> str:
    healing = _healing_status_text(points, mode)
    if not points:
        return "No observed groups yet\n" + (healing or "No supported group cost inferred")
    latest = points[-1]
    label = latest.get("label", f"K{latest['kill']}")
    pending = float(getattr(points, "attribution_pending", 0.0))
    shared = latest.get("kind") == "hunt" and bool(latest.get("cost_status"))
    if shared and (latest["cost"] is None or latest["profit"] is None):
        return (f"{label} · Cost unresolved · Return {format_chart_money(float(latest['loot']), mode)}\n"
                + (healing or f"Shared/unassigned {format_chart_money(pending, mode)} · booked totals unchanged"))
    if latest["cost"] is None or latest["profit"] is None:
        return (f"{label} · Cost unknown · Return {format_chart_money(float(latest['loot']), mode)}\n"
                + (healing or "Add input setup; no per-group profit inferred"))
    first = latest.get("first_point", len(points) == 1)
    change = ("first group" if first else "prev unresolved" if latest["delta_profit"] is None else
              "= unchanged" if not latest["delta_profit"] else
              f"{latest['trend_symbol']} {format_chart_money(float(latest['delta_profit']), mode, signed=True)} vs prev")
    if shared:
        basis = "~Neighbor cost" if latest.get("cost_basis") == "neighbors" else "~Cost"
        return (f"{label} · Est. profit {format_chart_money(float(latest['profit']), mode, signed=True)} · {change}\n"
                + f"{basis} {format_chart_money(float(latest['cost']), mode)}"
                + (f"\n{healing}" if healing else
                   f" · Shared/unassigned {format_chart_money(pending, mode)}"))
    scope = "Batch avg" if latest.get("kind") == "craft" else "Assigned avg"
    profit_label = "Est. profit" if latest.get("healing_cost", 0) else "Profit"
    detail = (f"{label} · {profit_label} {format_chart_money(float(latest['profit']), mode, signed=True)} · {change}\n"
              f"Cost {format_chart_money(float(latest['cost']), mode)} · {scope} {format_chart_money(float(latest['avg_cost']), mode)}")
    if healing:
        return detail + f"\n{healing}"
    if pending:
        detail += f" · Unassigned {format_chart_money(pending, mode)}"
    return detail


def _kill_trend_summary(points: list[dict[str, object]], mode: str = "PED") -> str:
    if not points:
        return ("No observed craft groups yet — configured inputs are booked." if getattr(points, "has_crafting", False) else
                "No completed kills yet — loot a mob to plot cost and returns; the summary shows net profit and delta per kill.") + (" · " + _healing_status_text(points, mode) if _healing_status_text(points, mode) else "")
    latest = points[-1]
    if latest["cost"] is None or latest["profit"] is None or latest.get("cost_status") or latest["delta_profit"] is None or _healing_status_text(points, mode):
        return _streamer_trend_detail(points, mode)
    profit = float(latest["profit"])
    cost = float(latest["cost"])
    avg_cost = float(latest["avg_cost"])
    delta_profit = float(latest["delta_profit"])
    if latest.get("first_point", len(points) == 1):
        trend_text = "first craft group" if latest.get("kind") == "craft" else "first kill"
    elif math.isclose(delta_profit, 0.0, abs_tol=1e-9):
        trend_text = "flat vs prev"
    else:
        trend_text = f"{format_chart_money(delta_profit, mode, signed=True)} vs prev"
    label = latest.get("label", f"K{int(latest['kill'])}")
    return (f"{label} • cost {format_chart_money(cost, mode)} • avg {format_chart_money(avg_cost, mode)} • "
            f"profit {format_chart_money(profit, mode, signed=True)} • {latest['trend_symbol']} {trend_text}")


def _kill_trend_badge(points: list[dict[str, object]], mode: str = "PED") -> str:
    if not points:
        return "Waiting for first craft group" if getattr(points, "has_crafting", False) else "Waiting for first kill"
    return _streamer_trend_detail(points, mode).splitlines()[0]



# Themed streamer HUD (all themes share one model/scene); see streamer_window.py.
from .streamer_window import StreamerWindow  # noqa: E402


def main() -> int:
    app = PedHunterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
