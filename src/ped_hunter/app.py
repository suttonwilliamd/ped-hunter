"""Modern Tkinter dashboard for PED Hunter.

The interface keeps LootNanny's core idea — live run tracking from Entropia chat
logs — but presents it as a cleaner local-first dashboard with obvious status,
summary cards, recent event streams, catalog search, and setup guidance.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import queue
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .catalog import Catalog, WeaponRecord
from .parser import ParsedEvent, is_conversion_output_item, parse_line
from .storage import LoadoutRecord, SessionSummary, Store


POLL_MS = 250
AMP_CATEGORIES = {"BLP Amp", "Energy Amp", "Melee Amp", "MF Amp"}
SCOPE_CATEGORIES = {"Scope"}
SIGHT_CATEGORIES = {"Sight"}
STREAMER_DEFAULT_WIDTH = 460
STREAMER_DEFAULT_HEIGHT = 245
STREAMER_MIN_WIDTH = 360
STREAMER_MIN_HEIGHT = 210


@dataclass(slots=True)
class MetricCard:
    frame: ttk.Frame
    value: tk.StringVar
    label: tk.StringVar


class PedHunterApp(tk.Tk):
    """Main PED Hunter desktop UI."""

    def __init__(self) -> None:
        super().__init__()
        self.title("PED Hunter")
        self.geometry("1180x720")
        self.minsize(980, 640)

        self.store = Store()
        self.catalog = Catalog.load()
        self.session_id: str | None = None
        self.last_size = 0
        self._last_ingested_log_line: str | None = None
        self.running = False
        self.current_log_path: Path | None = None

        self.streamer_window: StreamerWindow | None = None
        self._full_refresh_pending = False
        self._log_drain_pending = False
        self._log_worker: threading.Thread | None = None
        self._log_worker_stop = threading.Event()
        self._log_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.chat_path = tk.StringVar(value=str(_default_chat_log_path()))
        self.status_text = tk.StringVar(value="Ready — choose a chat log and start tracking")
        self.session_text = tk.StringVar(value="No active session")
        self.catalog_query = tk.StringVar(value="Frontier Rifle")
        blueprint_names = sorted(self.catalog.blueprints)
        self.crafting_blueprint = tk.StringVar(value=blueprint_names[0] if blueprint_names else "")
        self.crafting_attempts = tk.StringVar(value="1")
        self.crafting_preview = tk.StringVar(value="Select a blueprint to preview material TT cost.")
        self.active_loadout_title = tk.StringVar(value="No active setup")
        self.active_loadout_details = tk.StringVar(value="Configure and activate a hunting setup before starting for accurate costs.")

        self.active_loadout_text = tk.StringVar(value="No active setup — configure one before tracking")
        self.repair_radar_text = tk.StringVar(value="Repair radar: start a fresh run at 100% gun + amp TT")
        self.selected_session_text = tk.StringVar(value="Select a recent session.")
        self.hero_net = tk.StringVar(value="+0.00 PED")
        self.hero_return = tk.StringVar(value="0.00% Return")
        self.hero_loot = tk.StringVar(value="Loot 0.00 PED")
        self.hero_cost = tk.StringVar(value="Cost 0.00 PED")
        self.hero_events = tk.StringVar(value="Events 0")
        self.hero_state = tk.StringVar(value="Idle")
        self.hero_session = tk.StringVar(value="No active session — start a run to begin collecting data")
        self.skill_total_text = tk.StringVar(value="0.0000 XP")
        self.skill_proc_text = tk.StringVar(value="0 skill gains")
        self.skill_summary_text = tk.StringVar(value="No skill gains recorded for this session yet.")
        self.lifetime_summary_text = tk.StringVar(value="No completed runs yet — start tracking to build your lifetime stats.")
        self.lifetime_vars = {
            "total_cost": tk.StringVar(value="0.00 PED"),
            "total_loot": tk.StringVar(value="0.00 PED"),
            "total_net": tk.StringVar(value="+0.00 PED"),
            "overall_return": tk.StringVar(value="0.00%"),
            "total_events": tk.StringVar(value="0 events"),
            "avg_return": tk.StringVar(value="0.00% avg"),
            "best_run": tk.StringVar(value="—"),
            "worst_run": tk.StringVar(value="—"),
            "avg_profit": tk.StringVar(value="+0.00 PED/run"),
        }
        self.loadout_name = tk.StringVar(value="Starter rifle")
        self.loadout_weapon = tk.StringVar(value="Frontier Rifle")
        self.loadout_amp = tk.StringVar(value="None")
        self.loadout_scope = tk.StringVar(value="None")
        self.loadout_sight_1 = tk.StringVar(value="None")
        self.loadout_sight_2 = tk.StringVar(value="None")
        self.damage_enhancers = tk.StringVar(value="0")
        self.accuracy_enhancers = tk.StringVar(value="0")
        self.economy_enhancers = tk.StringVar(value="0")
        self.loadout_preview = tk.StringVar(value="Cost/shot: —")
        self.selected_loadout_id: int | None = None
        self.compact_density = tk.BooleanVar(value=True)
        self.top_area_expanded = tk.BooleanVar(value=True)
        self.top_toggle_text = tk.StringVar(value="Hide top")

        self.metric_cards: dict[str, MetricCard] = {}
        self._configure_theme()
        self._build_layout()
        self._refresh_all()

    def _configure_theme(self) -> None:
        bg = "#050711"
        panel = "#0b1020"
        panel_2 = "#101a2f"
        panel_3 = "#1a2740"
        border = "#2f405f"
        text = "#f3f8ff"
        muted = "#9aaec7"
        accent = "#2dd4bf"
        accent_soft = "#0f3438"
        good = "#22c55e"

        self.configure(bg=bg)
        style = ttk.Style(self)
        try:
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
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("Kicker.TLabel", background=bg, foreground=accent, font=("Segoe UI Semibold", 9))
        style.configure("Panel.TLabel", background=panel, foreground=text, font=("Segoe UI", 10))
        style.configure("PanelTitle.TLabel", background=panel, foreground="#d8fffb", font=("Segoe UI Semibold", 11))
        style.configure("PanelMuted.TLabel", background=panel, foreground=muted, font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background=panel_2, foreground=text, font=("Segoe UI Semibold", 19))
        style.configure("CardTitle.TLabel", background=panel_2, foreground=text, font=("Segoe UI Semibold", 11))
        style.configure("CardLabel.TLabel", background=panel_2, foreground=muted, font=("Segoe UI", 9))
        style.configure("HeroKicker.TLabel", background=panel_2, foreground=accent, font=("Segoe UI Semibold", 10))
        style.configure("HeroGood.TLabel", background=panel_2, foreground=accent, font=("Segoe UI Semibold", 36))
        style.configure("HeroBad.TLabel", background=panel_2, foreground="#fb923c", font=("Segoe UI Semibold", 36))
        style.configure("HeroNeutral.TLabel", background=panel_2, foreground=text, font=("Segoe UI Semibold", 36))
        style.configure("HeroReturnGood.TLabel", background=panel_2, foreground="#99f6e4", font=("Segoe UI Semibold", 20))
        style.configure("HeroReturnBad.TLabel", background=panel_2, foreground="#fed7aa", font=("Segoe UI Semibold", 20))
        style.configure("HeroReturnNeutral.TLabel", background=panel_2, foreground=muted, font=("Segoe UI Semibold", 20))
        style.configure("HeroSmallValue.TLabel", background="#0c1426", foreground=text, font=("Segoe UI Semibold", 12))
        style.configure("HeroSmallLabel.TLabel", background="#0c1426", foreground=accent, font=("Segoe UI Semibold", 8))
        style.configure("HeroSetup.TLabel", background=panel_2, foreground=muted, font=("Segoe UI", 10))
        style.configure("Pill.TLabel", background=accent_soft, foreground=accent, font=("Segoe UI Semibold", 9), padding=(12, 5))
        style.configure("Title.TLabel", background=bg, foreground=accent, font=("Segoe UI Semibold", 20))
        style.configure("Subtitle.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("Status.TLabel", background=panel, foreground=muted, font=("Segoe UI", 9))
        style.configure("Accent.TButton", background=accent, foreground="#031412", font=("Segoe UI Semibold", 9), padding=(12, 6), borderwidth=0, focusthickness=0)
        style.configure("Ghost.TButton", background=panel_3, foreground=text, font=("Segoe UI", 9), padding=(10, 6), borderwidth=0, focusthickness=0)
        style.configure("TButton", background=panel_3, foreground=text, padding=(9, 5), borderwidth=0, focusthickness=0)
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
        style.map("TButton", background=[("pressed", "#1f293d"), ("active", "#223049")], foreground=[("active", text)])
        style.configure("TEntry", fieldbackground="#0a0f1a", foreground=text, insertcolor=text, bordercolor=border, lightcolor=border, darkcolor=border, padding=(8, 5), borderwidth=1)
        style.configure("TCombobox", fieldbackground="#0a0f1a", foreground=text, arrowcolor=muted, bordercolor=border, lightcolor=border, darkcolor=border, padding=(8, 4), borderwidth=1)
        style.configure("TSpinbox", fieldbackground="#0a0f1a", foreground=text, arrowcolor=muted, bordercolor=border, lightcolor=border, darkcolor=border, padding=(7, 4), borderwidth=1)
        style.configure("Vertical.TScrollbar", background=panel_3, troughcolor=panel, borderwidth=0, arrowcolor=muted)
        style.configure("TNotebook", background=bg, borderwidth=0, tabmargins=(0, 0, 0, 0), lightcolor=bg, darkcolor=bg, bordercolor=bg)
        style.configure("TNotebook.Tab", background=bg, foreground=muted, padding=(16, 6), font=("Segoe UI Semibold", 9), borderwidth=0, relief="flat", lightcolor=bg, darkcolor=bg, bordercolor=bg)
        style.layout("TNotebook.Tab", [("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [("Notebook.label", {"side": "top", "sticky": ""})]})])
        style.map("TNotebook.Tab", background=[("selected", panel_2), ("active", "#101827")], foreground=[("selected", accent), ("active", text)])
        style.configure(
            "Treeview",
            background="#0a0f1a",
            fieldbackground="#0a0f1a",
            foreground=text,
            bordercolor=panel,
            rowheight=26,
            font=("Segoe UI", 9),
            borderwidth=0,
        )
        style.configure("Treeview.Heading", background=panel_3, foreground="#c7fffa", font=("Segoe UI Semibold", 9), borderwidth=0, padding=(6, 5))
        style.map("Treeview", background=[("selected", "#155e59")], foreground=[("selected", "#ffffff")])

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

    def _build_layout(self) -> None:
        self.root_frame = ttk.Frame(self, style="Root.TFrame", padding=(12, 10))
        root = self.root_frame
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        header = ttk.Frame(root, style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="◆ PED Hunter", style="Title.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(header, text="live profit radar", style="Subtitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(header, text="Compact", variable=self.compact_density, command=self._apply_density).grid(row=0, column=2, sticky="e", padx=(6, 6))
        ttk.Button(header, textvariable=self.top_toggle_text, command=self._toggle_top_area, style="Ghost.TButton").grid(row=0, column=3, sticky="e", padx=(0, 6))
        ttk.Button(header, text="Streamer UI", command=self._open_streamer_window, style="Ghost.TButton").grid(row=0, column=4, sticky="e", padx=(0, 6))
        ttk.Button(header, text="Refresh", command=self._refresh_all, style="Ghost.TButton").grid(row=0, column=5, sticky="e")

        self._build_session_hero(root, row=1)

        self.controls_panel = ttk.Frame(root, style="Panel.TFrame", padding=(10, 7))
        controls = self.controls_panel
        controls.grid(row=2, column=0, sticky="ew", pady=(7, 8))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Chat log", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(controls, textvariable=self.chat_path).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(controls, text="Browse", command=self._browse_log).grid(row=0, column=2, padx=(0, 8))
        self.status_label = ttk.Label(controls, textvariable=self.status_text, style="Status.TLabel")
        self.status_label.grid(row=0, column=3, sticky="e")

        notebook = ttk.Notebook(root)
        notebook.grid(row=3, column=0, sticky="nsew")
        self.dashboard_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        self.events_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        self.skills_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        self.loadouts_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        self.manufacturing_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        self.catalog_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        self.setup_tab = ttk.Frame(notebook, style="Root.TFrame", padding=(2, 14, 2, 2))
        notebook.add(self.dashboard_tab, text="Dashboard")
        notebook.add(self.events_tab, text="Events")
        notebook.add(self.skills_tab, text="Skills")
        notebook.add(self.loadouts_tab, text="Setups")
        notebook.add(self.manufacturing_tab, text="Manufacturing")
        notebook.add(self.catalog_tab, text="Catalog")
        notebook.add(self.setup_tab, text="Help")

        self._build_dashboard_tab()
        self._build_events_tab()
        self._build_skills_tab()
        self._build_loadouts_tab()
        self._build_manufacturing_tab()
        self._build_catalog_tab()
        self._build_setup_tab()
        self.bind("<Configure>", self._on_resize)
        self._apply_density()

    def _build_session_hero(self, parent: ttk.Frame, *, row: int) -> None:
        self.hero_panel = ttk.Frame(parent, style="Hero.TFrame", padding=(14, 10))
        hero = self.hero_panel
        hero.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        hero.columnconfigure(0, weight=0)
        hero.columnconfigure(1, weight=2)
        hero.columnconfigure(2, weight=1)
        ttk.Frame(hero, style="HeroAccent.TFrame", width=3).grid(row=0, column=0, sticky="ns", padx=(0, 10))

        self.hero_left = ttk.Frame(hero, style="Hero.TFrame")
        left = self.hero_left
        left.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        ttk.Label(left, text="LIVE RUN PULSE", style="HeroKicker.TLabel").grid(row=0, column=0, sticky="w")
        self.hero_session_label = ttk.Label(left, textvariable=self.hero_session, style="HeroSetup.TLabel", wraplength=540)
        self.hero_session_label.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        self.hero_net_label = ttk.Label(left, textvariable=self.hero_net, style="HeroNeutral.TLabel")
        self.hero_net_label.grid(row=2, column=0, sticky="w")
        self.hero_return_label = ttk.Label(left, textvariable=self.hero_return, style="HeroReturnNeutral.TLabel")
        self.hero_return_label.grid(row=3, column=0, sticky="w")
        self.hero_loadout_label = ttk.Label(left, textvariable=self.active_loadout_text, style="HeroSetup.TLabel", wraplength=540)
        self.hero_loadout_label.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.hero_repair_label = ttk.Label(left, textvariable=self.repair_radar_text, style="HeroSetup.TLabel", wraplength=540)
        self.hero_repair_label.grid(row=5, column=0, sticky="ew", pady=(2, 0))

        self.hero_right = ttk.Frame(hero, style="Hero.TFrame")
        right = self.hero_right
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure((0, 1, 2), weight=1, uniform="hero_stats")
        self._add_hero_stat(right, 0, self.hero_loot, "LOOT")
        self._add_hero_stat(right, 1, self.hero_cost, "COST")
        self._add_hero_stat(right, 2, self.hero_events, "EVENTS")

        actions = ttk.Frame(right, style="Hero.TFrame")
        actions.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(9, 0))
        self.start_button = ttk.Button(actions, text="Start", command=self.start, style="Accent.TButton")
        self.start_button.pack(side="left", padx=(0, 6))
        self.resume_button = ttk.Button(actions, text="Resume", command=self.resume_selected_session, style="Ghost.TButton")
        self.resume_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Label(actions, textvariable=self.hero_state, style="HeroSetup.TLabel").pack(side="left")

    def _add_hero_stat(self, parent: ttk.Frame, column: int, value: tk.StringVar, label: str) -> None:
        frame = ttk.Frame(parent, style="HeroInset.TFrame", padding=(9, 7))
        frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
        ttk.Frame(frame, style="AccentLine.TFrame", height=2).pack(fill="x", pady=(0, 5))
        ttk.Label(frame, text=label, style="HeroSmallLabel.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=value, style="HeroSmallValue.TLabel").pack(anchor="w", pady=(2, 0))

    def _toggle_top_area(self) -> None:
        expanded = not self.top_area_expanded.get()
        self.top_area_expanded.set(expanded)
        self.top_toggle_text.set("Hide top" if expanded else "Show top")
        if expanded:
            self.hero_panel.grid()
            self.controls_panel.grid()
        else:
            self.hero_panel.grid_remove()
            self.controls_panel.grid_remove()

    def _apply_density(self) -> None:
        compact = self.compact_density.get()
        root_pad = (12, 10) if compact else 18
        hero_pad = (14, 10) if compact else (22, 16)
        controls_pad = (10, 7) if compact else (16, 11)
        if hasattr(self, "root_frame"):
            self.root_frame.configure(padding=root_pad)
        if hasattr(self, "hero_panel"):
            self.hero_panel.configure(padding=hero_pad)
        if hasattr(self, "controls_panel"):
            self.controls_panel.configure(padding=controls_pad)
        self._configure_density_styles(compact)

    def _configure_density_styles(self, compact: bool) -> None:
        style = ttk.Style(self)
        if compact:
            style.configure("Treeview", rowheight=26, font=("Segoe UI", 9))
            style.configure("TNotebook.Tab", padding=(16, 6), font=("Segoe UI Semibold", 9))
            style.configure("Accent.TButton", padding=(12, 6), font=("Segoe UI Semibold", 9))
            style.configure("Ghost.TButton", padding=(10, 6), font=("Segoe UI", 9))
            style.configure("TButton", padding=(9, 5))
            style.configure("HeroNeutral.TLabel", font=("Segoe UI Semibold", 36))
            style.configure("HeroGood.TLabel", font=("Segoe UI Semibold", 36))
            style.configure("HeroBad.TLabel", font=("Segoe UI Semibold", 36))
            style.configure("HeroReturnNeutral.TLabel", font=("Segoe UI Semibold", 20))
            style.configure("HeroReturnGood.TLabel", font=("Segoe UI Semibold", 20))
            style.configure("HeroReturnBad.TLabel", font=("Segoe UI Semibold", 20))
        else:
            style.configure("Treeview", rowheight=32, font=("Segoe UI", 10))
            style.configure("TNotebook.Tab", padding=(22, 9), font=("Segoe UI Semibold", 10))
            style.configure("Accent.TButton", padding=(16, 9), font=("Segoe UI Semibold", 10))
            style.configure("Ghost.TButton", padding=(14, 9), font=("Segoe UI", 10))
            style.configure("TButton", padding=(12, 8))
            style.configure("HeroNeutral.TLabel", font=("Segoe UI Semibold", 48))
            style.configure("HeroGood.TLabel", font=("Segoe UI Semibold", 48))
            style.configure("HeroBad.TLabel", font=("Segoe UI Semibold", 48))
            style.configure("HeroReturnNeutral.TLabel", font=("Segoe UI Semibold", 26))
            style.configure("HeroReturnGood.TLabel", font=("Segoe UI Semibold", 26))
            style.configure("HeroReturnBad.TLabel", font=("Segoe UI Semibold", 26))

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        width = event.width
        wrap = max(280, min(620, width // 2))
        if hasattr(self, "hero_session_label"):
            self.hero_session_label.configure(wraplength=wrap)
            self.hero_loadout_label.configure(wraplength=wrap)
            self.hero_repair_label.configure(wraplength=wrap)
        if hasattr(self, "status_label"):
            if width < 1080:
                self.status_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(5, 0))
            else:
                self.status_label.grid(row=0, column=3, sticky="e")

    def _build_dashboard_tab(self) -> None:
        tab = self.dashboard_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=0)
        tab.rowconfigure(1, weight=5)
        tab.rowconfigure(2, weight=1)

        totals_panel = self._panel(tab, "Overall / Lifetime Totals", 0, 0)
        self._build_lifetime_totals(totals_panel)

        recent_panel = self._panel(tab, "Recent sessions", 1, 0)
        self.sessions_tree = self._tree(
            recent_panel,
            headings=("Started", "Setup", "Return", "Loot", "Cost", "Net", "Events", "Status"),
            columns=("started", "setup", "return", "loot", "cost", "net", "events", "status"),
        )
        self.sessions_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_session_selected())

        action_panel = self._panel(tab, "Selected session", 2, 0)
        command_card = ttk.Frame(action_panel, style="Card.TFrame", padding=(10, 8))
        command_card.grid(row=0, column=0, sticky="ew")
        command_card.columnconfigure(1, weight=1)
        ttk.Label(command_card, text="SESSION", style="CardLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(command_card, textvariable=self.selected_session_text, style="CardTitle.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(command_card, text="Resume", command=self.resume_selected_session, style="Accent.TButton").grid(row=0, column=2, sticky="e")

    def _build_lifetime_totals(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(tuple(range(5)), weight=1, uniform="lifetime")
        ttk.Label(parent, textvariable=self.lifetime_summary_text, style="PanelMuted.TLabel").grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 7))
        cards = (
            ("total_cost", "TOTAL TT IN", 0, 0),
            ("total_loot", "TOTAL TT OUT", 0, 1),
            ("total_net", "LIFETIME P/L", 0, 2),
            ("overall_return", "OVERALL RETURN", 0, 3),
            ("avg_profit", "AVG PROFIT / RUN", 0, 4),
            ("total_events", "EVENTS", 1, 0),
            ("avg_return", "AVG RETURN / RUN", 1, 1),
            ("best_run", "BEST RUN", 1, 2),
            ("worst_run", "WORST RUN", 1, 3),
        )
        for key, label, row, column in cards:
            columnspan = 2 if key == "worst_run" else 1
            self._add_lifetime_card(parent, key, label, row + 1, column, columnspan=columnspan)

    def _add_lifetime_card(self, parent: ttk.Frame, key: str, label: str, row: int, column: int, *, columnspan: int = 1) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(10, 8))
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=(0 if column == 0 else 8, 0), pady=(0 if row == 1 else 8, 0))
        ttk.Label(frame, text=label, style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=self.lifetime_vars[key], style="CardValue.TLabel").pack(anchor="w", pady=(3, 0))
        if key == "overall_return":
            ttk.Label(frame, text="Weighted: TT Out ÷ TT In", style="CardLabel.TLabel").pack(anchor="w", pady=(2, 0))
        elif key == "avg_return":
            ttk.Label(frame, text="Simple average of per-run %", style="CardLabel.TLabel").pack(anchor="w", pady=(2, 0))

    def _build_events_tab(self) -> None:
        tab = self.events_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=2)
        tab.rowconfigure(1, weight=3)
        chart_panel = self._panel(tab, "Loot event chart", 0, 0)
        self.loot_chart_canvas = tk.Canvas(
            chart_panel,
            bg=self.colors["panel_2"],
            highlightthickness=0,
            relief="flat",
            height=210,
        )
        self.loot_chart_canvas.grid(row=0, column=0, sticky="nsew")
        self.loot_chart_canvas.bind("<Configure>", lambda _event: self._draw_loot_chart())

        panel = self._panel(tab, "Live event stream", 1, 0)
        self.events_tree = self._tree(
            panel,
            columns=("time", "kind", "summary"),
            headings=("Time", "Kind", "Summary"),
        )

    def _build_skills_tab(self) -> None:
        tab = self.skills_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        summary_panel = self._panel(tab, "Session skill gain summary", 0, 0)
        summary_panel.columnconfigure(0, weight=1)
        summary_panel.columnconfigure(1, weight=1)
        ttk.Label(summary_panel, textvariable=self.skill_summary_text, style="PanelMuted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_skill_card(summary_panel, self.skill_total_text, "TOTAL SKILL GAIN", 1, 0)
        self._add_skill_card(summary_panel, self.skill_proc_text, "SKILL GAIN PROCS", 1, 1)

        table_panel = self._panel(tab, "Skill gains by skill", 1, 0)
        self.skills_tree = self._tree(
            table_panel,
            columns=("skill", "xp", "procs", "proc_pct"),
            headings=("Skill", "Value", "Procs", "Proc %"),
        )

    def _add_skill_card(self, parent: ttk.Frame, value: tk.StringVar, label: str, row: int, column: int) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(10, 8))
        frame.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        ttk.Label(frame, text=label, style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=value, style="CardValue.TLabel").pack(anchor="w", pady=(3, 0))

    def _build_loadouts_tab(self) -> None:
        tab = self.loadouts_tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        list_panel = self._panel(tab, "Saved setups", 0, 0)
        self.loadouts_tree = self._tree(
            list_panel,
            columns=("active", "name", "weapon", "amp", "enh", "cost"),
            headings=("Active", "Name", "Weapon", "Amp", "Enhancers", "Cost/shot"),
        )
        self.loadouts_tree.bind("<<TreeviewSelect>>", lambda _event: self._load_selected_loadout())

        editor = self._panel(tab, "Hunting setup builder", 0, 1)
        form = ttk.Frame(editor, style="Panel.TFrame")
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        weapon_names = sorted(self.catalog.weapons)
        amp_names = ["None"] + _attachment_names_by_category(self.catalog, AMP_CATEGORIES)
        scope_names = ["None"] + _attachment_names_by_category(self.catalog, SCOPE_CATEGORIES)
        sight_names = ["None"] + _attachment_names_by_category(self.catalog, SIGHT_CATEGORIES)
        weapon_combo = ttk.Combobox(form, textvariable=self.loadout_weapon, values=weapon_names, state="normal")
        amp_combo = ttk.Combobox(form, textvariable=self.loadout_amp, values=amp_names, state="normal")
        scope_combo = ttk.Combobox(form, textvariable=self.loadout_scope, values=scope_names, state="normal")
        sight_1_combo = ttk.Combobox(form, textvariable=self.loadout_sight_1, values=sight_names, state="normal")
        sight_2_combo = ttk.Combobox(form, textvariable=self.loadout_sight_2, values=sight_names, state="normal")
        for combo in (weapon_combo, amp_combo, scope_combo, sight_1_combo, sight_2_combo):
            _install_combobox_typeahead(combo, on_change=self._refresh_loadout_preview)

        rows = [
            ("Name", ttk.Entry(form, textvariable=self.loadout_name)),
            ("Weapon", weapon_combo),
            ("Amplifier", amp_combo),
            ("Scope", scope_combo),
            ("Sight 1", sight_1_combo),
            ("Sight 2", sight_2_combo),
            ("Damage enhancers", ttk.Spinbox(form, from_=0, to=10, textvariable=self.damage_enhancers, width=8)),
            ("Accuracy enhancers", ttk.Spinbox(form, from_=0, to=10, textvariable=self.accuracy_enhancers, width=8)),
            ("Economy enhancers", ttk.Spinbox(form, from_=0, to=10, textvariable=self.economy_enhancers, width=8)),
        ]
        for r, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label, style="PanelMuted.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=5)
            widget.grid(row=r, column=1, sticky="ew", pady=5)
            if hasattr(widget, "bind"):
                widget.bind("<KeyRelease>", lambda _event: self._refresh_loadout_preview())
                widget.bind("<<ComboboxSelected>>", lambda _event: self._refresh_loadout_preview())

        ttk.Label(form, textvariable=self.loadout_preview, style="Panel.TLabel", font=("Segoe UI", 11, "bold")).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(12, 8))
        buttons = ttk.Frame(form, style="Panel.TFrame")
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="Save & activate", command=lambda: self._save_loadout(make_active=True), style="Accent.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Save", command=lambda: self._save_loadout(make_active=False)).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Delete", command=self._delete_selected_loadout).pack(side="left")

        help_text = tk.Text(
            editor,
            height=8,
            wrap="word",
            bg=self.colors["panel_2"],
            fg=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 9),
            padx=12,
            pady=10,
        )
        help_text.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        help_text.insert(
            "1.0",
            "Hunting setups are the source of truth for interpreting hunt logs: weapon + amp + scope/sights + enhancers define the cost model. "
            "PED Hunter keeps the active setup visible in the cockpit, so you do not choose hunt/craft/mine separately when starting a session. "
            "Amplifier, scope, and sight selectors are category-specific so items like ZX Eagle Eye only appear under Scope. "
            "Damage enhancers increase weapon ammo/decay by 10% each; economy enhancers reduce weapon ammo/decay by 1% each; attachments add their own burn/decay.",
        )
        help_text.configure(state="disabled")
        self._refresh_loadout_preview()

    def _build_manufacturing_tab(self) -> None:
        tab = self.manufacturing_tab
        tab.columnconfigure(0, weight=2)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        builder = self._panel(tab, "Blueprint material cost", 0, 0)
        form = ttk.Frame(builder, style="Panel.TFrame")
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        blueprint_names = sorted(self.catalog.blueprints)
        blueprint_combo = ttk.Combobox(form, textvariable=self.crafting_blueprint, values=blueprint_names, state="normal")
        _install_combobox_typeahead(blueprint_combo, on_change=self._refresh_crafting_preview)
        attempts_spin = ttk.Spinbox(form, from_=1, to=1000000, textvariable=self.crafting_attempts, width=10, command=self._refresh_crafting_preview)
        blueprint_combo.bind("<KeyRelease>", lambda _event: self._refresh_crafting_preview())
        blueprint_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_crafting_preview())
        attempts_spin.bind("<KeyRelease>", lambda _event: self._refresh_crafting_preview())

        ttk.Label(form, text="Blueprint", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        blueprint_combo.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="Attempts", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        attempts_spin.grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(form, textvariable=self.crafting_preview, style="Panel.TLabel", font=("Segoe UI", 11, "bold"), wraplength=620).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        ttk.Button(form, text="Add material cost to active session", command=self._add_crafting_cost_to_session, style="Accent.TButton").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        help_panel = self._panel(tab, "How manufacturing offsets loot", 0, 1)
        help_text = tk.Text(
            help_panel,
            height=14,
            wrap="word",
            bg=self.colors["panel_2"],
            fg=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 9),
            padx=12,
            pady=10,
        )
        help_text.grid(row=0, column=0, sticky="nsew")
        help_text.insert(
            "1.0",
            "Choose a blueprint and enter the number of manufacturing attempts. PED Hunter totals the TT value of the required materials per click, multiplies by attempts, and records that input cost as a crafting event on the active session.\n\n"
            "This mirrors LootNanny's manufacturing flow: crafting returns can still be parsed as loot/output, while the material spend is added to session cost so return %, net PED, streamer overlay, and lifetime totals stay honest.",
        )
        help_text.configure(state="disabled")
        self._refresh_crafting_preview()

    def _build_catalog_tab(self) -> None:
        tab = self.catalog_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        search = ttk.Frame(tab, style="Panel.TFrame", padding=12)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="Weapon lookup", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(search, textvariable=self.catalog_query).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(search, text="Search", command=self._search_catalog, style="Accent.TButton").grid(row=0, column=2)

        panel = self._panel(tab, "Catalog results", 1, 0)
        self.catalog_tree = self._tree(
            panel,
            columns=("name", "category", "ammo", "decay", "cost", "aliases"),
            headings=("Name", "Category", "Ammo", "Decay", "Cost/shot", "Aliases"),
        )
        self._search_catalog()

    def _build_setup_tab(self) -> None:
        tab = self.setup_tab
        tab.columnconfigure(0, weight=1)
        panel = ttk.Frame(tab, style="Panel.TFrame", padding=18)
        panel.grid(row=0, column=0, sticky="nsew")
        text = tk.Text(
            panel,
            height=20,
            wrap="word",
            bg=self.colors["panel_2"],
            fg=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 10),
            padx=16,
            pady=14,
        )
        text.pack(fill="both", expand=True)
        text.insert(
            "1.0",
            "Getting started\n\n"
            "1. In Entropia Universe, enable chat logging.\n"
            "2. Select your chat.log above. The default guess is shown automatically.\n"
            "3. Create or activate a setup; its configuration determines how the log is interpreted.\n"
            "4. Start a new session or select an older session and Resume Selected.\n"
            "5. PED Hunter parses new lines and writes events to a local SQLite database under AppData.\n\n"
            "Modernization notes\n\n"
            "LootNanny used PyQt tabs for loot, analysis, skills, combat, crafting, Twitch, config, and streamer views. "
            "PED Hunter starts with the same useful backbone but simplifies the first-run experience into a dashboard, "
            "event stream, catalog lookup, and setup guide. Future passes can add dedicated crafting, loadout, markup, "
            "and streamer panels without crowding the main cockpit.",
        )
        text.configure(state="disabled")

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
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 8))
        panel.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0), pady=(0 if row == 0 else 8, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text=title, style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 7))
        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        return body

    def _tree(self, parent: ttk.Frame, *, columns: tuple[str, ...], headings: tuple[str, ...]) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse", style="Treeview")
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        tree.configure(yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        tree.tag_configure("odd", background="#0d1421")
        tree.tag_configure("even", background="#0a0f1a")
        for col, heading in zip(columns, headings):
            tree.heading(col, text=heading)
            width = 170 if col in {"summary", "setup", "name", "weapon", "aliases"} else 118
            tree.column(col, width=width, minwidth=82, stretch=True, anchor="w")
        return tree

    def _refresh_loadout_preview(self) -> None:
        try:
            loadout = self._loadout_from_form()
        except ValueError as exc:
            self.loadout_preview.set(f"Loadout needs attention: {exc}")
            return
        self.loadout_preview.set(
            f"Ammo burn: {loadout.ammo_burn} • Decay: {loadout.decay:.5f} PED • Cost/shot: {loadout.cost_per_shot:.5f} PED"
        )

    def _loadout_from_form(self) -> LoadoutRecord:
        name = self.loadout_name.get().strip()
        if not name:
            raise ValueError("name is required")
        weapon = self.catalog.find_weapon(self.loadout_weapon.get().strip())
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

    def _save_loadout(self, *, make_active: bool) -> None:
        try:
            loadout = self._loadout_from_form()
        except ValueError as exc:
            messagebox.showerror("PED Hunter", str(exc))
            return
        loadout_id = self.store.save_loadout(loadout, make_active=make_active)
        self.selected_loadout_id = loadout_id
        self.status_text.set(f"Saved loadout: {loadout.name}")
        self._refresh_loadouts()
        self._refresh_all()

    def _delete_selected_loadout(self) -> None:
        if self.selected_loadout_id is None:
            return
        self.store.delete_loadout(self.selected_loadout_id)
        self.selected_loadout_id = None
        self.status_text.set("Deleted loadout")
        self._refresh_loadouts()
        self._refresh_all()

    def _load_selected_loadout(self) -> None:
        selection = self.loadouts_tree.selection()
        if not selection:
            return
        values = self.loadouts_tree.item(selection[0], "values")
        if not values:
            return
        loadout_id = int(self.loadouts_tree.set(selection[0], "id")) if "id" in self.loadouts_tree["columns"] else None
        for loadout in self.store.list_loadouts():
            if loadout.name == values[1]:
                self._populate_loadout_form(loadout)
                return
        if loadout_id is not None:
            self.selected_loadout_id = loadout_id

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
        self._refresh_loadout_preview()

    def _refresh_loadouts(self) -> None:
        self.loadouts_tree.delete(*self.loadouts_tree.get_children())
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
            self.active_loadout_details.set(f"{active.weapon} • {attachments} • {active.cost_per_shot:.5f} PED/shot")
            self.active_loadout_text.set(f"Setup: {active.name} · {active.cost_per_shot:.5f} PED/shot")
        else:
            self.active_loadout_title.set("No active setup")
            self.active_loadout_details.set("Activate a hunting setup before starting. This replaces the old hunt/craft/mine dropdown.")
            self.active_loadout_text.set("No active setup — configure one before tracking for accurate costs")
        for index, loadout in enumerate(self.store.list_loadouts()):
            self.loadouts_tree.insert(
                "",
                "end",
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    "✓" if loadout.active else "",
                    loadout.name,
                    loadout.weapon,
                    loadout.amp or "—",
                    f"D{loadout.damage_enhancers}/A{loadout.accuracy_enhancers}/E{loadout.economy_enhancers}",
                    f"{loadout.cost_per_shot:.5f}",
                ),
            )

    def _browse_log(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Entropia chat.log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.chat_path.set(path)

    def _open_streamer_window(self) -> None:
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.lift()
            self.streamer_window.focus_force()
            return
        self.streamer_window = StreamerWindow(self)
        self.streamer_window.update_from_session(self.store.get_current_session())

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
        self._full_refresh_pending = False
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
        session = self.store.get_session(session_id)
        if not session:
            messagebox.showerror("PED Hunter", "That session could not be found in local history.")
            self._refresh_all()
            return
        if session.ended_at is not None:
            if not messagebox.askyesno(
                "Resume ended session?",
                "PED Hunter will reopen this session and append only new chat.log events from this point forward.",
            ):
                return
            self.store.resume_session(session_id)

        self.current_log_path = path
        self._last_ingested_log_line = None
        self._full_refresh_pending = False
        self.session_id = session_id
        self.last_size = path.stat().st_size
        self.running = True
        self.start_button.configure(state="disabled")
        self.resume_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_text.set(f"Resumed {session_id}; tracking new lines from {path.name}")
        self._refresh_all()
        self._start_log_worker()

    def _selected_session_id(self) -> str | None:
        selection = self.sessions_tree.selection()
        return selection[0] if selection else None

    def _resume_session_id(self) -> str | None:
        """Return the explicitly selected session, or default to the newest recent session."""
        selected = self._selected_session_id()
        if selected:
            return selected
        children = self.sessions_tree.get_children()
        if children:
            return children[0]
        sessions = self.store.list_recent_sessions(1)
        return sessions[0].session_id if sessions else None

    def _on_session_selected(self) -> None:
        session_id = self._selected_session_id()
        if not session_id:
            self.selected_session_text.set("Select a recent session.")
            return
        session = self.store.get_session(session_id)
        if not session:
            self.selected_session_text.set("Selected session is no longer available.")
            return
        status = "active" if session.ended_at is None else f"ended {session.ended_at}"
        setup = session.loadout_name or session.activity.title()
        return_pct = _return_pct(session)
        self.selected_session_text.set(
            f"{setup} • {status}\n"
            f"Started {session.started_at} • {session.events} events • return {return_pct:.2f}% • net {session.net_value:+.2f} PED"
        )
    def stop(self) -> None:
        if not self.running:
            return
        self._stop_log_worker()
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
            args=(self.current_log_path, self.session_id, self.last_size, stop_event),
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
        self.after(100, self._drain_log_queue)

    def _drain_log_queue(self) -> None:
        self._log_drain_pending = False
        parsed_total = 0
        while True:
            try:
                kind, payload = self._log_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "parsed":
                parsed_total += int(payload)
            elif kind == "error":
                self.status_text.set(str(payload))
            elif kind == "status":
                self.status_text.set(str(payload))
        if parsed_total:
            self.status_text.set(f"Parsed {parsed_total} new event{'s' if parsed_total != 1 else ''}")
            self._refresh_live_views()
            self._schedule_full_refresh()
        worker = self._log_worker
        if self.running and worker and worker.is_alive():
            self._schedule_log_drain()

    def _log_worker_loop(self, path: Path, session_id: str, start_size: int, stop_event: threading.Event) -> None:
        last_size = start_size
        last_ingested_log_line: str | None = None
        while not stop_event.is_set():
            try:
                size = path.stat().st_size
                if size < last_size:
                    last_size = 0
                parsed = 0
                if size > last_size:
                    with path.open("r", encoding="utf-8", errors="ignore") as fh:
                        fh.seek(last_size)
                        lines = fh.readlines()
                        last_size = fh.tell()
                    parsed, last_ingested_log_line = self._process_lines(lines, session_id, last_ingested_log_line)
                if parsed:
                    self._log_queue.put(("parsed", parsed))
            except Exception as exc:  # pragma: no cover - defensive UI guard
                self._log_queue.put(("error", f"Tracking error: {exc}"))
            if stop_event.wait(POLL_MS / 1000.0):
                break

    def _process_lines(
        self,
        lines: list[str],
        session_id: str,
        last_ingested_log_line: str | None,
    ) -> tuple[int, str | None]:
        parsed = 0
        active_loadout = self.store.get_active_loadout()
        if active_loadout:
            active_loadout = with_repair_estimates(self.catalog, active_loadout)
        for raw in lines:
            if _is_duplicate_log_line(last_ingested_log_line, raw):
                continue
            normalized_raw = raw.strip()
            event = parse_line(raw)
            if not event:
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
        try:
            attempts = _positive_int(self.crafting_attempts.get())
            per_attempt, _materials = calculate_blueprint_material_cost(self.catalog, self.crafting_blueprint.get())
        except ValueError as exc:
            self.crafting_preview.set(f"Blueprint needs attention: {exc}")
            return
        total = per_attempt * attempts
        self.crafting_preview.set(f"Material TT: {per_attempt:.2f} PED/attempt • {attempts:,} attempts = {total:.2f} PED input")

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
                "kind": "craft",
                "raw_message": f"Manufacturing cost: {attempts} x {blueprint_name} = {total:.2f} PED",
                "payload": {
                    "blueprint": blueprint_name,
                    "attempts": attempts,
                    "cost_per_attempt": per_attempt,
                    "total_cost": total,
                    "materials": [
                        {"name": name, "quantity": quantity, "tt_value": tt_value, "total": line_total}
                        for name, quantity, tt_value, line_total in materials
                    ],
                },
            },
        )
        self.status_text.set(f"Added {total:.2f} PED manufacturing cost for {attempts:,} attempt{'s' if attempts != 1 else ''}")
        self._refresh_all()

    def _schedule_full_refresh(self) -> None:
        if self._full_refresh_pending:
            return
        self._full_refresh_pending = True
        self.after(500, self._run_full_refresh)

    def _run_full_refresh(self) -> None:
        self._full_refresh_pending = False
        if not self.running and self.session_id is None:
            return
        self._refresh_all()

    def _refresh_live_views(self) -> None:
        current = self.store.get_current_session()
        display = self._display_session(current, [])
        self._refresh_metrics(display, [])
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.update_from_session(display)

    def _refresh_all(self) -> None:
        current = self.store.get_current_session()
        sessions = self.store.list_recent_sessions(20)
        display = self._display_session(current, sessions)
        self._refresh_metrics(display, sessions)

        self._refresh_lifetime_totals()

        self._refresh_sessions(sessions)

        self._refresh_events(display.session_id if display else None)
        self._refresh_skills(display.session_id if display else None)
        self._refresh_loot_chart(display.session_id if display else None)
        self._refresh_loadouts()
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.update_from_session(display)

    def _display_session(self, current: SessionSummary | None, sessions: list[SessionSummary]) -> SessionSummary | None:
        if self.session_id:
            resumed_or_active = self.store.get_session(self.session_id)
            if resumed_or_active:
                return resumed_or_active
        return current or (sessions[0] if sessions else None)

    def _refresh_metrics(self, session: SessionSummary | None, sessions: list[SessionSummary]) -> None:
        state = "Live" if self.running else "Idle"
        self.hero_state.set(state)
        if not session:
            self.session_text.set("No sessions yet — start a run to begin collecting data")
            self.hero_session.set("No active session — start a run to begin collecting data")
            self.hero_net.set("+0.00 PED")
            self.hero_return.set("0.00% Return")
            self.hero_loot.set("Loot 0.00 PED")
            self.hero_cost.set("Cost 0.00 PED")
            self.hero_events.set("Events 0")
            self.repair_radar_text.set("Repair radar: start a fresh run at 100% gun + amp TT")
            self._set_hero_profit_style("neutral")
            return
        status = "active" if session.ended_at is None else "ended"
        setup = f"Hunt · {session.loadout_name}" if session.loadout_name else session.activity.title()
        return_pct = _return_pct(session)
        self.session_text.set(f"{session.session_id} • {setup} • {status} • started {session.started_at}")
        self.hero_session.set(f"{setup} • {status} • started {session.started_at}")
        self.hero_net.set(f"{session.net_value:+.2f} PED")
        self.hero_return.set(f"{return_pct:.2f}% Return")
        self.hero_loot.set(f"Loot {session.loot_value:.2f} PED")
        self.hero_cost.set(f"Cost {session.hunting_cost:.2f} PED")
        self.hero_events.set(f"Events {session.events}")
        self.repair_radar_text.set(format_repair_radar(session))
        self._set_hero_profit_style(_profit_state(session.net_value))

    def _refresh_lifetime_totals(self) -> None:
        totals = self.store.lifetime_totals()
        if totals.session_count == 0:
            self.lifetime_summary_text.set("No completed runs yet — start tracking to build your lifetime stats.")
            self._set_empty_lifetime_totals()
            return
        active_note = " · includes active run" if totals.active_count else ""
        self.lifetime_summary_text.set(
            f"{totals.session_count} stored run{'s' if totals.session_count != 1 else ''}{active_note} · weighted by PED input"
        )
        self.lifetime_vars["total_cost"].set(f"{totals.total_cost:.2f} PED")
        self.lifetime_vars["total_loot"].set(f"{totals.total_loot:.2f} PED")
        self.lifetime_vars["total_net"].set(f"{totals.total_net:+.2f} PED")
        self.lifetime_vars["overall_return"].set(f"{totals.overall_return_pct:.2f}%")
        self.lifetime_vars["total_events"].set(f"{totals.total_events} events")
        self.lifetime_vars["avg_return"].set(f"{totals.avg_return_pct:.2f}% avg")
        self.lifetime_vars["avg_profit"].set(f"{totals.avg_profit_per_run:+.2f} PED/run")
        self.lifetime_vars["best_run"].set(_lifetime_run_label(totals.best_session))
        self.lifetime_vars["worst_run"].set(_lifetime_run_label(totals.worst_session))

    def _set_empty_lifetime_totals(self) -> None:
        self.lifetime_vars["total_cost"].set("0.00 PED")
        self.lifetime_vars["total_loot"].set("0.00 PED")
        self.lifetime_vars["total_net"].set("+0.00 PED")
        self.lifetime_vars["overall_return"].set("0.00%")
        self.lifetime_vars["total_events"].set("0 events")
        self.lifetime_vars["avg_return"].set("0.00% avg")
        self.lifetime_vars["best_run"].set("—")
        self.lifetime_vars["worst_run"].set("—")
        self.lifetime_vars["avg_profit"].set("+0.00 PED/run")

    def _set_hero_profit_style(self, state: str) -> None:
        suffix = {"good": "Good", "bad": "Bad"}.get(state, "Neutral")
        if hasattr(self, "hero_net_label"):
            self.hero_net_label.configure(style=f"Hero{suffix}.TLabel")
        if hasattr(self, "hero_return_label"):
            self.hero_return_label.configure(style=f"HeroReturn{suffix}.TLabel")

    def _refresh_sessions(self, sessions: list[SessionSummary]) -> None:
        previous_selection = self._selected_session_id()
        self.sessions_tree.delete(*self.sessions_tree.get_children())
        for index, session in enumerate(sessions):
            return_pct = _return_pct(session)
            setup = f"Hunt · {session.loadout_name}" if session.loadout_name else session.activity.title()
            self.sessions_tree.insert(
                "",
                "end",
                iid=session.session_id,
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    session.started_at,
                    setup,
                    f"{return_pct:.2f}%",
                    f"{session.loot_value:.2f} PED",
                    f"{session.hunting_cost:.2f} PED",
                    f"{session.net_value:+.2f} PED",
                    session.events,
                    "active" if session.ended_at is None else "ended",
                ),
            )

        if not sessions:
            self.selected_session_text.set("No saved sessions yet.")
            return

        selected = previous_selection if previous_selection in {session.session_id for session in sessions} else sessions[0].session_id
        self.sessions_tree.selection_set(selected)
        self.sessions_tree.focus(selected)
        self._on_session_selected()

    def _refresh_events(self, session_id: str | None) -> None:
        self.events_tree.delete(*self.events_tree.get_children())
        if not session_id:
            return
        for index, row in enumerate(_recent_events(self.store, session_id, limit=80)):
            self.events_tree.insert("", "end", tags=("even" if index % 2 == 0 else "odd",), values=(row["timestamp"] or "", row["kind"], _summarize_event(row)))

    def _refresh_skills(self, session_id: str | None) -> None:
        self.skills_tree.delete(*self.skills_tree.get_children())
        if not session_id:
            self.skill_total_text.set("0.0000 XP")
            self.skill_proc_text.set("0 skill gains")
            self.skill_summary_text.set("No session selected — start or select a run to see skill gains.")
            return

        gains = self.store.skill_gains_for_session(session_id)
        total_xp = sum(gain.xp for gain in gains)
        total_procs = sum(gain.procs for gain in gains)
        self.skill_total_text.set(f"{total_xp:.4f} XP")
        self.skill_proc_text.set(f"{total_procs} skill gain{'s' if total_procs != 1 else ''}")
        if gains:
            self.skill_summary_text.set(
                f"LootNanny-style per-session totals for {len(gains)} skill{'s' if len(gains) != 1 else ''}, sorted by XP gained."
            )
        else:
            self.skill_summary_text.set("No skill gains recorded for this session yet.")
        for index, gain in enumerate(gains):
            self.skills_tree.insert(
                "",
                "end",
                tags=("even" if index % 2 == 0 else "odd",),
                values=(gain.skill, f"{gain.xp:.4f}", gain.procs, f"{gain.proc_pct:.0f}%"),
            )

    def _refresh_loot_chart(self, session_id: str | None) -> None:
        self.loot_chart_points = _loot_event_points(self.store, session_id, limit=160) if session_id else []
        self._draw_loot_chart()

    def _draw_loot_chart(self) -> None:
        canvas = self.loot_chart_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        pad_left = 56
        pad_right = 18
        pad_top = 22
        pad_bottom = 38
        plot_w = max(width - pad_left - pad_right, 1)
        plot_h = max(height - pad_top - pad_bottom, 1)
        colors = self.colors
        canvas.create_rectangle(0, 0, width, height, fill=colors["panel_2"], outline="")
        points = getattr(self, "loot_chart_points", [])
        if not points:
            canvas.create_text(
                width / 2,
                height / 2,
                text="No loot events yet — start a run and loot events will plot here.",
                fill=colors["muted"],
                font=("Segoe UI", 10),
                width=max(width - 40, 100),
            )
            return

        max_value = max(value for _, value, _ in points) or 1.0
        tick_values = [0, max_value / 2, max_value]
        for tick in tick_values:
            y = pad_top + plot_h - (tick / max_value * plot_h)
            canvas.create_line(pad_left, y, width - pad_right, y, fill="#1f2a3d")
            canvas.create_text(pad_left - 8, y, text=f"{tick:.2f}", anchor="e", fill=colors["muted"], font=("Segoe UI", 8))
        canvas.create_text(10, pad_top - 4, text="PED", anchor="w", fill=colors["muted"], font=("Segoe UI", 8, "bold"))

        count = len(points)
        step = plot_w / max(count - 1, 1)
        radius = 4 if count < 60 else 3
        line_points: list[float] = []
        for index, (_timestamp, value, label) in enumerate(points):
            x = pad_left + (step * index if count > 1 else plot_w / 2)
            y = pad_top + plot_h - (value / max_value * plot_h)
            line_points.extend([x, y])
            canvas.create_line(x, pad_top + plot_h, x, y, fill="#075985", width=2)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=colors["accent"], outline="#e0f2fe")
            if count <= 28:
                canvas.create_text(x, pad_top + plot_h + 8, text=str(index + 1), anchor="n", fill=colors["muted"], font=("Segoe UI", 8))
            if value == max_value:
                canvas.create_text(x, max(y - 10, 8), text=f"{value:.2f}", anchor="s", fill=colors["text"], font=("Segoe UI", 8, "bold"))
        if len(line_points) >= 4:
            canvas.create_line(*line_points, fill="#7dd3fc", width=2, smooth=False)
        total = sum(value for _, value, _ in points)
        canvas.create_text(
            width - pad_right,
            height - 12,
            text=f"{count} loot events • {total:.2f} PED total • latest: {points[-1][1]:.2f} PED {points[-1][2]}",
            anchor="e",
            fill=colors["muted"],
            font=("Segoe UI", 9),
        )

    def _search_catalog(self) -> None:
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        query = self.catalog_query.get().strip()
        matches: list[WeaponRecord] = []
        if query:
            exact = self.catalog.find_weapon(query)
            if exact:
                matches.append(exact)
            q = query.casefold()
            for weapon in self.catalog.weapons.values():
                if len(matches) >= 50:
                    break
                if weapon in matches:
                    continue
                if q in weapon.name.casefold() or any(q in alias.casefold() for alias in weapon.aliases):
                    matches.append(weapon)
        else:
            matches = list(self.catalog.weapons.values())[:50]

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


class StreamerWindow(tk.Toplevel):
    """Always-on-top compact overlay inspired by LootNanny's streamer window."""

    def __init__(self, app: PedHunterApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("PED Hunter Streamer UI")
        self.configure(bg="#020617")
        self.attributes("-topmost", True)
        self.overrideredirect(True)
        self.geometry(f"{STREAMER_DEFAULT_WIDTH}x{STREAMER_DEFAULT_HEIGHT}+120+120")
        self.minsize(STREAMER_MIN_WIDTH, STREAMER_MIN_HEIGHT)
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int, int] | None = None
        self.vars = {
            "net_big": tk.StringVar(value="+0.00 PED"),
            "return": tk.StringVar(value="0.00% Return"),
            "loot": tk.StringVar(value="Loot 0.00 PED"),
            "cost": tk.StringVar(value="Cost 0.00 PED"),
            "events": tk.StringVar(value="Events 0"),
            "damage": tk.StringVar(value="Damage 0.0"),
            "loadout": tk.StringVar(value="No active loadout"),
            "durability": tk.StringVar(value="Durability —"),
        }
        self.durability_pct = 0.0
        self.durability_color = "#111827"
        self._build()
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self) -> None:
        self.outer = tk.Frame(self, bg="#020617", highlightbackground=self.app.colors["accent"], highlightthickness=2, padx=14, pady=10)
        self.outer.pack(fill="both", expand=True)
        top = tk.Frame(self.outer, bg="#020617")
        top.pack(fill="x")
        tk.Label(top, text="◆ PED HUNTER", bg="#020617", fg=self.app.colors["accent"], font=("Segoe UI", 8, "bold")).pack(side="left")
        close = tk.Label(top, text=" × ", bg="#0f1726", fg="#94a3b8", font=("Segoe UI", 10, "bold"), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _event: self.destroy())
        close.bind("<Enter>", lambda _event: close.configure(bg="#1f2937", fg=self.app.colors["bad"]))
        close.bind("<Leave>", lambda _event: close.configure(bg="#0f1726", fg="#94a3b8"))

        self.net_label = tk.Label(self.outer, textvariable=self.vars["net_big"], bg="#020617", fg="#e5edf8", font=("Segoe UI", 28, "bold"))
        self.net_label.pack(anchor="w", pady=(4, 0))
        self.return_label = tk.Label(self.outer, textvariable=self.vars["return"], bg="#020617", fg="#94a3b8", font=("Segoe UI", 16, "bold"))
        self.return_label.pack(anchor="w")

        stats = tk.Frame(self.outer, bg="#020617")
        stats.pack(fill="x", pady=(8, 0))
        for col, key in enumerate(("loot", "cost", "events")):
            stats.columnconfigure(col, weight=1)
            tk.Label(stats, textvariable=self.vars[key], bg="#0f1726", fg="#e5edf8", font=("Segoe UI", 9, "bold"), padx=8, pady=7).grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))

        durability = tk.Frame(self.outer, bg="#020617")
        durability.pack(fill="x", pady=(8, 0))
        tk.Label(durability, textvariable=self.vars["durability"], bg="#020617", fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.durability_bar = tk.Canvas(durability, height=14, bg="#020617", highlightthickness=0)
        self.durability_bar.pack(fill="x", pady=(3, 0))
        self.durability_bar.bind("<Configure>", lambda _event: self._draw_durability_bar())

        bottom = tk.Frame(self.outer, bg="#020617")
        bottom.pack(fill="x", pady=(7, 0))
        tk.Label(bottom, textvariable=self.vars["loadout"], bg="#020617", fg="#94a3b8", font=("Segoe UI", 8), wraplength=330, justify="left").pack(side="left", fill="x", expand=True, anchor="w")
        tk.Label(bottom, textvariable=self.vars["damage"], bg="#020617", fg="#64748b", font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))
        resize = tk.Label(bottom, text="◢", bg="#020617", fg=self.app.colors["accent"], font=("Segoe UI", 11, "bold"), cursor="size_nw_se")
        resize.pack(side="right", padx=(10, 0))
        resize.bind("<ButtonPress-1>", self._start_resize)
        resize.bind("<B1-Motion>", self._resize)
        resize.bind("<ButtonRelease-1>", self._stop_resize)

    def update_from_session(self, session: SessionSummary | None) -> None:
        metrics = streamer_metrics(session)
        net = float(metrics["net"])
        return_pct = float(metrics["return_pct"])
        color = self.app.colors["accent"] if net > 0 else "#f97316" if net < 0 else "#e5edf8"
        self.vars["net_big"].set(f"{net:+.2f} PED")
        self.vars["return"].set(f"{return_pct:.2f}% Return")
        self.vars["loot"].set(f"Loot {float(metrics['loot']):.2f}")
        self.vars["cost"].set(f"Cost {float(metrics['cost']):.2f}")
        self.vars["events"].set(f"Events {int(float(metrics['events']))}")
        self.vars["damage"].set(f"Dmg {float(metrics['damage']):.1f}")
        self.vars["loadout"].set(str(metrics["loadout"]))
        durability = repair_durability_status(session)
        self.durability_pct = durability["percent"]
        self.durability_color = durability_color(self.durability_pct)
        self.vars["durability"].set(durability["streamer_text"])
        self._draw_durability_bar()
        self.net_label.configure(fg=color)
        self.return_label.configure(fg=color)

    def _draw_durability_bar(self) -> None:
        canvas = self.durability_bar
        if not hasattr(self, "durability_bar"):
            return
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        pct = max(0.0, min(100.0, self.durability_pct))
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#111827", outline="#1f2937", width=1)
        fill_width = int((width - 2) * pct / 100.0)
        if fill_width > 0:
            canvas.create_rectangle(1, 1, fill_width + 1, height - 1, fill=self.durability_color, outline="")
        if pct <= 0:
            canvas.create_text(width / 2, height / 2, text="AMP EMPTY", fill="#e5e7eb", font=("Segoe UI", 7, "bold"))

    def _start_drag(self, event) -> None:
        self._drag_origin = (event.x, event.y)

    def _drag(self, event) -> None:
        if not self._drag_origin:
            return
        dx, dy = self._drag_origin
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _start_resize(self, event) -> str:
        self._resize_origin = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())
        return "break"

    def _resize(self, event) -> str:
        if not self._resize_origin:
            return "break"
        start_x, start_y, start_width, start_height = self._resize_origin
        width = max(STREAMER_MIN_WIDTH, start_width + (event.x_root - start_x))
        height = max(STREAMER_MIN_HEIGHT, start_height + (event.y_root - start_y))
        self.geometry(f"{width}x{height}")
        return "break"

    def _stop_resize(self, _event) -> str:
        self._resize_origin = None
        return "break"

    def destroy(self) -> None:
        self.app.streamer_window = None
        super().destroy()


