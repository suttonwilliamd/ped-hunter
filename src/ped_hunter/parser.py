
"""Chat log parsing for PED Hunter."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import re
from typing import Any


TIMESTAMP_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(?P<body>.+)$")
PATTERNS = {
    "loot": re.compile(r"\[System\] \[\]\s*You\s+received\s+(.+?)\s+x\s*\(?(\d+)\)?\s*Value:\s*([\d.]+)\s+PED"),
    "damage": re.compile(r"\[System\] \[\]\s*You\s+inflicted\s+([\d.]+)\s+points\s+of\s+damage"),
    "damage_taken": re.compile(r"\[System\] \[\]\s*You\s+took\s+([\d.]+)\s+points\s+of\s+damage"),
    "critical": re.compile(r"\[System\] \[\]\s*Critical\s+hit\s+-\s+Additional\s+damage!\s+You\s+inflicted\s+([\d.]+)\s+points\s+of\s+damage"),
    "critical_armor": re.compile(r"\[System\] \[\]\s*Critical\s+hit\s+-\s+Armor\s+penetration!\s+You\s+took\s+([\d.]+)\s+points\s+of\s+damage"),
    "miss": re.compile(r"\[System\] \[\]\s*The\s+attack\s+missed\s+you"),
    "dodge": re.compile(r"\[System\] \[\]\s*.*Dodged.*your\s+attack"),
    "evade": re.compile(r"\[System\] \[\]\s*You\s+Evaded\s+the\s+attack"),
    "heal": re.compile(r"\[System\] \[\]\s*You\s+healed\s+yourself\s+([\d.]+)\s+points"),
    "weapon": re.compile(r"\[System\] \[\]\s*You\s+equipped\s+(.+)$"),
    "skill": re.compile(r"\[System\] \[\]\s*You\s+have\s+gained\s+([\d.]+)\s+experience\s+in\s+your\s+(.+?)\s+skill"),
    "skill_gain": re.compile(r"\[System\] \[\]\s*You\s+have\s+gained\s+([\d.]+)\s+(.+)$"),
    "skill_alt": re.compile(r"\[System\] \[\]\s*You\s+gained\s+([\d.]+)\s+(.+)$"),
    "skill_improved": re.compile(r"\[System\] \[\]\s*Your\s+(.+?)\s+has\s+improved\s+by\s+([\d.]+)$"),
    "craft_success": re.compile(r"\[System\] \[\]\s*You\s+successfully\s+crafted\s+(.+)$"),
    "craft_fail": re.compile(r"\[System\] \[\]\s*You\s+failed\s+to\s+craft\s+(.+)$"),
    "picked_up": re.compile(r"\[System\] \[\]\s*Picked up (.+?)(?: \((\d+)\))?$"),
}


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
            return ParsedEvent(
                kind="loot",
                timestamp=timestamp,
                raw_message=line,
                payload={
                    "item_name": match.group(1),
                    "quantity": int(match.group(2)),
                    "value": float(match.group(3)),
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

    return None
