"""Time the real app (Tk) on a private database copy and a private chat.log.

Startup, Resume, sustained appended lines (ingestion backlog, event-loop gaps,
refresh cost, CPU, memory), redraws, tab switches and End run. Run it through
``hidden_run.py`` so no window reaches the desktop; run ``bench.py`` first (it
writes the replay lines). Never touches %LOCALAPPDATA%/ped-hunter.

    python tools/perf/hidden_run.py 900 <python> tools/perf/appbench.py <label> [session-id]

PED_BENCH_WARM=1 measures a normal launch (the one-time upgrade already done),
PED_BENCH_MINIMIZED=1 the main window minimized with the HUD open, PED_BENCH_SHORT=1
skips the faster feed. Output: ``appbench-<label>.log`` / ``appresult-<label>.json``.
"""
import ctypes, json, os, shutil, statistics, sys, threading, time, traceback
from ctypes import wintypes
from pathlib import Path

HERE = Path(os.environ["PED_PERF_DIR"])
LABEL = sys.argv[1]
LOG = HERE / f"appbench-{LABEL}.log"
sys.stdout = sys.stderr = LOG.open("w", encoding="utf-8", buffering=1)
REAL = HERE / "data" / "real.sqlite3"
WORKDIR = HERE / "data" / f"app-{LABEL}"
import sqlite3


def _pick_sessions(database):
    """(most recently written run, largest run) of the copied history."""
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT session_id, COUNT(*), MAX(id) FROM events GROUP BY session_id").fetchall()
    finally:
        conn.close()
    return max(rows, key=lambda row: row[2])[0], max(rows, key=lambda row: row[1])[0]


LIVE = sys.argv[2] if len(sys.argv) > 2 else _pick_sessions(REAL)[0]
WARM = bool(os.environ.get("PED_BENCH_WARM"))
SHORT = bool(os.environ.get("PED_BENCH_SHORT"))
out = {}


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def rss_mb():
    pmc = PMC()
    pmc.cb = ctypes.sizeof(pmc)
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return round(pmc.WorkingSetSize / 1e6, 1), round(pmc.PeakWorkingSetSize / 1e6, 1)


def note(name, value):
    out[name] = value
    print(f"{name:58s} {value}", flush=True)


def ms(t):
    return round((time.perf_counter() - t) * 1000, 1)


def stats(values):
    if not values:
        return {"n": 0}
    values = sorted(values)
    return {"n": len(values), "median": round(statistics.median(values), 1),
            "p95": round(values[min(len(values) - 1, int(len(values) * .95))], 1), "max": round(values[-1], 1),
            "sum": round(sum(values), 1)}


