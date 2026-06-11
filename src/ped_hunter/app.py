"""Modern Tkinter dashboard for PED Hunter.

The interface keeps LootNanny's core idea — live run tracking from Entropia chat
logs — but presents it as a cleaner local-first dashboard with obvious status,
summary cards, recent event streams, catalog search, and setup guidance.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .catalog import Catalog, WeaponRecord
from .parser import ParsedEvent, parse_line
from .storage import LoadoutRecord, SessionSummary, Store


POLL_MS = 1000
AMP_CATEGORIES = {"BLP Amp", "Energy Amp", "Melee Amp", "MF Amp"}
SCOPE_CATEGORIES = {"Scope"}
SIGHT_CATEGORIES = {"Sight"}


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
        self.geometry("1180x760")
        self.minsize(1040, 680)

        self.store = Store()
        self.catalog = Catalog.load()
        self.session_id: str | None = None
        self.last_size = 0
        self.running = False
        self.current_log_path: Path | None = None
        self.streamer_window: StreamerWindow | None = None

        self.chat_path = tk.StringVar(value=str(_default_chat_log_path()))
        self.activity = tk.StringVar(value="hunt")
        self.status_text = tk.StringVar(value="Ready — choose a chat log and start tracking")
        self.session_text = tk.StringVar(value="No active session")
        self.catalog_query = tk.StringVar(value="Frontier Rifle")
        self.active_loadout_text = tk.StringVar(value="No active loadout — configure one before hunting")
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

        self.metric_cards: dict[str, MetricCard] = {}
        self._configure_theme()
        self._build_layout()
        self._refresh_all()

    def _configure_theme(self) -> None:
        self.configure(bg="#0f172a")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg = "#0f172a"
        panel = "#111827"
        panel_2 = "#172033"
        border = "#243247"
        text = "#e5edf8"
        muted = "#94a3b8"
        accent = "#38bdf8"
        good = "#22c55e"

        style.configure("Root.TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel, borderwidth=1, relief="solid")
        style.configure("Soft.TFrame", background=panel_2)
        style.configure("Card.TFrame", background=panel_2, borderwidth=1, relief="solid")
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=panel, foreground=text, font=("Segoe UI", 10))
        style.configure("PanelMuted.TLabel", background=panel, foreground=muted, font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background=panel_2, foreground=text, font=("Segoe UI", 20, "bold"))
        style.configure("CardLabel.TLabel", background=panel_2, foreground=muted, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=bg, foreground=text, font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("Status.TLabel", background=panel, foreground=muted, font=("Segoe UI", 9))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.configure("Ghost.TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.map("Accent.TButton", foreground=[("active", "#001018")], background=[("active", "#7dd3fc")])
        style.configure("TButton", padding=(10, 7))
        style.configure("TEntry", fieldbackground="#020617", foreground=text, insertcolor=text, bordercolor=border)
        style.configure("TCombobox", fieldbackground="#020617", foreground=text, arrowcolor=text)
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=muted, padding=(16, 9), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", panel_2)], foreground=[("selected", text)])
        style.configure(
            "Treeview",
            background="#0b1220",
            fieldbackground="#0b1220",
            foreground=text,
            bordercolor=border,
            rowheight=28,
            font=("Segoe UI", 9),
        )
        style.configure("Treeview.Heading", background=panel_2, foreground=text, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#075985")], foreground=[("selected", "#ffffff")])

        self.colors = {
            "bg": bg,
            "panel": panel,
            "panel_2": panel_2,
            "border": border,
            "text": text,
            "muted": muted,
            "accent": accent,
            "good": good,
            "warn": "#f59e0b",
            "bad": "#ef4444",
        }

    def _build_layout(self) -> None:
        root = ttk.Frame(self, style="Root.TFrame", padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="Root.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="PED Hunter", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="A modern, local-first Entropia session cockpit inspired by LootNanny — cleaner, faster, and ready for richer catalogs.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="Streamer UI", command=self._open_streamer_window, style="Ghost.TButton").grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 8))
        ttk.Button(header, text="Refresh", command=self._refresh_all, style="Ghost.TButton").grid(row=0, column=2, rowspan=2, sticky="e")

        controls = ttk.Frame(root, style="Panel.TFrame", padding=14)
        controls.grid(row=1, column=0, sticky="ew", pady=(16, 14))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Chat log", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(controls, textvariable=self.chat_path).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(controls, text="Browse", command=self._browse_log).grid(row=0, column=2, padx=(0, 8))
        ttk.Label(controls, text="Activity", style="PanelMuted.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 8))
        ttk.Combobox(controls, textvariable=self.activity, values=["hunt", "craft", "mine"], width=9, state="readonly").grid(row=0, column=4, padx=(0, 8))
        self.start_button = ttk.Button(controls, text="Start run", command=self.start, style="Accent.TButton")
        self.start_button.grid(row=0, column=5, padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=6)
        ttk.Label(controls, textvariable=self.active_loadout_text, style="PanelMuted.TLabel").grid(row=1, column=0, columnspan=7, sticky="w", pady=(10, 0))
        ttk.Label(controls, textvariable=self.status_text, style="Status.TLabel").grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew")
        self.dashboard_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.events_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.loadouts_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.catalog_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.setup_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        notebook.add(self.dashboard_tab, text="Dashboard")
        notebook.add(self.events_tab, text="Events")
        notebook.add(self.loadouts_tab, text="Loadouts")
        notebook.add(self.catalog_tab, text="Catalog")
        notebook.add(self.setup_tab, text="Setup")

        self._build_dashboard_tab()
        self._build_events_tab()
        self._build_loadouts_tab()
        self._build_catalog_tab()
        self._build_setup_tab()

    def _build_dashboard_tab(self) -> None:
        tab = self.dashboard_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        ttk.Label(tab, textvariable=self.session_text, style="Subtitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 14))

        cards = ttk.Frame(tab, style="Root.TFrame")
        cards.grid(row=1, column=0, sticky="ew")
        for i in range(5):
            cards.columnconfigure(i, weight=1, uniform="cards")
        self._add_metric_card(cards, 0, "loot", "0.00 PED", "Loot return")
        self._add_metric_card(cards, 1, "cost", "0.00 PED", "Hunting cost")
        self._add_metric_card(cards, 2, "net", "0.00 PED", "Net TT")
        self._add_metric_card(cards, 3, "damage", "0.0", "Damage dealt")
        self._add_metric_card(cards, 4, "status", "Idle", "Tracker state")

        content = ttk.Frame(tab, style="Root.TFrame")
        content.grid(row=2, column=0, sticky="nsew", pady=(16, 0))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        recent_panel = self._panel(content, "Recent sessions", 0, 0)
        self.sessions_tree = self._tree(
            recent_panel,
            headings=("Started", "Activity", "Loadout", "Loot", "Cost", "Net", "Events", "Status"),
            columns=("started", "activity", "loadout", "loot", "cost", "net", "events", "status"),
        )

        insight_panel = self._panel(content, "Design direction", 0, 1)
        text = tk.Text(
            insight_panel,
            height=12,
            wrap="word",
            bg="#0b1220",
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 10),
        )
        text.pack(fill="both", expand=True)
        text.insert(
            "1.0",
            "LootNanny proved the value of run-centric tracking: start a run, read chat, summarize loot, combat, skills, and crafting.\n\n"
            "PED Hunter keeps that loop but modernizes the experience:\n"
            "• one clear cockpit instead of dense form rows\n"
            "• visible state and metrics at all times\n"
            "• local SQLite history, not loose run files\n"
            "• catalog search and richer data-source path\n"
            "• clean dark UI that works as a standalone Windows app",
        )
        text.configure(state="disabled")

    def _build_events_tab(self) -> None:
        tab = self.events_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        panel = self._panel(tab, "Live event stream", 0, 0)
        self.events_tree = self._tree(
            panel,
            columns=("time", "kind", "summary"),
            headings=("Time", "Kind", "Summary"),
        )

    def _build_loadouts_tab(self) -> None:
        tab = self.loadouts_tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        list_panel = self._panel(tab, "Saved loadouts", 0, 0)
        self.loadouts_tree = self._tree(
            list_panel,
            columns=("active", "name", "weapon", "amp", "enh", "cost"),
            headings=("Active", "Name", "Weapon", "Amp", "Enhancers", "Cost/shot"),
        )
        self.loadouts_tree.bind("<<TreeviewSelect>>", lambda _event: self._load_selected_loadout())

        editor = self._panel(tab, "Loadout builder", 0, 1)
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
            bg="#0b1220",
            fg=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 9),
        )
        help_text.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        help_text.insert(
            "1.0",
            "LootNanny's Config tab made loadouts the source of truth for hunting costs: weapon + amp + scope/sights + enhancers. "
            "PED Hunter keeps that requirement but keeps the active loadout visible in the main control bar so you do not start a hunt with unknown costs. "
            "Amplifier, scope, and sight selectors are category-specific so items like ZX Eagle Eye only appear under Scope. "
            "Damage enhancers increase weapon ammo/decay by 10% each; economy enhancers reduce weapon ammo/decay by 1% each; attachments add their own burn/decay.",
        )
        help_text.configure(state="disabled")
        self._refresh_loadout_preview()

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
            bg="#0b1220",
            fg=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 10),
        )
        text.pack(fill="both", expand=True)
        text.insert(
            "1.0",
            "Getting started\n\n"
            "1. In Entropia Universe, enable chat logging.\n"
            "2. Select your chat.log above. The default guess is shown automatically.\n"
            "3. Choose hunt, craft, or mine, then Start run.\n"
            "4. PED Hunter parses new lines and writes events to a local SQLite database under AppData.\n\n"
            "Modernization notes\n\n"
            "LootNanny used PyQt tabs for loot, analysis, skills, combat, crafting, Twitch, config, and streamer views. "
            "PED Hunter starts with the same useful backbone but simplifies the first-run experience into a dashboard, "
            "event stream, catalog lookup, and setup guide. Future passes can add dedicated crafting, loadout, markup, "
            "and streamer panels without crowding the main cockpit.",
        )
        text.configure(state="disabled")

    def _add_metric_card(self, parent: ttk.Frame, column: int, key: str, value: str, label: str) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=14)
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        value_var = tk.StringVar(value=value)
        label_var = tk.StringVar(value=label)
        ttk.Label(frame, textvariable=value_var, style="CardValue.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=label_var, style="CardLabel.TLabel").pack(anchor="w", pady=(4, 0))
        self.metric_cards[key] = MetricCard(frame=frame, value=value_var, label=label_var)

    def _panel(self, parent: ttk.Frame, title: str, row: int, column: int) -> ttk.Frame:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        panel.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0), pady=0)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text=title, style="Panel.TLabel", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))
        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        return body

    def _tree(self, parent: ttk.Frame, *, columns: tuple[str, ...], headings: tuple[str, ...]) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        for col, heading in zip(columns, headings):
            tree.heading(col, text=heading)
            tree.column(col, width=120, minwidth=70, stretch=True)
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
            self.active_loadout_text.set(f"Active loadout: {active.name} • {active.weapon} • {active.cost_per_shot:.5f} PED/shot")
        else:
            self.active_loadout_text.set("No active loadout — configure one before hunting for accurate costs")
        for loadout in self.store.list_loadouts():
            self.loadouts_tree.insert(
                "",
                "end",
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
        if self.activity.get() == "hunt" and not active_loadout:
            if not messagebox.askyesno(
                "PED Hunter",
                "No active loadout is configured. Hunting costs will be incomplete. Start anyway?",
            ):
                return

        self.current_log_path = path
        self.session_id = self.store.start_session(self.activity.get(), active_loadout)
        self.last_size = path.stat().st_size
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_text.set(f"Tracking {path.name}; new lines will appear in Events")
        self._refresh_all()
        self.after(POLL_MS, self._poll_log)

    def stop(self) -> None:
        if not self.running:
            return
        if self.session_id:
            self.store.end_session(self.session_id)
        self.running = False
        self.session_id = None
        self.current_log_path = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_text.set("Run stopped")
        self._refresh_all()

    def _poll_log(self) -> None:
        if not self.running or self.current_log_path is None:
            return
        path = self.current_log_path
        try:
            size = path.stat().st_size
            if size < self.last_size:
                self.last_size = 0
            if size > self.last_size:
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(self.last_size)
                    lines = fh.readlines()
                    self.last_size = fh.tell()
                self._process_lines(lines)
            self._refresh_all()
        except Exception as exc:  # pragma: no cover - defensive UI guard
            self.status_text.set(f"Tracking error: {exc}")
        finally:
            if self.running:
                self.after(POLL_MS, self._poll_log)

    def _process_lines(self, lines: list[str]) -> None:
        if not self.session_id:
            return
        parsed = 0
        active_loadout = self.store.get_active_loadout()
        for raw in lines:
            event = parse_line(raw)
            if not event:
                continue
            if active_loadout and _event_consumes_shot(event):
                event.payload["shot_cost"] = active_loadout.cost_per_shot
                event.payload["loadout"] = active_loadout.name
            self.store.add_event(self.session_id, event.to_row())
            parsed += 1
        if parsed:
            self.status_text.set(f"Parsed {parsed} new event{'s' if parsed != 1 else ''}")

    def _refresh_all(self) -> None:
        current = self.store.get_current_session()
        sessions = self.store.list_recent_sessions(20)
        display = current or (sessions[0] if sessions else None)
        self._refresh_metrics(display, sessions)
        self._refresh_sessions(sessions)
        self._refresh_events(display.session_id if display else None)
        self._refresh_loadouts()
        if self.streamer_window and self.streamer_window.winfo_exists():
            self.streamer_window.update_from_session(display)

    def _refresh_metrics(self, session: SessionSummary | None, sessions: list[SessionSummary]) -> None:
        state = "Live" if self.running else "Idle"
        self.metric_cards["status"].value.set(state)
        if not session:
            self.session_text.set("No sessions yet — start a run to begin collecting data")
            self.metric_cards["loot"].value.set("0.00 PED")
            self.metric_cards["cost"].value.set("0.00 PED")
            self.metric_cards["net"].value.set("0.00 PED")
            self.metric_cards["damage"].value.set("0.0")
            return
        status = "active" if session.ended_at is None else "ended"
        loadout = f" • {session.loadout_name}" if session.loadout_name else ""
        self.session_text.set(f"{session.session_id} • {session.activity}{loadout} • {status} • started {session.started_at}")
        self.metric_cards["loot"].value.set(f"{session.loot_value:.2f} PED")
        self.metric_cards["cost"].value.set(f"{session.hunting_cost:.2f} PED")
        self.metric_cards["net"].value.set(f"{session.net_value:+.2f} PED")
        self.metric_cards["damage"].value.set(f"{session.combat_damage:.1f}")

    def _refresh_sessions(self, sessions: list[SessionSummary]) -> None:
        self.sessions_tree.delete(*self.sessions_tree.get_children())
        for session in sessions:
            self.sessions_tree.insert(
                "",
                "end",
                values=(
                    session.started_at,
                    session.activity,
                    session.loadout_name or "—",
                    f"{session.loot_value:.2f} PED",
                    f"{session.hunting_cost:.2f} PED",
                    f"{session.net_value:+.2f} PED",
                    session.events,
                    "active" if session.ended_at is None else "ended",
                ),
            )

    def _refresh_events(self, session_id: str | None) -> None:
        self.events_tree.delete(*self.events_tree.get_children())
        if not session_id:
            return
        for row in _recent_events(self.store, session_id, limit=80):
            self.events_tree.insert("", "end", values=(row["timestamp"] or "", row["kind"], _summarize_event(row)))

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

        for weapon in matches:
            self.catalog_tree.insert(
                "",
                "end",
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
        self.geometry("520x170+120+120")
        self._drag_origin: tuple[int, int] | None = None
        self.vars = {
            "return": tk.StringVar(value="0.00%"),
            "loot": tk.StringVar(value="Loot 0.00 PED"),
            "cost": tk.StringVar(value="Cost 0.00 PED"),
            "net": tk.StringVar(value="Net +0.00 PED"),
            "damage": tk.StringVar(value="Damage 0.0"),
            "events": tk.StringVar(value="Events 0"),
            "loadout": tk.StringVar(value="No active loadout"),
        }
        self._build()
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self) -> None:
        outer = tk.Frame(self, bg="#020617", highlightbackground="#38bdf8", highlightthickness=2, padx=14, pady=10)
        outer.pack(fill="both", expand=True)
        top = tk.Frame(outer, bg="#020617")
        top.pack(fill="x")
        tk.Label(top, textvariable=self.vars["return"], bg="#020617", fg="#e5edf8", font=("Segoe UI", 30, "bold")).pack(side="left")
        close = tk.Label(top, text="×", bg="#020617", fg="#94a3b8", font=("Segoe UI", 16, "bold"), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _event: self.destroy())
        tk.Label(outer, textvariable=self.vars["loadout"], bg="#020617", fg="#7dd3fc", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        grid = tk.Frame(outer, bg="#020617")
        grid.pack(fill="x")
        for col, key in enumerate(("loot", "cost", "net")):
            grid.columnconfigure(col, weight=1)
            tk.Label(grid, textvariable=self.vars[key], bg="#111827", fg="#e5edf8", font=("Segoe UI", 12, "bold"), padx=10, pady=8).grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
        bottom = tk.Frame(outer, bg="#020617")
        bottom.pack(fill="x", pady=(8, 0))
        tk.Label(bottom, textvariable=self.vars["damage"], bg="#020617", fg="#94a3b8", font=("Segoe UI", 10)).pack(side="left")
        tk.Label(bottom, textvariable=self.vars["events"], bg="#020617", fg="#94a3b8", font=("Segoe UI", 10)).pack(side="right")

    def update_from_session(self, session: SessionSummary | None) -> None:
        metrics = streamer_metrics(session)
        self.vars["return"].set(f"{metrics['return_pct']:.2f}%")
        self.vars["loot"].set(f"Loot {metrics['loot']:.2f} PED")
        self.vars["cost"].set(f"Cost {metrics['cost']:.2f} PED")
        self.vars["net"].set(f"Net {metrics['net']:+.2f} PED")
        self.vars["damage"].set(f"Damage {metrics['damage']:.1f}")
        self.vars["events"].set(f"Events {int(metrics['events'])}")
        self.vars["loadout"].set(str(metrics["loadout"]))

    def _start_drag(self, event) -> None:
        self._drag_origin = (event.x, event.y)

    def _drag(self, event) -> None:
        if not self._drag_origin:
            return
        dx, dy = self._drag_origin
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

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
    return_pct = (session.loot_value / session.hunting_cost * 100.0) if session.hunting_cost > 0 else 0.0
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
