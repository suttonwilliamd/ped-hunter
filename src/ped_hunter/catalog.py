
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
class HealingToolRecord:
    """Unmodified tool TT inputs; unknown source values are not free."""
    name: str
    decay: float | None
    ammo: int | None = 0
    ammo_name: str | None = None
    ammo_unit_value: float | None = None
    category: str = "MedicalTool"
    tracking_limit: str | None = None
    source_name: str = "Entropia Nexus API"
    source_url: str = ""
    heal_min: float | None = None
    heal_max: float | None = None
    uses_per_minute: float | None = None
    cooldown: float | None = None
    has_hot: bool = False
    hot_fraction: float | None = None
    hot_duration: float | None = None
    hot_trigger: str | None = None
    behavior_source: str = ""
    cost_verified: bool = True
    verification_note: str = ""
    max_tt: float | None = None
    secondary_healing: bool = False
    # "direct_receipt": every use prints one direct-heal line (the HoT marker appears only when the
    # effect starts), so each matching receipt is one activation and smaller tick lines are never charged.
    hot_activation: str | None = None
    hot_tick_interval: float | None = None

    @property
    def cost_per_use(self) -> float | None:
        if not self.cost_verified or self.decay is None or self.ammo is None:
            return None
        if self.ammo == 0:
            return self.decay
        if self.ammo_unit_value is None:
            return None
        return self.decay + self.ammo * self.ammo_unit_value


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
        healing_tools: dict[str, HealingToolRecord] | None = None,
    ) -> None:
        self.weapons = weapons
        self.attachments = attachments
        self.resources = resources
        self.blueprints = blueprints
        self.aliases = aliases
        self.healing_tools = healing_tools if healing_tools is not None else {}

    @classmethod
    def load(cls, root: Path | None = None) -> "Catalog":
        root = root or _default_catalog_root()
        weapons, aliases = _load_weapons(root / "weapons.json")
        attachments = _load_attachment_records(root / "attachments.json")
        resources = _load_resource_records(root / "resources.json")
        blueprints = _load_blueprint_records(root / "crafting.json")
        healing_tools = _load_healing_records(root / "healing_tools.json")
        _merge_aliases(aliases, root / "aliases.json")
        return cls(
            weapons=weapons,
            attachments=attachments,
            resources=resources,
            blueprints=blueprints,
            aliases=aliases,
            healing_tools=healing_tools,
        )

    def exact_weapon_name(self, query: str) -> str | None:
        """The catalog name ``query`` names exactly (any case) or by alias; never a partial match.

        An exact-case name wins, so names differing only in case (legacy "RIP"/"Rip"
        entries) each resolve to themselves.
        """
        text = query.strip()
        if text in self.weapons:
            return text
        key = text.casefold()
        return self._weapon_key_map().get(key) or self.aliases.get(key)

    def weapon_candidates(self, query: str) -> list[str]:
        """The one exact name for ``query``, else every name containing it (any case)."""
        exact = self.exact_weapon_name(query)
        if exact:
            return [exact]
        key = query.casefold().strip()
        return [name for name in self.weapons if key and key in name.casefold()]

    def resolve_weapon_name(self, query: str) -> str | None:
        exact = self.exact_weapon_name(query)
        if exact:
            return exact
        key = query.casefold().strip()
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


_HEALING_BEHAVIOR_KEYS = frozenset(
    {"has_hot", "hot_activation", "hot_tick_interval", "hot_fraction", "hot_duration", "hot_trigger",
     "behavior_source"})


def _load_healing_records(path: Path) -> dict[str, HealingToolRecord]:
    raw = _load_json(path) or {"items": []}
    verification = _load_json(path.with_name("healing_verification.json")) or {}
    checks = {(item["source_url"], item["name"]): item for item in verification.get("items", [])}
    records = {}
    for item in raw.get("items", []):
        check = checks.get((item["source_url"], item["name"]), {})
        # Curated behavior the Nexus import cannot derive (e.g. a HoT stated only in prose).
        behavior = {key: value for key, value in (check.get("behavior") or {}).items()
                    if key in _HEALING_BEHAVIOR_KEYS}
        item = {**item, **behavior}
        record = HealingToolRecord(
            name=item["name"],
            decay=float(item["decay"]) if item.get("decay") is not None else None,
            ammo=int(item["ammo"]) if item.get("ammo") is not None else None,
            ammo_name=item.get("ammo_name"),
            ammo_unit_value=float(item["ammo_unit_value"]) if item.get("ammo_unit_value") is not None else None,
            category=item["category"],
            tracking_limit=item.get("tracking_limit", "Activation metadata is missing."),
            source_name=item.get("source_name", "Entropia Nexus API"),
            source_url=item["source_url"],
            heal_min=float(item["heal_min"]) if item.get("heal_min") is not None else None,
            heal_max=float(item["heal_max"]) if item.get("heal_max") is not None else None,
            uses_per_minute=float(item["uses_per_minute"]) if item.get("uses_per_minute") is not None else None,
            cooldown=float(item["cooldown"]) if item.get("cooldown") is not None else None,
            has_hot=bool(item.get("has_hot", False)),
            hot_fraction=float(item["hot_fraction"]) if item.get("hot_fraction") is not None else None,
            hot_duration=float(item["hot_duration"]) if item.get("hot_duration") is not None else None,
            hot_trigger=item.get("hot_trigger"),
            behavior_source=item.get("behavior_source", ""),
            cost_verified=bool(check.get("cost_verified", item.get("cost_verified", True))),
            verification_note=check.get("note", item.get("verification_note", "")),
            max_tt=float(check.get("max_tt", item.get("max_tt"))) if check.get("max_tt", item.get("max_tt")) is not None else None,
            secondary_healing=bool(item.get("secondary_healing", False)),
            hot_activation=item.get("hot_activation"),
            hot_tick_interval=float(item["hot_tick_interval"]) if item.get("hot_tick_interval") is not None else None,
        )
        records[record.name] = record
    return records


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


def merge_weapon_supplements(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep verified source snapshots when refreshing the legacy weapon feed."""
    source = _load_json(_default_catalog_root() / "weapon_supplements.json") or {}
    supplements = [
        {key: value for key, value in item.items()
         if key not in {"source_record", "ammo_source_record"}}
        for item in source.get("items", [])
    ]
    names = {item["name"].strip().casefold() for item in supplements}
    return [item for item in items if item["name"].strip().casefold() not in names] + supplements