def _default_chat_log_path() -> Path:
    return Path.home() / "Documents" / "Entropia Universe" / "chat.log"


def _recent_events(store: Store, session_id: str, limit: int = 50) -> list[dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, kind, raw_message, payload
            FROM events
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
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
            FROM events
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


def _summarize_event(row: dict[str, object]) -> str:
    try:
        payload = json.loads(str(row.get("payload") or "{}"))
    except json.JSONDecodeError:
        payload = {}
    kind = row.get("kind")
    if kind == "loot":
        return f"{payload.get('quantity', 1)} x {payload.get('item_name', '?')} — {float(payload.get('value', 0) or 0):.2f} PED"
    if kind == "combat":
        cost = f" • {float(payload['shot_cost']):.5f} PED" if "shot_cost" in payload else ""
        if "damage" in payload:
            return f"Damage dealt: {payload['damage']}{cost}"
        if "damage_taken" in payload:
            return f"Damage taken: {payload['damage_taken']}"
        if "healed" in payload:
            return f"Healed: {payload['healed']}"
        return (", ".join(k for k, v in payload.items() if v and k not in {"shot_cost", "loadout"}) or "Combat event") + cost
    if kind == "weapon":
        return f"Equipped {payload.get('weapon', '?')}"
    if kind == "skill":
        return f"{payload.get('skill', '?')} +{payload.get('xp', '?')} XP"
    if kind == "craft":
        if "total_cost" in payload:
            attempts = int(payload.get("attempts", 1) or 1)
            blueprint = payload.get("blueprint", "Blueprint")
            return f"Crafting input: {attempts:,} x {blueprint} — {float(payload.get('total_cost', 0) or 0):.2f} PED"
        return f"{payload.get('result', '?')} {payload.get('item', '')}".strip()
    return str(row.get("raw_message") or "")


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


def durability_color(percent: float) -> str:
    stops = [(100.0, (34, 197, 94)), (60.0, (234, 179, 8)), (30.0, (249, 115, 22)), (10.0, (239, 68, 68)), (0.0, (0, 0, 0))]
    pct = max(0.0, min(100.0, percent))
    for index in range(len(stops) - 1):
        hi_pct, hi_rgb = stops[index]
        lo_pct, lo_rgb = stops[index + 1]
        if lo_pct <= pct <= hi_pct:
            span = hi_pct - lo_pct or 1.0
            t = (pct - lo_pct) / span
            rgb = tuple(round(lo + ((hi - lo) * t)) for hi, lo in zip(hi_rgb, lo_rgb))
            return "#%02x%02x%02x" % rgb
    return "#000000"


def format_repair_radar(session: SessionSummary | None) -> str:
    if not session:
        return "Repair radar: start a fresh run at 100% gun + amp TT"
    snapshot = session.loadout_snapshot or {}
    status = repair_durability_status(session)
    used = float(status.get("used_decay", 0.0) or 0.0)
    shots = int(status.get("used_shots", session.repair_shots) or 0)
    if "items" in status:
        return f"Repair radar: {status['streamer_text']} • {used:.4f} PED gun+amp decay across {shots:,} shots"
    budget = float(snapshot.get("repair_budget", 0) or 0)
    known = bool(snapshot.get("repair_budget_known")) and budget > 0
    if not known:
        return f"Repair radar: {shots:,} shots since 100% • {used:.4f} PED gun+amp decay accrued"
    remaining = max(0.0, budget - used)
    remaining_pct = remaining / budget * 100.0
    per_shot = float(snapshot.get("repair_decay_per_shot", 0) or 0)
    shots_left = max(0, math.ceil(remaining / per_shot)) if per_shot > 0 else 0
    return (
        f"Repair radar: {remaining_pct:.1f}% gun+amp TT left • "
        f"{remaining:.2f}/{budget:.2f} PED remaining • ~{shots_left:,} shots to empty"
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
    # LootNanny charged cost for outgoing attacks. In PED Hunter's parser those
    # are damage events or creature dodges; incoming misses/damage/heals do not
    # consume weapon ammo.
    return "damage" in event.payload or bool(event.payload.get("dodged"))


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


def _lifetime_run_label(session: SessionSummary | None) -> str:
    if not session:
        return "—"
    setup = session.loadout_name or session.activity.title()
    return f"{session.net_value:+.2f} PED · {setup}"


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
        }
    return_pct = _return_pct(session)
    return {
        "return_pct": return_pct,
        "loot": session.loot_value,
        "cost": session.hunting_cost,
        "net": session.net_value,
        "damage": session.combat_damage,
        "events": float(session.events),
        "loadout": session.loadout_name or "No loadout snapshot",
    }


def _install_combobox_typeahead(combo: ttk.Combobox, *, on_change) -> None:
    """Add predictable LootNanny-style first-letter/prefix navigation.

    Tk's native combobox behavior varies depending on focus/dropdown state. This
    handler keeps a short-lived typed prefix so clicking Amplifier and pressing
    "z" jumps to the Z entries, while typing "zx s" resolves to ZX Sinkadus.
    """
    combo._ped_typeahead = ""  # type: ignore[attr-defined]
    combo._ped_typeahead_after = None  # type: ignore[attr-defined]

    def clear_prefix() -> None:
        combo._ped_typeahead = ""  # type: ignore[attr-defined]
        combo._ped_typeahead_after = None  # type: ignore[attr-defined]

    def on_key(event) -> str | None:
        key = getattr(event, "keysym", "")
        char = getattr(event, "char", "") or ""
        if key in {"BackSpace", "Delete", "Left", "Right", "Home", "End", "Tab", "Return", "Escape"}:
            return None
        if not char or not char.isprintable():
            return None

        pending = getattr(combo, "_ped_typeahead_after", None)
        if pending:
            combo.after_cancel(pending)
        combo._ped_typeahead = f"{getattr(combo, '_ped_typeahead', '')}{char}"  # type: ignore[attr-defined]
        combo._ped_typeahead_after = combo.after(900, clear_prefix)  # type: ignore[attr-defined]

        values = tuple(combo.cget("values") or ())
        match = _typeahead_match(values, combo._ped_typeahead)  # type: ignore[attr-defined]
        if match is None and len(combo._ped_typeahead) > 1:  # type: ignore[attr-defined]
            combo._ped_typeahead = char  # type: ignore[attr-defined]
            match = _typeahead_match(values, char)
        if match is None:
            return None
        combo.set(match)
        combo.icursor(tk.END)
        combo.selection_clear()
        on_change()
        return "break"

    combo.bind("<KeyPress>", on_key, add="+")


def main() -> int:
    app = PedHunterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
