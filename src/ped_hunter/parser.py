
"""Chat log parsing for PED Hunter."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import re
from typing import Any


TIMESTAMP_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(?P<body>.+)$")
SYSTEM_PREFIX = r"\[System\](?:\s*:\s*|\s+\[\]\s*)"
PATTERNS = {
    "loot": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*received\s*(?:\[(?P<loot_item_bracketed>.+?)\]|(?P<loot_item_plain>.+?))\s*x\s*\(?([\d,]+)\)?\s*Value:\s*([\d.]+)\s*PED(?:\s+from\s+.+)?$"),
    "damage": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*inflicted\s*([\d.]+)\s*points\s*of\s*damage(?:\s*with\s*costs\s*of\s*[\d.]+\s*PED)?\.?$"),
    "damage_taken": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*took\s*([\d.]+)\s*points\s*of\s*damage(?:\s+into\s+\[.+?\])?\.?$"),
    "critical": re.compile(rf"{SYSTEM_PREFIX}\s*Critical\s*hit\s*-\s*Additional\s*damage!\s*You\s*inflicted\s*([\d.]+)\s*points\s*of\s*damage(?:\s*with\s*costs\s*of\s*[\d.]+\s*PED)?\.?$"),
    "critical_armor": re.compile(rf"{SYSTEM_PREFIX}\s*Critical\s*hit\s*-\s*Armor\s*penetration!\s*You\s*took\s*([\d.]+)\s*points\s*of\s*damage(?:\s+into\s+\[.+?\])?\.?$"),
    "miss": re.compile(rf"{SYSTEM_PREFIX}\s*The\s*attack\s*missed\s*you\.?$"),
    "dodge": re.compile(rf"{SYSTEM_PREFIX}\s*.*Dodged.*your\s*attack\.?$"),
    "evade": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*Evaded\s*the\s*attack\.?$"),
    "heal": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*healed\s*yourself\s*([\d.]+)\s*points\.?$"),
    "weapon": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*equipped\s*(.+)$"),
    "skill": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*have\s*gained\s*([\d.]+)\s*experience\s*in\s*your\s*(.+?)\s*skill"),
    "skill_gain": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*have\s*gained\s*([\d.]+)\s*(.+)$"),
    "skill_alt": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*gained\s*([\d.]+)\s*(.+)$"),
    "skill_improved": re.compile(rf"{SYSTEM_PREFIX}\s*Your\s*(.+?)\s*has\s*improved\s*by\s*([\d.]+)$"),
    "craft_success": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*successfully\s*crafted\s*(.+)$"),
    "craft_fail": re.compile(rf"{SYSTEM_PREFIX}\s*You\s*failed\s*to\s*craft\s*(.+)$"),
    "picked_up": re.compile(rf"{SYSTEM_PREFIX}\s*Picked\s*up\s*(.+?)(?:\s*\((\d+)\))?$"),
    "item_damaged": re.compile(rf"{SYSTEM_PREFIX}\s*The\s*item\s*is\s*damaged\.?$"),
    "repair": re.compile(rf"{SYSTEM_PREFIX}\s*Item\(s\)\s*repaired\s*successfully\.?$"),
}

CONVERSION_OUTPUT_ITEM_NAMES = {"oil", "universal ammo"}
CONVERSION_OUTPUT_ITEM_SUFFIXES = (" ingot",)


@dataclass(slots=True)
class ParsedEvent:
    kind: str
    timestamp: datetime | None
    raw_message: str
    payload: dict[str, Any]

    def to_row(self) -> dict[str, Any]:
        data = asdict(self)
        if self.timestamp is not None:
            data["timestamp"] = self.timestamp.isoformat(timespec="seconds")
        return data


def parse_line(line: str) -> ParsedEvent | None:
    line = line.strip()
    if not line:
        return None

    timestamp = None
    body = line
    m = TIMESTAMP_RE.match(line)
    if m:
        body = m.group("body")
        try:
            timestamp = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            timestamp = None

    for kind, pattern in PATTERNS.items():
        match = pattern.search(body)
        if not match:
            continue

        if kind == "loot":
            item_name = (match.group("loot_item_bracketed") or match.group("loot_item_plain") or "").strip()
            if is_conversion_output_item(item_name):
                return None
            return ParsedEvent(
                kind="loot",
                timestamp=timestamp,
                raw_message=line,
                payload={
                    "item_name": item_name,
                    "quantity": int((match.group(3) or "0").replace(",", "")),
                    "value": float(match.group(4)),
                    "is_personal": True,
                },
            )

        if kind in {"damage", "damage_taken", "critical", "critical_armor", "heal"}:
            key = {
                "damage": "damage",
                "damage_taken": "damage_taken",
                "critical": "damage",
                "critical_armor": "damage_taken",
                "heal": "healed",
            }[kind]
            return ParsedEvent(
                kind="combat",
                timestamp=timestamp,
                raw_message=line,
                payload={key: float(match.group(1))},
            )

        if kind == "miss":
            return ParsedEvent(kind="combat", timestamp=timestamp, raw_message=line, payload={"miss": True})
        if kind == "dodge":
            return ParsedEvent(kind="combat", timestamp=timestamp, raw_message=line, payload={"dodged": True})
        if kind == "evade":
            return ParsedEvent(kind="combat", timestamp=timestamp, raw_message=line, payload={"evaded": True})
        if kind == "weapon":
            return ParsedEvent(kind="weapon", timestamp=timestamp, raw_message=line, payload={"weapon": match.group(1)})
        if kind in {"skill", "skill_gain", "skill_alt"}:
            return ParsedEvent(kind="skill", timestamp=timestamp, raw_message=line, payload={"skill": match.group(2).strip(), "xp": float(match.group(1))})
        if kind == "skill_improved":
            return ParsedEvent(kind="skill", timestamp=timestamp, raw_message=line, payload={"skill": match.group(1).strip(), "xp": float(match.group(2))})
        if kind == "craft_success":
            return ParsedEvent(kind="craft", timestamp=timestamp, raw_message=line, payload={"result": "success", "item": match.group(1)})
        if kind == "craft_fail":
            return ParsedEvent(kind="craft", timestamp=timestamp, raw_message=line, payload={"result": "fail", "item": match.group(1)})
        if kind == "picked_up":
            qty = int(match.group(2) or 1)
            return ParsedEvent(kind="loot", timestamp=timestamp, raw_message=line, payload={"item_name": match.group(1), "quantity": qty, "value": 0.0, "picked_up": True})
        if kind == "item_damaged":
            return ParsedEvent(kind="equipment", timestamp=timestamp, raw_message=line, payload={"item_damaged": True})
        if kind == "repair":
            return ParsedEvent(kind="repair", timestamp=timestamp, raw_message=line, payload={"estimated_cost": 0.0, "resets_durability": True, "repair_reset": True})

    return None

def is_conversion_output_item(item_name: str) -> bool:
    """Return True for inventory conversion outputs, not newly earned loot.

    Entropia reports some refiner/ammo conversions as ordinary-looking
    ``You received ... Value: X PED`` lines. Those transform existing inventory
    value, so counting them as session return inflates profit.
    """
    normalized = " ".join(item_name.strip().casefold().split())
    return normalized in CONVERSION_OUTPUT_ITEM_NAMES or normalized.endswith(CONVERSION_OUTPUT_ITEM_SUFFIXES)
