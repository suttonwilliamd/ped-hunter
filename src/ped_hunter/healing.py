"""Healing inputs, separate from weapon setups and inferred creature costs.

Chat identifies outgoing healing, not equipment/activation IDs. Only supported
instant tools are priced, explicitly assuming the selected unmodified tool.
"""
from __future__ import annotations

from decimal import Decimal
import json
import math
import urllib.request

from .catalog import HealingToolRecord
from .parser import ParsedEvent

NEXUS_API = "https://api.entropianexus.com"
HEALING_SOURCES = ("/medicaltools", "/medicalchips", "/materials/1447", "/materials/1288")
PERIODIC_EFFECTS = {"heal over time", "regeneration per second", "lifesteal added"}


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def normalize_healing_catalog(tools: list[dict], chips: list[dict], materials: list[dict]) -> dict:
    """Nexus decay PEC -> PED; AmmoBurn units use the material's TT PED/unit.

    Preserve missing source values. Do not infer an activation from every HoT
    tick/area recipient or guess the behavior of a newly introduced chip.
    """
    unit_values = {item["Name"]: _number(item["Properties"]["Economy"].get("MaxTT")) for item in materials}
    items = {}
    for category, rows in (("MedicalTool", tools), ("MedicalChip", chips)):
        for item in rows:
            props = item["Properties"]
            if props.get("MaxHeal") == 0:  # Resurrection is not a heal-use receipt.
                continue
            economy = props["Economy"]
            decay = _number(economy.get("Decay"))
            burn = _number(economy.get("AmmoBurn")) if category == "MedicalChip" or "AmmoBurn" in economy else 0.0
            ammo = int(burn) if burn is not None and burn.is_integer() else None
            ammo_name = (item.get("Ammo") or {}).get("Name")
            rate = _number(props.get("UsesPerMinute"))
            effects = {effect["Name"].casefold() for key in ("EffectsOnUse", "EffectsOnEquip") for effect in item.get(key, [])}
            limit = None
            if effects & PERIODIC_EFFECTS:
                limit = "Periodic healing does not identify tool activations."
            elif category == "MedicalChip" and item["Name"].startswith("Regeneration Field Chip "):
                limit = "Area healing can report multiple targets per activation."
            elif category == "MedicalChip" and not item["Name"].startswith("Regeneration Chip "):
                limit = "This chip's activation messages are not verified."
            elif rate is None or rate == 0 or "EffectsOnUse" not in item or "EffectsOnEquip" not in item:
                limit = "Activation metadata is missing."
            elif rate > 60:
                limit = "Sub-second uses cannot be distinguished from duplicate messages."
            record = {
                "name": item["Name"], "category": category,
                "decay": None if decay is None else float(Decimal(str(decay)) / 100),
                "ammo": ammo, "ammo_name": ammo_name,
                "ammo_unit_value": unit_values.get(ammo_name) if ammo else None,
                "tracking_limit": limit, "source_name": "Entropia Nexus API",
                "source_url": NEXUS_API + item["Links"]["$Url"],
            }
            key = item["Name"].casefold()
            if key in items and items[key] != record:
                raise ValueError(f"Conflicting healing catalog entries: {item['Name']}")
            items[key] = record
    return {
        "schema_version": 1,
        "source_urls": [NEXUS_API + path for path in HEALING_SOURCES],
        "units": {"decay": "PED/use", "ammo": "units/use", "ammo_unit_value": "PED/unit"},
        "items": sorted(items.values(), key=lambda item: item["name"].casefold()),
    }


def fetch_healing_catalog() -> dict:
    """Import only; the running dashboard uses the bundled local catalog."""
    responses = []
    for path in HEALING_SOURCES:
        with urllib.request.urlopen(NEXUS_API + path, timeout=60) as response:
            responses.append(json.loads(response.read().decode("utf-8")))
    return normalize_healing_catalog(responses[0], responses[1], responses[2:])


def estimate_healing_cost(event: ParsedEvent, selected_name: str | None, tool: HealingToolRecord | None) -> None:
    """Snapshot new outgoing healing only; never reprice stored history.

    Unmarked concurrent HoT/lifesteal cannot be distinguished from these generic
    messages. This is a selected-equipment assumption, not tool detection.
    """
    if event.kind != "combat" or event.payload.get("healing_action") not in {"self", "other"}:
        return
    payload = event.payload
    for key in ("healing_cost", "healing_decay_cost", "healing_ammo_cost", "healing_cost_estimated", "healing_limit", "healing_source"):
        payload.pop(key, None)
    payload["healing_tool"] = selected_name
    if payload["healed"] <= 0:
        status = "no_positive_heal"
    elif selected_name is None:
        status = "not_selected"
    elif tool is None:
        status = "tool_not_found"
    elif not event.timestamp:
        status = "missing_timestamp"
    elif tool.tracking_limit:
        status = "unresolved_activation"
        payload["healing_limit"] = tool.tracking_limit
    elif tool.cost_per_use is None:
        status = "unknown_cost"
    else:
        status = "selected_tool_estimate"
        payload.update({
            "healing_cost": tool.cost_per_use,
            "healing_decay_cost": tool.decay,
            "healing_ammo_cost": tool.ammo * tool.ammo_unit_value if tool.ammo else 0.0,
            "healing_cost_estimated": True,
        })
    payload["healing_cost_status"] = status
    if tool:
        payload["healing_source"] = tool.source_url
