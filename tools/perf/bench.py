"""Headless timings of storage, ingestion and per-refresh projections (no Tk).

Works only on copies: put a backup of a hunt database at
``%PED_PERF_DIR%/data/real.sqlite3`` (``sqlite3 .backup``; never the live file).

    set PED_PERF_DIR=<scratch folder>
    set PYTHONPATH=<source tree to measure>/src
    python tools/perf/bench.py <label>

Writes ``result-<label>.json`` and the replay lines that ``appbench.py`` feeds.
"""
import json, os, re, shutil, sqlite3, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(os.environ["PED_PERF_DIR"])
REAL = HERE / "data" / "real.sqlite3"
WORK = HERE / "data" / f"work-{sys.argv[1]}.sqlite3"
out = {}


def _pick_sessions(database):
    """(most recently written run, largest run) of the copied history."""
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT session_id, COUNT(*), MAX(id) FROM events GROUP BY session_id").fetchall()
    finally:
        conn.close()
    return max(rows, key=lambda row: row[2])[0], max(rows, key=lambda row: row[1])[0]


LIVE, BIG = _pick_sessions(REAL)


def timed(name, fn, repeat=1):
    best = None
    for _ in range(repeat):
        t = time.perf_counter()
        result = fn()
        dt = (time.perf_counter() - t) * 1000
        best = dt if best is None else min(best, dt)
    out[name] = round(best, 2)
    print(f"{name:55s} {best:10.2f} ms", flush=True)
    return result


for suffix in ("", "-wal", "-shm"):
    Path(str(WORK) + suffix).unlink(missing_ok=True)
shutil.copyfile(REAL, WORK)

t0 = time.perf_counter()
import ped_hunter.app as appmod
from ped_hunter.storage import Store
from ped_hunter.catalog import Catalog
from ped_hunter.activity_trend import ActivityTrendBuilder
from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.chart_trails import TrailRecorder
from ped_hunter.hunt_trajectory import build_trajectory, active_hours
from ped_hunter.cockpit_chart import chart_scene
from ped_hunter.streamer_model import project
from ped_hunter.streamer_scene import scene, DESIGN
out["import app modules"] = round((time.perf_counter() - t0) * 1000, 2)
print("src:", appmod.__file__)

store = timed("startup: Store() open (schema + integrity)", lambda: Store(WORK))
catalog = timed("startup: Catalog.load()", Catalog.load)
timed("startup: list_recent_sessions(20)", lambda: store.list_recent_sessions(20))
timed("startup: lifetime_totals (all sessions)", store.lifetime_totals)
timed("startup: _wtb_listings", lambda: appmod._wtb_listings(store, 240))
timed("startup: get_last_contributed_session", store.get_last_contributed_session)
timed("resume: _load_resume_view (background thread)", lambda: appmod._load_resume_view(store, LIVE))
timed("resume: _resume_revision", lambda: appmod._resume_revision(store))

for label, sid in (("latest", LIVE), ("largest", BIG)):
    session = timed(f"tick[{label}]: get_session summary", lambda: store.get_session(sid), 3)
    timed(f"tick[{label}]: skill_gains_for_session", lambda: store.skill_gains_for_session(sid), 3)
    timed(f"tick[{label}]: _recent_events(80)", lambda: appmod._recent_events(store, sid, 80), 3)
    timed(f"tick[{label}]: _loot_event_points(160)", lambda: appmod._loot_event_points(store, sid, 160), 3)
    builder = ActivityTrendBuilder(appmod.KILL_CLUSTER_WINDOW_SECONDS)
    points = timed(f"tick[{label}]: trend build from scratch", lambda: appmod._kill_trend_points(store, sid, builder=builder))
    print("   groups:", len(points))
    points = timed(f"tick[{label}]: trend incremental (0 new rows)", lambda: appmod._kill_trend_points(store, sid, builder=builder), 3)
    attribution = HuntAttribution(store)
    attributed = timed(f"tick[{label}]: attribution.update first", lambda: attribution.update(sid, points, live=False))
    attributed = timed(f"tick[{label}]: attribution.update steady", lambda: attribution.update(sid, points, live=False), 3)
    recorder = TrailRecorder(store)
    histories = timed(f"tick[{label}]: trail recorder (read only)", lambda: recorder.update(sid, attributed, False), 3)
    trajectory = timed(f"tick[{label}]: build_trajectory", lambda: build_trajectory(session, attributed), 3)
    timed(f"tick[{label}]: active_hours", lambda: active_hours(trajectory), 3)
    timed(f"tick[{label}]: cockpit chart_scene 900x300", lambda: chart_scene(trajectory, 900, 300), 3)
    model = timed(f"tick[{label}]: HUD project()", lambda: project(session, attributed, (), unit="USD", running=True, histories=histories), 3)
    timed(f"tick[{label}]: HUD scene()", lambda: scene(model, "night-signal", DESIGN[0], DESIGN[1], 1, "theme"), 3)

