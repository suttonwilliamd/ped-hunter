
"""Fetch and normalize the LootNanny data files into PED Hunter's catalog."""
from __future__ import annotations

from pathlib import Path
import json
import urllib.request

RAW_BASE = "https://raw.githubusercontent.com/euloggeradmin/LootNanny/main/data/raw/"
FRONTIER_HUNTING_RIFLE = {
    "name": "Frontier Hunting Rifle",
    "category": "Rifle",
    "ammo": 100,
    "decay": 0.0002,
    "aliases": ["Frontier Rifle"],
    "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
}
FRONTIER_COMBAT_KNIFE = {
    "name": "Frontier Combat Knife",
    "category": "Knife",
    "ammo": 49,
    "decay": 0.0002,
    "aliases": [],
    "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
}
FRONTIER_COMBAT_KNIFE_ADJUSTED = {
    "name": "Frontier Combat Knife, Adjusted",
    "category": "Knife",
    "ammo": 98,
    "decay": 0.0002,
    "aliases": [],
    "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
}
SET_P1_CIVILIAN_SIDEARM_ADJUSTED = {
    "name": "SET-P1 Civilian Sidearm, Adjusted",
    "category": "Pistol",
    "ammo": 7,
    "decay": 0.001,
    "aliases": [],
    "source_name": "Entropia Nexus supplemental seed",
    "max_tt": 0.1,
    "min_tt": 0.0,
}
SET_P2_SCOUT_SIDEARM = {
    "name": "SET-P2 Scout Sidearm (L)",
    "category": "Pistol",
    "ammo": 20,
    "decay": 0.001,
    "aliases": [],
    "source_name": "Entropia Nexus supplemental seed",
    "max_tt": 0.2,
    "min_tt": 0.0,
}

# Entropia Nexus lists these as Tools → Finders. PED Hunter reuses its
# per-use cost model: probe burn plus finder decay.
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


def download(name: str) -> dict:
    with urllib.request.urlopen(RAW_BASE + name, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out = root / "data" / "catalog"
    out.mkdir(parents=True, exist_ok=True)

    payloads = {name: download(name) for name in ["weapons.json", "attachments.json", "scopes.json", "sights.json", "resources.json", "crafting.json"]}

    weapons = []
    for name, item in payloads["weapons.json"]["data"].items():
        weapons.append({
            "name": name,
            "category": item["type"],
            "ammo": int(item["ammo"]),
            "decay": float(item["decay"]),
            "aliases": [],
            "source_name": "LootNanny legacy seed",
        })

    # The Frontier starter rifle is distinct from the older EWE LC-100 Frontier
    # and is not currently present in the legacy LootNanny seed files.
    weapons.extend([
        FRONTIER_COMBAT_KNIFE.copy(),
        FRONTIER_COMBAT_KNIFE_ADJUSTED.copy(),
        FRONTIER_HUNTING_RIFLE.copy(),
        SET_P1_CIVILIAN_SIDEARM_ADJUSTED.copy(),
        SET_P2_SCOUT_SIDEARM.copy(),
    ])
    weapons.extend(item.copy() for item in MINING_FINDERS)

    attachments = []
    for source_name in ["attachments.json", "scopes.json", "sights.json"]:
        for name, item in payloads[source_name]["data"].items():
            attachments.append({
                "name": name,
                "category": item["type"],
                "ammo": int(item["ammo"]),
                "decay": float(item["decay"]),
                "source_name": "LootNanny legacy seed",
            })

    resources = [
        {"name": name, "tt_value": float(value), "source_name": "LootNanny legacy seed"}
        for name, value in payloads["resources.json"]["data"].items()
    ]
    crafting = [
        {"name": name, "materials": materials, "source_name": "LootNanny legacy seed"}
        for name, materials in payloads["crafting.json"]["data"].items()
    ]

    (out / "weapons.json").write_text(json.dumps({"items": sorted(weapons, key=lambda x: x["name"].casefold())}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "attachments.json").write_text(json.dumps({"items": sorted(attachments, key=lambda x: x["name"].casefold())}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "resources.json").write_text(json.dumps({"items": sorted(resources, key=lambda x: x["name"].casefold())}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "crafting.json").write_text(json.dumps({"items": sorted(crafting, key=lambda x: x["name"].casefold())}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "aliases.json").write_text(json.dumps({"Frontier Rifle": "Frontier Hunting Rifle"}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    print(f"Wrote normalized catalog to {out}")
    print("Frontier Rifle alias -> Frontier Hunting Rifle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
