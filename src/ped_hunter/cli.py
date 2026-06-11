
"""Command-line entrypoint for PED Hunter."""
from __future__ import annotations

import argparse
from pathlib import Path
import time
import urllib.request
import json

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
}


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
        from .app import main as gui_main

        return gui_main()
    return 1


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
        print(f"{s.session_id} | {status} | {s.activity} | events={s.events} | loot={s.loot_value:.2f} PED | combat={s.combat_damage:.2f}")
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
    weapon_items.append(FRONTIER_HUNTING_RIFLE.copy())

    attachment_items = []
    for source in (attachments, scopes, sights):
        for name, item in source.items():
            attachment_items.append(
                {
                    "name": name,
                    "category": item["type"],
                    "ammo": int(item["ammo"]),
                    "decay": float(item["decay"]),
                    "source_name": "LootNanny legacy seed",
                }
            )

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


if __name__ == "__main__":
    raise SystemExit(main())