try:
    shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True)
    db = WORKDIR / "ped-hunter.sqlite3"
    shutil.copyfile(REAL, db)
    chat = WORKDIR / "chat.log"
    chat.write_bytes(b"2026-11-19 00:00:00 [System] [] bench start\r\n")
    lines = json.loads((HERE / "data" / "replay-lines.json").read_text(encoding="utf-8"))

    t_import = time.perf_counter()
    from ped_hunter import cli
    from ped_hunter.storage import Store
    import ped_hunter.app as appmod
    from tkinter import messagebox
    note("import ms", ms(t_import))
    print("src:", appmod.__file__)
    Store.default_db_path = staticmethod(lambda: db)
    cli._gui_lock_path = lambda: WORKDIR / "PED-Hunter.gui.lock"
    for name in ("showerror", "showinfo", "showwarning", "askyesno"):
        setattr(messagebox, name, lambda *a, _n=name, **k: print("MESSAGEBOX", _n, a) or (_n == "askyesno"))
    appmod._default_chat_log_path = lambda: chat

    if WARM:  # a normal launch: the one-time upgrade work happened on an earlier open
        import subprocess
        subprocess.run([sys.executable, "-c", "import sys; from ped_hunter.storage import Store; "
                        "s = Store(sys.argv[1]); s.list_all_sessions()", str(db)], check=True,
                       creationflags=0x08000000)
    # ---- startup ---------------------------------------------------------
    t_start = time.perf_counter()
    t = time.perf_counter()
    store = Store(db)
    note("startup: Store() ms", ms(t))
    t = time.perf_counter()
    app = appmod.PedHunterApp(store=store)
    note("startup: PedHunterApp() ms", ms(t))
    t = time.perf_counter()
    app.update()
    note("startup: first update() ms", ms(t))
    note("startup: total to interactive ms", ms(t_start))
    note("rss after startup MB (now, peak)", rss_mb())

    # instrumentation
    spans = {}

    def wrap(obj, name, key=None):
        original = getattr(obj, name)
        key = key or name

        def wrapper(*a, **k):
            t = time.perf_counter()
            try:
                return original(*a, **k)
            finally:
                spans.setdefault(key, []).append((time.perf_counter() - t) * 1000)
        setattr(obj, name, wrapper)

    wrap(app, "_refresh_live_views")
    wrap(app, "_process_lines")
    wrap(app, "_run_full_refresh")
    wrap(app, "_refresh_hunt_chart")
    wrap(app, "_draw_hunt_chart")

    gaps = []
    beat = {"last": time.perf_counter(), "on": True}

    def heartbeat():
        now = time.perf_counter()
        gaps.append((now - beat["last"]) * 1000)
        beat["last"] = now
        if beat["on"]:
            app.after(10, heartbeat)

    def pump(seconds=None, until=None, limit=120):
        end = time.perf_counter() + (seconds if seconds is not None else limit)
        while time.perf_counter() < end:
            app.update()
            if until is not None and until():
                return True
            time.sleep(0.002)
        return until is None

    # ---- streamer HUD + resume ---------------------------------------------
    t = time.perf_counter()
    app._open_streamer_window()
    app.update()
    note("HUD: open ms", ms(t))
    hud = app.streamer_window
    wrap(hud, "update_from_session", "hud.update_from_session")
    wrap(hud, "redraw", "hud.redraw")
    app._resume_session_id = lambda: LIVE
    beat["last"] = time.perf_counter()
    app.after(10, heartbeat)
    t = time.perf_counter()
    app.resume_selected_session()
    ok = pump(until=lambda: app._resume_pending is None and app._log_worker is not None, limit=120)
    note("resume: click to tracking ms", ms(t))
    note("resume: ok", ok)
    note("resume: status", app.status_text.get())
    note("resume: UI stall during resume (max gap ms)", round(max(gaps), 1) if gaps else None)
    del app._resume_session_id
    pump(2.0)
    spans.clear()
    gaps.clear()

    # ---- sustained live updates ----------------------------------------------
    def feed(rate, count, offset):
        with chat.open("ab", buffering=0) as stream:
            for i in range(count):
                stream.write(lines[offset + i].encode("utf-8"))
                time.sleep(1.0 / rate)

    def live_phase(name, rate, count, offset):
        spans.clear()
        gaps.clear()
        before = store.get_session(LIVE).events
        cpu0, wall0 = time.process_time(), time.perf_counter()
        feeder = threading.Thread(target=feed, args=(rate, count, offset), daemon=True)
        feeder.start()
        beat["last"] = time.perf_counter()
        pump(until=lambda: not feeder.is_alive(), limit=300)
        fed = time.perf_counter()
        caught = pump(until=lambda: app._log_queue.empty() and store.get_session_resume_state(LIVE) is not None
                      and _events() >= before + count, limit=600)
        lag = (time.perf_counter() - fed) * 1000
        pump(2.5)  # let the deferred full refresh run
        wall = time.perf_counter() - wall0
        note(f"{name}: caught up", caught)
        note(f"{name}: backlog after feed ended ms", round(lag, 1))
        note(f"{name}: CPU % of one core", round((time.process_time() - cpu0) / wall * 100, 1))
        note(f"{name}: UI heartbeat gap ms", stats(gaps))
        note(f"{name}: UI gaps >100ms", sum(1 for g in gaps if g > 100))
        for key in sorted(spans):
            note(f"{name}: {key} ms", stats(spans[key]))

    def _events():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (LIVE,)).fetchone()[0]
        finally:
            conn.close()

    if os.environ.get("PED_BENCH_MINIMIZED"):  # the usual hunting setup: main minimized, HUD open
        app.iconify(); app.update()
        note("main window state", app.state())
    live_phase("live 5 lines/s x 20s", 5, 100, 0)
    if not SHORT:
        live_phase("live 20 lines/s x 10s", 20, 200, 100)
    note("rss after live MB (now, peak)", rss_mb())
    if app.state() == "iconic":
        t = time.perf_counter(); app.deiconify(); app.update()
        note("restore main window (deferred chart rebuild) ms", ms(t))
        note("chart caught up after restore", app._hunt_trajectory.net == store.get_session(LIVE).net_value)

    # ---- redraw / resize / interaction costs (UI thread) -----------------------
    def measure(fn, n):
        values = []
        for i in range(n):
            t = time.perf_counter()
            fn(i)
            app.update()
            values.append((time.perf_counter() - t) * 1000)
        return stats(values)

    note("HUD: redraw ms", measure(lambda i: hud.redraw(), 30))
    w0 = hud.winfo_width()
    note("HUD: resize step ms", measure(lambda i: hud._resize_to(w0 + (i % 10) * 12), 30))
    hud._resize_to(w0)
    note("HUD: theme cycle ms", measure(lambda i: hud.cycle_theme(1), 8))
    note("cockpit chart: redraw ms", measure(lambda i: app._draw_hunt_chart(), 30))
    steps = len(app._hunt_view.steps) if app._hunt_view else 0
    note("cockpit chart: steps", steps)
    if steps:
        note("cockpit chart: hover step ms", measure(lambda i: app._set_hunt_focus(i % steps), 40))
        app._set_hunt_focus(None)
    base_w, base_h = app.winfo_width(), app.winfo_height()
    note("main window: size", (base_w, base_h))
    note("main window: resize step ms", measure(lambda i: app.geometry(f"{base_w - 200 + (i % 10) * 40}x{base_h - 100 + (i % 5) * 40}"), 30))
    app.geometry(f"{base_w}x{base_h}")
    notebook = app.dashboard_tab.master
    for tab in notebook.tabs():
        label = notebook.tab(tab, "text")
        note(f"tab switch+refresh [{label}] ms", measure(lambda i: (notebook.select(tab), app._refresh_visible_tab()), 3))
    note("refresh_all (currency toggle) ms", measure(lambda i: app.currency_mode.set("PED" if i % 2 == 0 else "USD"), 4))
    t = time.perf_counter()
    app.stop()
    app.update()
    note("End run ms", ms(t))
    beat["on"] = False
    final = store.get_session(LIVE)
    note("_check", {"events": final.events, "loot": round(final.loot_value, 6), "cost": round(final.hunting_cost, 6),
                    "repair_shots": final.repair_shots, "repair_decay": round(final.repair_decay, 9),
                    "healing": round(final.healing_cost, 6)})
    app.destroy()
except Exception:
    traceback.print_exc()
    out["_error"] = traceback.format_exc()
(HERE / f"appresult-{LABEL}.json").write_text(json.dumps(out, indent=1, default=str))
os._exit(0)
