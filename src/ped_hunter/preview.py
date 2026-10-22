"""Isolated visual preview: synthetic events only, never the player's database/log."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from . import app as app_module
from .storage import LoadoutRecord, Store


def open_preview() -> None:
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        command = [sys.executable, "gui", "--preview"]
    else:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
        command = [sys.executable, "-m", "ped_hunter", "gui", "--preview"]
    subprocess.Popen(command, env=env)


def main() -> None:
    with TemporaryDirectory(prefix="ped-hunter-feedback-demo-") as directory:
        store = Store(Path(directory) / "demo.sqlite3")
        loadout = LoadoutRecord(id=None, name="DEMO · Frontier rifle", weapon="Frontier Rifle", ammo_burn=100, decay=0.0002, cost_per_shot=0.0102)
        sid = store.start_session("hunt", loadout)
        app = app_module.PedHunterApp(store=store)
        app.title("PED Hunter · VISUAL PREVIEW · synthetic data")
        app.session_id = sid
        app.currency_mode.set("PED")
        app.running = True  # Visual state only: no log worker is ever started.
        app.status_text.set("DEMO ONLY · synthetic data · your history is untouched")
        app.start_button.configure(state="disabled")
        app.resume_button.configure(state="disabled")
        app.stop_button.configure(state="disabled")
        app.chat_path.set("Visual preview — no chat log is read")
        app._refresh_all()
        app._open_streamer_window()
        overlay = app.streamer_window
        overlay.title("PED Hunter · DEMO feedback overlay")
        overlay.geometry(f"460x430+{min(1260, max(0, app.winfo_screenwidth() - 480))}+100")
        # The compact window must identify simulated money as clearly as the main one.
        overlay.brand_label.configure(text="◆ PED HUNTER · DEMO")
        app.geometry("1180x780+60+60")
        notebook = app.events_tab.master
        notebook.select(app.events_tab)
        step = 0
        origin = datetime.now()
        values = (0.04, 0.09, 0.025, 0.14, 0.06, 0.035, 0.11, 0.05)

        def replay() -> None:
            nonlocal step, sid, origin
            step = 0
            origin = datetime.now()
            sid = store.start_session("hunt", loadout)
            app.session_id = sid
            app.running = True
            app.start_button.configure(state="disabled")
            app.status_text.set("DEMO ONLY · synthetic data · your history is untouched")
            app._refresh_all()
            app.after(800, tick)

        app.start_button.configure(text="Replay preview", command=replay)

        def tick() -> None:
            nonlocal step
            if step >= 12:
                app.running = False
                app._refresh_all()
                app.start_button.configure(state="normal")
                app.status_text.set("Preview complete · Replay preview to see the effects again")
                return
            timestamp = (origin + timedelta(seconds=step * 4)).isoformat()
            for shot in range(5):
                store.add_event(sid, {"kind": "combat", "timestamp": timestamp, "raw_message": "Demo shot", "payload": {"shot_cost": 0.0102, "ammo_cost": 0.01, "repair_decay": 0.0002, "damage": 8.0}})
            store.add_event(sid, {"kind": "loot", "timestamp": timestamp, "raw_message": "Demo loot", "payload": {"value": values[step % len(values)], "item_name": "Animal Muscle Oil" if step % 2 else "Shrapnel"}})
            if step % 2 == 0:
                store.add_event(sid, {"kind": "skill", "timestamp": timestamp, "raw_message": "Demo skill", "payload": {"skill": "Rifle" if step % 4 == 0 else "Anatomy", "xp": 0.0123}})
            app._refresh_all()
            step += 1
            app.after(2400, tick)

        app.after(1200, tick)
        app.mainloop()


if __name__ == "__main__":
    main()
