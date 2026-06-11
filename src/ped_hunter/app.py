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
from .storage import SessionSummary, Store


POLL_MS = 1000


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

        self.chat_path = tk.StringVar(value=str(_default_chat_log_path()))
        self.activity = tk.StringVar(value="hunt")
        self.status_text = tk.StringVar(value="Ready — choose a chat log and start tracking")
        self.session_text = tk.StringVar(value="No active session")
        self.catalog_query = tk.StringVar(value="Frontier Rifle")

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
        ttk.Button(header, text="Refresh", command=self._refresh_all, style="Ghost.TButton").grid(row=0, column=1, rowspan=2, sticky="e")

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
        ttk.Label(controls, textvariable=self.status_text, style="Status.TLabel").grid(row=1, column=0, columnspan=7, sticky="w", pady=(10, 0))

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew")
        self.dashboard_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.events_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.catalog_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        self.setup_tab = ttk.Frame(notebook, style="Root.TFrame", padding=14)
        notebook.add(self.dashboard_tab, text="Dashboard")
        notebook.add(self.events_tab, text="Events")
        notebook.add(self.catalog_tab, text="Catalog")
        notebook.add(self.setup_tab, text="Setup")

        self._build_dashboard_tab()
        self._build_events_tab()
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
        self._add_metric_card(cards, 1, "damage", "0.0", "Damage dealt")
        self._add_metric_card(cards, 2, "events", "0", "Events parsed")
        self._add_metric_card(cards, 3, "sessions", "0", "Stored runs")
        self._add_metric_card(cards, 4, "status", "Idle", "Tracker state")

        content = ttk.Frame(tab, style="Root.TFrame")
        content.grid(row=2, column=0, sticky="nsew", pady=(16, 0))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        recent_panel = self._panel(content, "Recent sessions", 0, 0)
        self.sessions_tree = self._tree(
            recent_panel,
            columns=("started", "activity", "loot", "damage", "events", "status"),
            headings=("Started", "Activity", "Loot", "Damage", "Events", "Status"),
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

    def _browse_log(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Entropia chat.log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.chat_path.set(path)

    def start(self) -> None:
        if self.running:
            return
        path = Path(self.chat_path.get().strip().strip('"'))
        if not path.exists():
            messagebox.showerror("PED Hunter", f"Chat log not found:\n{path}")
            self.status_text.set(f"Chat log not found: {path}")
            return

        self.current_log_path = path
        self.session_id = self.store.start_session(self.activity.get())
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
        for raw in lines:
            event = parse_line(raw)
            if not event:
                continue
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

    def _refresh_metrics(self, session: SessionSummary | None, sessions: list[SessionSummary]) -> None:
        state = "Live" if self.running else "Idle"
        self.metric_cards["status"].value.set(state)
        self.metric_cards["sessions"].value.set(str(len(sessions)))
        if not session:
            self.session_text.set("No sessions yet — start a run to begin collecting data")
            self.metric_cards["loot"].value.set("0.00 PED")
            self.metric_cards["damage"].value.set("0.0")
            self.metric_cards["events"].value.set("0")
            return
        status = "active" if session.ended_at is None else "ended"
        self.session_text.set(f"{session.session_id} • {session.activity} • {status} • started {session.started_at}")
        self.metric_cards["loot"].value.set(f"{session.loot_value:.2f} PED")
        self.metric_cards["damage"].value.set(f"{session.combat_damage:.1f}")
        self.metric_cards["events"].value.set(str(session.events))

    def _refresh_sessions(self, sessions: list[SessionSummary]) -> None:
        self.sessions_tree.delete(*self.sessions_tree.get_children())
        for session in sessions:
            self.sessions_tree.insert(
                "",
                "end",
                values=(
                    session.started_at,
                    session.activity,
                    f"{session.loot_value:.2f} PED",
                    f"{session.combat_damage:.1f}",
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
        if "damage" in payload:
            return f"Damage dealt: {payload['damage']}"
        if "damage_taken" in payload:
            return f"Damage taken: {payload['damage_taken']}"
        if "healed" in payload:
            return f"Healed: {payload['healed']}"
        return ", ".join(k for k, v in payload.items() if v) or "Combat event"
    if kind == "weapon":
        return f"Equipped {payload.get('weapon', '?')}"
    if kind == "skill":
        return f"{payload.get('skill', '?')} +{payload.get('xp', '?')} XP"
    if kind == "craft":
        return f"{payload.get('result', '?')} {payload.get('item', '')}".strip()
    return str(row.get("raw_message") or "")


def main() -> int:
    app = PedHunterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
