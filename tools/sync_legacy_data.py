
"""Fetch and normalize the LootNanny data files into PED Hunter's catalog."""
from __future__ import annotations

from pathlib import Path
import json
import urllib.request

RAW_BASE = "https://raw.githubusercontent.com/euloggeradmin/LootNanny/main/data/raw/"


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
    weapons.append({
        "name": "Frontier Hunting Rifle",
        "category": "Rifle",
        "ammo": 100,
        "decay": 0.0002,
        "aliases": ["Frontier Rifle"],
        "source_name": "EntropiaWiki/Entropia Nexus supplemental seed",
    })

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
