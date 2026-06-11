"""A lightweight Tkinter dashboard for PED Hunter."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from .catalog import Catalog
from .parser import parse_line
from .storage import Store


class PedHunterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PED Hunter")
        self.geometry("900x620")
        self.minsize(800, 520)

        self.store = Store()
        self.catalog = Catalog.load()
        self.session_id: str | None = None
        self.last_size = 0
        self.running = False
        self.chat_path = tk.StringVar()
        self.activity = tk.StringVar(value="hunt")

        self._build()
        self._refresh_summary()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="Chat log:").pack(side="left")
        ttk.Entry(top, textvariable=self.chat_path).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(top, text="Activity:").pack(side="left", padx=(8, 4))
        ttk.Combobox(top, textvariable=self.activity, values=["hunt", "craft", "mine"], width=10, state="readonly").pack(side="left")
        ttk.Button(top, text="Start", command=self.start).pack(side="left", padx=6)
        ttk.Button(top, text="Stop", command=self.stop).pack(side="left")

        self.summary = tk.StringVar(value="No active session")
        ttk.Label(root, textvariable=self.summary, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 8))

        self.log = tk.Text(root, height=25, wrap="none")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    def start(self) -> None:
        if self.running:
            return
        path = Path(self.chat_path.get().strip().strip('"'))
        if not path.exists():
            messagebox.showerror("PED Hunter", f"Chat log not found:\n{path}")
            return
        self.session_id = self.store.start_session(self.activity.get())
        self.last_size = 0
        self.running = True
        self._append(f"Started session {self.session_id}\n")
        self._poll(path)

    def stop(self) -> None:
        if not self.running:
            return
        if self.session_id:
            self.store.end_session(self.session_id)
            self._append(f"Stopped session {self.session_id}\n")
        self.running = False
        self.session_id = None
        self._refresh_summary()

    def _poll(self, path: Path) -> None:
        if not self.running:
            return
        try:
            size = path.stat().st_size
            if size < self.last_size:
                self.last_size = 0
            if size > self.last_size:
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(self.last_size)
                    lines = fh.readlines()
                    self.last_size = fh.tell()
                for line in lines:
                    event = parse_line(line)
                    if not event:
                        continue
                    self.store.add_event(self.session_id, event.to_row())
                    self._append(self._format_event(event))
            self._refresh_summary()
        except Exception as exc:
            self._append(f"[error] {exc}\n")
        finally:
            self.after(1000, lambda: self._poll(path))

    def _refresh_summary(self) -> None:
        session = self.store.get_current_session()
        if not session:
            self.summary.set("No active session")
            return
        self.summary.set(
            f"{session.session_id} | {session.activity} | events={session.events} | loot={session.loot_value:.2f} PED | damage={session.combat_damage:.2f}"
        )

    def _format_event(self, event) -> str:
        if event.kind == "loot":
            item = event.payload.get("item_name", "?")
            resolved = self.catalog.resolve_weapon_name(item) or item
            qty = event.payload.get("quantity", 1)
            value = event.payload.get("value", 0.0)
            return f"[loot] {qty} x {resolved} ({value:.2f} PED)\n"
        if event.kind == "weapon":
            return f"[weapon] {event.payload.get('weapon')}\n"
        if event.kind == "skill":
            return f"[skill] {event.payload.get('skill')}: +{event.payload.get('xp')} XP\n"
        if event.kind == "craft":
            return f"[craft] {event.payload.get('result')} {event.payload.get('item')}\n"
        return f"[{event.kind}] {event.payload}\n"

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    app = PedHunterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
