
"""Command-line entrypoint for PED Hunter."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
import traceback
import urllib.request
import json
from contextlib import contextmanager

from .catalog import Catalog
from .parser import parse_line
from .storage import Store

LOOTNANNY_RAW_BASE = "https://raw.githubusercontent.com/euloggeradmin/LootNanny/main/data/raw/"
FRONTIER_HUNTING_RIFLE = {
    "name": "Frontier Hunting Rifle",
    "category": "Rifle",
    "ammo": 100,
    "decay": 0.0002,
    "aliases": ["Frontier Rifle"],
    "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
    "max_tt": 2.0,
    "min_tt": 0.0,
}
FRONTIER_COMBAT_KNIFE = {
    "name": "Frontier Combat Knife",
    "category": "Knife",
    "ammo": 49,
    "decay": 0.0002,
    "aliases": [],
    "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
    "max_tt": 2.0,
    "min_tt": 0.0,
}
FRONTIER_COMBAT_KNIFE_ADJUSTED = {
    "name": "Frontier Combat Knife, Adjusted",
    "category": "Knife",
    "ammo": 98,
    "decay": 0.0002,
    "aliases": [],
    "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
    "max_tt": 2.0,
    "min_tt": 2e-05,
}
ZX_SINKADUS_TT = {"max_tt": 0.5, "min_tt": 0.001}

MINING_FINDERS = [
    {"name": "Locator MK1 (L)", "category": "Mining Finder", "ammo": 10, "decay": 0.00239, "source_name": "Entropia Nexus", "max_tt": 0.1, "min_tt": 0.002},
    {"name": "A.R.C. Finder 0001 (L)", "category": "Mining Finder", "ammo": 100, "decay": 0.0025, "source_name": "Entropia Nexus", "max_tt": 0.1, "min_tt": 0.00251},
    {"name": "Finder F-101", "category": "Mining Finder", "ammo": 100, "decay": 0.01, "source_name": "Entropia Nexus", "max_tt": 5.5, "min_tt": 0.165},
    {"name": "Finder F-102", "category": "Mining Finder", "ammo": 100, "decay": 0.0115, "source_name": "Entropia Nexus", "max_tt": 30.0, "min_tt": 0.9},
    {"name": "Finder F-103", "category": "Mining Finder", "ammo": 100, "decay": 0.0145, "source_name": "Entropia Nexus", "max_tt": 55.0, "min_tt": 1.5},
    {"name": "Finder F-104", "category": "Mining Finder", "ammo": 100, "decay": 0.01632, "source_name": "Entropia Nexus", "max_tt": 66.6, "min_tt": 1.998},
    {"name": "Finder F-105", "category": "Mining Finder", "ammo": 100, "decay": 0.0205, "source_name": "Entropia Nexus", "max_tt": 82.0, "min_tt": 2.46},
    {"name": "Finder F-210 (L)", "category": "Mining Finder", "ammo": 100, "decay": 0.01211, "source_name": "Entropia Nexus", "max_tt": 105.2, "min_tt": 3.15},
    {"name": "Finder F-211 (L)", "category": "Mining Finder", "ammo": 100, "decay": 0.01306, "source_name": "Entropia Nexus", "max_tt": 121.2, "min_tt": 3.636},
    {"name": "Finder F-212 (L)", "category": "Mining Finder", "ammo": 100, "decay": 0.01343, "source_name": "Entropia Nexus", "max_tt": 155.2, "min_tt": 4.656},
    {"name": "Finder F-213 (L)", "category": "Mining Finder", "ammo": 100, "decay": 0.0166, "source_name": "Entropia Nexus", "max_tt": 201.2, "min_tt": 6.036},
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ped-hunter", description="PED Hunter - local-first Entropia profit tracking")
    sub = parser.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed-data", help="Download and normalize LootNanny seed data")
    seed.add_argument("--force", action="store_true", help="Overwrite existing catalog files")

    monitor = sub.add_parser("monitor", help="Follow an Entropia chat log and record events")
    monitor.add_argument("--chat-log", required=True, help="Path to the Entropia chat log")
    monitor.add_argument("--db", default=None, help="Path to the PED Hunter sqlite DB")
    monitor.add_argument("--activity", default="hunt", choices=["hunt", "craft", "mine"], help="Session type")
    monitor.add_argument("--once", action="store_true", help="Process the file once and exit")

    stats = sub.add_parser("stats", help="Show recent session stats")
    stats.add_argument("--db", default=None, help="Path to the PED Hunter sqlite DB")
    stats.add_argument("--limit", type=int, default=5)

    weapon = sub.add_parser("weapon", help="Look up a weapon in the catalog")
    weapon.add_argument("query", help="Weapon name or alias")

    sub.add_parser("gui", help="Launch the Tkinter dashboard")

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["gui"]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "seed-data":
        return _seed_data(args.force)
    if args.cmd == "monitor":
        return _monitor(Path(args.chat_log), Path(args.db) if args.db else None, args.activity, args.once)
    if args.cmd == "stats":
        return _stats(Path(args.db) if args.db else None, args.limit)
    if args.cmd == "weapon":
        return _weapon_lookup(args.query)
    if args.cmd == "gui":
        return _launch_gui()
    return 1


def _gui_lock_path() -> Path:
    return Path.home() / "AppData" / "Local" / "ped-hunter" / "PED-Hunter.gui.lock"


@contextmanager
def _single_gui_instance():
    """Hold an OS lock so two GUI workers cannot ingest the same chat log."""
    lock_path = _gui_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        try:
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (ImportError, OSError):
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (ImportError, OSError):
                raise RuntimeError("another PED Hunter GUI instance is already running") from None
        yield
    finally:
        try:
            handle.seek(0)
            if sys.platform.startswith("win"):
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def _launch_gui() -> int:
    try:
        with _single_gui_instance():
            from .app import main as gui_main
            return gui_main()
    except RuntimeError as exc:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning("PED Hunter already running", str(exc))
            root.destroy()
        except Exception:
            pass
        return 1
    except Exception as exc:  # pragma: no cover - defensive desktop startup guard
        log_path = Path.home() / "AppData" / "Local" / "ped-hunter" / "crash.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("PED Hunter crashed", f"{exc}\n\nCrash log written to:\n{log_path}")
            root.destroy()
        except Exception:
            pass
        raise


def _seed_data(force: bool) -> int:
    root = Path(__file__).resolve().parents[2]
    out = root / "data" / "catalog"
    out.mkdir(parents=True, exist_ok=True)

    payloads = {
        "weapons.json": _download_json("weapons.json"),
        "attachments.json": _download_json("attachments.json"),
        "scopes.json": _download_json("scopes.json"),
        "sights.json": _download_json("sights.json"),
        "resources.json": _download_json("resources.json"),
        "crafting.json": _download_json("crafting.json"),
    }

    normalized = _normalize(payloads)
    for filename, payload in normalized.items():
        dst = out / filename
        if dst.exists() and not force:
            continue
        dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Seed data written to {out}")
    return 0


def _monitor(chat_log: Path, db_path: Path | None, activity: str, once: bool) -> int:
    store = Store(db_path)
    catalog = Catalog.load()
    session_id = store.start_session(activity)
    print(f"Started session {session_id} ({activity})")
    last_size = 0

    def process_new_lines(lines: list[str]) -> None:
        nonlocal last_size
        for raw in lines:
            event = parse_line(raw)
            if not event:
                continue
            store.add_event(session_id, event.to_row())
            _print_event(event, catalog)

    while True:
        if not chat_log.exists():
            print(f"Waiting for chat log: {chat_log}")
            time.sleep(1)
            continue
        size = chat_log.stat().st_size
        if size < last_size:
            last_size = 0
        if size > last_size:
            with chat_log.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(last_size)
                new_lines = fh.readlines()
                last_size = fh.tell()
            process_new_lines(new_lines)
        if once:
            break
        time.sleep(1)

    store.end_session(session_id)
    print(f"Ended session {session_id}")
    return 0


def _stats(db_path: Path | None, limit: int) -> int:
    store = Store(db_path)
    sessions = store.list_recent_sessions(limit)
    if not sessions:
        print("No sessions yet.")
        return 0
    for s in sessions:
        status = "active" if s.ended_at is None else "ended"
        print(
            f"{s.session_id} | {status} | {s.activity} | events={s.events} | "
            f"loot={s.loot_value:.2f} PED | cost={s.hunting_cost:.2f} PED | "
            f"net={s.net_value:+.2f} PED | combat={s.combat_damage:.2f}"
        )
    return 0


def _weapon_lookup(query: str) -> int:
    catalog = Catalog.load()
    weapon = catalog.find_weapon(query)
    if not weapon:
        print(f"No weapon found for {query!r}")
        return 1
    print(f"Name: {weapon.name}")
    print(f"Category: {weapon.category}")
    print(f"Ammo: {weapon.ammo}")
    print(f"Decay: {weapon.decay}")
    print(f"Cost/shot: {weapon.cost_per_shot:.5f}")
    if weapon.aliases:
        print(f"Aliases: {', '.join(weapon.aliases)}")
    return 0


def _download_json(filename: str) -> dict:
    url = LOOTNANNY_RAW_BASE + filename
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize(payloads: dict[str, dict]) -> dict[str, dict]:
    weapons = payloads["weapons.json"]["data"]
    attachments = payloads["attachments.json"]["data"]
    scopes = payloads["scopes.json"]["data"]
    sights = payloads["sights.json"]["data"]
    resources = payloads["resources.json"]["data"]
    crafting = payloads["crafting.json"]["data"]

    weapon_items = []
    for name, item in weapons.items():
        weapon_items.append(
            {
                "name": name,
                "category": item["type"],
                "ammo": int(item["ammo"]),
                "decay": float(item["decay"]),
                "aliases": [],
                "source_name": "LootNanny legacy seed",
            }
        )
    weapon_items.extend(
        [
            FRONTIER_COMBAT_KNIFE.copy(),
            FRONTIER_COMBAT_KNIFE_ADJUSTED.copy(),
            FRONTIER_HUNTING_RIFLE.copy(),
        ]
    )
    weapon_items.extend(item.copy() for item in MINING_FINDERS)

    attachment_items = []
    for source in (attachments, scopes, sights):
        for name, item in source.items():
            record = {
                "name": name,
                "category": item["type"],
                "ammo": int(item["ammo"]),
                "decay": float(item["decay"]),
                "source_name": "LootNanny legacy seed",
            }
            if name == "ZX Sinkadus":
                record.update(ZX_SINKADUS_TT)
            attachment_items.append(record)

    resource_items = [
        {"name": name, "tt_value": float(value), "source_name": "LootNanny legacy seed"}
        for name, value in resources.items()
    ]
    blueprint_items = [
        {"name": name, "materials": materials, "source_name": "LootNanny legacy seed"}
        for name, materials in crafting.items()
    ]
    aliases = {"Frontier Rifle": "Frontier Hunting Rifle"}
    return {
        "weapons.json": {"items": sorted(weapon_items, key=lambda x: x["name"].casefold())},
        "attachments.json": {"items": sorted(attachment_items, key=lambda x: x["name"].casefold())},
        "resources.json": {"items": sorted(resource_items, key=lambda x: x["name"].casefold())},
        "crafting.json": {"items": sorted(blueprint_items, key=lambda x: x["name"].casefold())},
        "aliases.json": aliases,
    }


def _print_event(event, catalog: Catalog) -> None:
    if event.kind == "loot":
        item = event.payload.get("item_name", "?")
        resolved = catalog.resolve_weapon_name(item) or item
        value = event.payload.get("value", 0.0)
        qty = event.payload.get("quantity", 1)
        print(f"[LOOT] {qty} x {resolved} ({value:.2f} PED)")
    elif event.kind == "combat":
        print(f"[COMBAT] {event.payload}")
    elif event.kind == "weapon":
        print(f"[WEAPON] {event.payload.get('weapon')}")
    elif event.kind == "skill":
        print(f"[SKILL] {event.payload.get('skill')}: +{event.payload.get('xp')} XP")
    elif event.kind == "craft":
        print(f"[CRAFT] {event.payload.get('result')} {event.payload.get('item')}")
    elif event.kind in {"chat", "global"}:
        channel = event.payload.get("channel", "?")
        speaker = event.payload.get("speaker", "")
        message = event.payload.get("message", "")
        prefix = f"[{channel}]"
        if speaker:
            prefix += f" [{speaker}]"
        print(f"{prefix} {message}".rstrip())


if __name__ == "__main__":
    raise SystemExit(main())