# ---- ingestion: replay a real combat-dense window as new lines -------------
conn = sqlite3.connect(f"file:{REAL}?mode=ro", uri=True)
last_loot = conn.execute("SELECT MAX(id) FROM events WHERE session_id=? AND kind='loot'", (LIVE,)).fetchone()[0]
rows = conn.execute(
    "SELECT raw_message FROM events WHERE session_id=? AND id<=? AND kind NOT IN "
    "('manufacturing_offset','manufacturing_allocation') ORDER BY id DESC LIMIT 1200", (LIVE, last_loot)).fetchall()
conn.close()
stamp = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def shift(line):  # replay as *later* lines so duplicate-heal detection behaves as live
    m = stamp.match(line)
    if not m:
        return line
    moved = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") + timedelta(days=3)
    return moved.strftime("%Y-%m-%d %H:%M:%S") + line[19:]


lines = [shift(r[0]) + "\r\n" for r in reversed(rows)]
(HERE / "data" / "replay-lines.json").write_text(json.dumps(lines), encoding="utf-8")
shim = SimpleNamespace(store=store, catalog=catalog)
shim._restore_session_loadout = lambda s: appmod.PedHunterApp._restore_session_loadout(shim, s)
shim._process_held_lines = lambda *a, **k: appmod.PedHunterApp._process_held_lines(shim, *a, **k) if hasattr(appmod.PedHunterApp, "_process_held_lines") else None

# live style: small batches as the 250 ms poller would deliver them
batches = [lines[i:i + 4] for i in range(0, 400, 4)]
last = None
per_batch = []
t = time.perf_counter()
parsed_total = 0
for batch in batches:
    b = time.perf_counter()
    parsed, last = appmod.PedHunterApp._process_lines(shim, batch, LIVE, last)
    per_batch.append((time.perf_counter() - b) * 1000)
    parsed_total += parsed
total = (time.perf_counter() - t) * 1000
per_batch.sort()
out["ingest[latest]: 100 polls x 4 lines total"] = round(total, 1)
out["ingest[latest]: per event"] = round(total / max(parsed_total, 1), 2)
out["ingest[latest]: poll median"] = round(per_batch[len(per_batch) // 2], 2)
out["ingest[latest]: poll p95"] = round(per_batch[int(len(per_batch) * .95)], 2)
print(f"ingest live-style: {parsed_total} events in {total:.0f} ms -> {total / max(parsed_total, 1):.2f} ms/event; "
      f"poll median {per_batch[len(per_batch) // 2]:.1f} ms p95 {per_batch[int(len(per_batch) * .95)]:.1f} ms")
# burst: one 800-line catch-up read
t = time.perf_counter()
parsed, last = appmod.PedHunterApp._process_lines(shim, lines[400:1200], LIVE, last)
burst = (time.perf_counter() - t) * 1000
out["ingest[latest]: 800-line burst"] = round(burst, 1)
print(f"ingest burst: {parsed} events in {burst:.0f} ms")
final = store.get_session(LIVE)
out["_check"] = {"events": final.events, "loot": round(final.loot_value, 6), "cost": round(final.hunting_cost, 6),
                 "repair_shots": final.repair_shots, "repair_decay": round(final.repair_decay, 9),
                 "healing": round(final.healing_cost, 6)}
print("final state:", out["_check"])
(HERE / f"result-{sys.argv[1]}.json").write_text(json.dumps(out, indent=1))
