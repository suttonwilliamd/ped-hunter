
"""Catalog loading and lookups for PED Hunter."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import sys


@dataclass(frozen=True, slots=True)
class WeaponRecord:
    name: str
    category: str
    ammo: int
    decay: float
    aliases: tuple[str, ...] = field(default_factory=tuple)
    source_name: str = "LootNanny legacy seed"
    max_tt: float | None = None
    min_tt: float = 0.0

    @property
    def cost_per_shot(self) -> float:
        return self.decay + (self.ammo / 10_000.0)


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    name: str
    category: str
    ammo: int
    decay: float
    source_name: str = "LootNanny legacy seed"
    max_tt: float | None = None
    min_tt: float = 0.0


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    name: str
    tt_value: float
    source_name: str = "LootNanny legacy seed"


@dataclass(frozen=True, slots=True)
class BlueprintRecord:
    name: str
    materials: tuple[tuple[str, int], ...]
    source_name: str = "LootNanny legacy seed"


class Catalog:
    def __init__(
        self,
        *,
        weapons: dict[str, WeaponRecord],
        attachments: dict[str, AttachmentRecord],
        resources: dict[str, ResourceRecord],
        blueprints: dict[str, BlueprintRecord],
        aliases: dict[str, str],
    ) -> None:
        self.weapons = weapons
        self.attachments = attachments
        self.resources = resources
        self.blueprints = blueprints
        self.aliases = aliases

    @classmethod
    def load(cls, root: Path | None = None) -> "Catalog":
        root = root or _default_catalog_root()
        weapons, aliases = _load_weapons(root / "weapons.json")
        attachments = _load_attachment_records(root / "attachments.json")
        resources = _load_resource_records(root / "resources.json")
        blueprints = _load_blueprint_records(root / "crafting.json")
        _merge_aliases(aliases, root / "aliases.json")
        return cls(
            weapons=weapons,
            attachments=attachments,
            resources=resources,
            blueprints=blueprints,
            aliases=aliases,
        )

    def resolve_weapon_name(self, query: str) -> str | None:
        key = query.casefold().strip()
        if key in self._weapon_key_map():
            return self._weapon_key_map()[key]
        alias = self.aliases.get(key)
        if alias:
            return alias
        for name in self.weapons:
            if key in name.casefold():
                return name
        return None

    def find_weapon(self, query: str) -> WeaponRecord | None:
        resolved = self.resolve_weapon_name(query)
        if resolved:
            return self.weapons.get(resolved)
        return None

    def find_blueprint(self, query: str) -> BlueprintRecord | None:
        key = query.casefold().strip()
        if not key:
            return None
        for name, blueprint in self.blueprints.items():
            if name.casefold() == key:
                return blueprint
        for name, blueprint in self.blueprints.items():
            if key in name.casefold():
                return blueprint
        return None

    def _weapon_key_map(self) -> dict[str, str]:
        return {name.casefold(): name for name in self.weapons}


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _default_catalog_root() -> Path:
    """Return the catalog root for source checkouts and PyInstaller bundles."""
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / "data" / "catalog"
    return Path(__file__).resolve().parents[2] / "data" / "catalog"


def _load_weapons(path: Path) -> tuple[dict[str, WeaponRecord], dict[str, str]]:
    raw = _load_json(path) or {"items": []}
    weapons: dict[str, WeaponRecord] = {}
    aliases: dict[str, str] = {}
    for item in raw.get("items", []):
        record = WeaponRecord(
            name=item["name"],
            category=item["category"],
            ammo=int(item["ammo"]),
            decay=float(item["decay"]),
            aliases=tuple(item.get("aliases", [])),
            source_name=item.get("source_name", "LootNanny legacy seed"),
            max_tt=float(item["max_tt"]) if item.get("max_tt") is not None else None,
            min_tt=float(item.get("min_tt", 0) or 0),
        )
        weapons[record.name] = record
        for alias in record.aliases:
            aliases[alias.casefold()] = record.name
    return weapons, aliases


def _load_attachment_records(path: Path) -> dict[str, AttachmentRecord]:
    raw = _load_json(path) or {"items": []}
    attachments: dict[str, AttachmentRecord] = {}
    for item in raw.get("items", []):
        record = AttachmentRecord(
            name=item["name"],
            category=item["category"],
            ammo=int(item["ammo"]),
            decay=float(item["decay"]),
            source_name=item.get("source_name", "LootNanny legacy seed"),
            max_tt=float(item["max_tt"]) if item.get("max_tt") is not None else None,
            min_tt=float(item.get("min_tt", 0) or 0),
        )
        attachments[record.name] = record
    return attachments


def _load_resource_records(path: Path) -> dict[str, ResourceRecord]:
    raw = _load_json(path) or {"items": []}
    resources: dict[str, ResourceRecord] = {}
    for item in raw.get("items", []):
        record = ResourceRecord(
            name=item["name"],
            tt_value=float(item["tt_value"]),
            source_name=item.get("source_name", "LootNanny legacy seed"),
        )
        resources[record.name] = record
    return resources


def _load_blueprint_records(path: Path) -> dict[str, BlueprintRecord]:
    raw = _load_json(path) or {"items": []}
    blueprints: dict[str, BlueprintRecord] = {}
    for item in raw.get("items", []):
        materials = tuple((mat[0], int(mat[1])) for mat in item.get("materials", []))
        record = BlueprintRecord(
            name=item["name"],
            materials=materials,
            source_name=item.get("source_name", "LootNanny legacy seed"),
        )
        blueprints[record.name] = record
    return blueprints


def _merge_aliases(target: dict[str, str], path: Path) -> None:
    raw = _load_json(path)
    if not raw:
        return
    for alias, canonical in raw.items():
        target[alias.casefold()] = canonical
