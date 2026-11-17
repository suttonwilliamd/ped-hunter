"""Selected-profile healing estimates; log evidence is not tool/caster identity.

Only new observations receive a selection snapshot. Store finalizes inference
and its persistent state in the same transaction as the immutable event.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import urllib.request

from .catalog import HealingToolRecord
from .parser import ParsedEvent

NEXUS_API = "https://api.entropianexus.com"
HEALING_SOURCES = ("/medicaltools", "/medicalchips", "/materials/1447", "/materials/1288")
PERIODIC_EFFECTS = {"heal over time", "regeneration per second", "lifesteal added"}
HOT_EFFECTS = {"heal over time", "regeneration per second"}
RESTORATION_BEHAVIOR_SOURCE = "https://forum.entropiauniverse.com/t/modern-mindforce-guide-for-2020/255948"
# An effect whose duration is unknown (generic marker without a selected HoT, or another
# avatar's effect) blocks pricing only this long after its last evidence: the longest HoT
# duration in the bundled catalog (Restoration Chip, Modified: 60 s). It used to be a
# permanent flag, which left every later heal of the run unpriced.
UNKNOWN_EFFECT_WINDOW = 60.0


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
            on_use = [effect for effect in item.get("EffectsOnUse", []) if effect["Name"].casefold() in HOT_EFFECTS]
            on_equip = [effect for effect in item.get("EffectsOnEquip", []) if effect["Name"].casefold() in HOT_EFFECTS]
            restoration = category == "MedicalChip" and item["Name"].startswith("Restoration Chip ")
            hot = (on_use[0].get("Values") or {}) if len(on_use) == 1 else {}
            fraction = _number(hot.get("Strength")) if hot.get("Unit") == "%" else None
            duration = _number(hot.get("DurationSeconds"))
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
                "heal_min": _number(props.get("MinHeal")), "heal_max": _number(props.get("MaxHeal")),
                "uses_per_minute": rate,
                "cooldown": _number((props.get("Mindforce") or {}).get("Cooldown")),
                "max_tt": _number(economy.get("MaxTT")),
                "secondary_healing": bool(effects & {"lifesteal added"}),
                "has_hot": bool(on_use or on_equip or restoration),
                "hot_fraction": fraction / 100 if fraction is not None and 0 < fraction <= 100 else None,
                "hot_duration": duration if duration else None,
                "hot_trigger": "use" if on_use or restoration else "equip" if on_equip else None,
                "behavior_source": NEXUS_API + item["Links"]["$Url"] if on_use or on_equip else RESTORATION_BEHAVIOR_SOURCE if restoration else "",
            }
            key = item["Name"].casefold()
            if key in items and items[key] != record:
                raise ValueError(f"Conflicting healing catalog entries: {item['Name']}")
            items[key] = record
    return {
        "schema_version": 2,
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


def estimate_healing_cost(
    event: ParsedEvent, selected_name: str | None, tool: HealingToolRecord | None,
    hot_name: str | None, hot_tool: HealingToolRecord | None,
) -> None:
    """Capture choices only. Persistent inference belongs inside Store's write."""
    if event.kind != "combat" or "healing_action" not in event.payload:
        return
    def snapshot(name, record):
        return asdict(record) if record is not None else {"name": name}
    event.payload["healing_tool"] = selected_name
    event.payload["healing_selection"] = {
        "version": 2, "heal": snapshot(selected_name, tool), "hot": snapshot(hot_name, hot_tool),
    }


def _time(value) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _cost(profile: dict) -> tuple[float, float] | None:
    if not profile.get("cost_verified", True):
        return None
    decay, ammo = _number(profile.get("decay")), _number(profile.get("ammo"))
    unit = _number(profile.get("ammo_unit_value"))
    if decay is None or ammo is None or (ammo and unit is None):
        return None
    return decay, ammo * unit if ammo else 0.0


def _range(profile: dict, amount: float, fraction: float = 1.0) -> bool:
    low, high = _number(profile.get("heal_min")), _number(profile.get("heal_max"))
    # The real log prints one decimal; tolerate only that quantization.
    return low is not None and high is not None and low * fraction - .050001 <= amount <= high * fraction + .050001


def _unpriced(payload: dict, status: str, reason: str) -> None:
    payload["healing_cost_status"] = status
    payload["healing_limit"] = reason


def _book(payload: dict, profile: dict, kind: str, anchor: int, eligible: bool, reason: str = "") -> bool:
    costs = _cost(profile)
    payload["healing_tool"] = profile.get("name")
    payload["healing_source"] = profile.get("source_url", "")
    if costs is None:
        _unpriced(payload, "unknown_cost", profile.get("verification_note") or "Decay or consumption is unknown.")
        return False
    decay, ammo = costs
    if profile.get("secondary_healing"):
        eligible = False
        reason = reason or "Selected profile also produces secondary healing; recipient is uncertain."
    payload.update({
        "healing_cost": decay + ammo, "healing_decay_cost": decay, "healing_ammo_cost": ammo,
        "healing_cost_estimated": True, "healing_cost_status": "selected_tool_estimate",
        "healing_kind": kind, "healing_activation_event_id": anchor,
        "healing_allocation_eligible": eligible, "healing_allocation_reason": reason,
        "healing_inference": "Selected-profile estimate; log does not identify the tool or caster reliably.",
    })
    return True


def _direct_receipt_profiles(*profiles: dict) -> list[dict]:
    """Selected HoT tools whose every use prints one direct receipt; one entry per tool name."""
    found = {}
    for profile in profiles:
        if profile.get("name") and profile.get("has_hot") and profile.get("hot_activation") == "direct_receipt":
            found.setdefault(profile["name"], profile)
    return list(found.values())


def _tick_bound(profile: dict) -> float | None:
    """Largest possible tick line: every concurrently stacked cast ticking in the same message."""
    fraction, duration = _number(profile.get("hot_fraction")), _number(profile.get("hot_duration"))
    interval, rate = _number(profile.get("hot_tick_interval")), _number(profile.get("uses_per_minute"))
    maximum, minimum = _number(profile.get("heal_max")), _number(profile.get("heal_min"))
    if not (fraction and duration and interval and rate and maximum and minimum is not None) or rate > 60:
        return None
    stacks = math.floor(duration * rate / 60) + 1
    bound = stacks * fraction * maximum * min(1.0, interval / duration) + .050001
    # Usable only when a tick can never be mistaken for the smallest direct receipt.
    return bound if bound < minimum * (1 - fraction) - .050001 else None


def _finalize_direct_receipt(payload, state, direct, heal, hot, amount, now, event_id, action, diminished) -> None:
    """One charge per direct receipt of a direct-receipt HoT tool; ticks and markers are never charged.

    Independent of the effect-marker lifecycle: Vivo-style tools print the marker only when the
    effect starts, so refresh casts and casts on others are visible only as direct receipts.
    """
    casts = state.setdefault("direct_casts", {})
    bounds = {}
    for profile in direct:
        bounds[profile["name"]] = _tick_bound(profile)
        if bounds[profile["name"]] is None:
            _unpriced(payload, "unresolved_activation",
                      f"{profile['name']} direct-receipt metadata is incomplete or ticks overlap direct heals; not charged.")
            return
    tick_of = [p for p in direct if amount <= bounds[p["name"]]]
    if tick_of:
        recent = [(casts[p["name"]], p) for p in tick_of if p["name"] in casts
                  and 0 <= now - casts[p["name"]][0] <= _number(p["hot_duration"]) + _number(p["hot_tick_interval"])]
        if recent:
            (_, anchor), profile = max(recent, key=lambda item: item[0][0])
            payload.update({"healing_cost_status": "hot_tick", "healing_activation_event_id": anchor,
                            "healing_tool": profile["name"]})
        else:
            _unpriced(payload, "unresolved_activation",
                      "Tick-sized receipt without a recent priced cast (capped/diminished cast or other source); never charged.")
        return
    names = {p["name"] for p in direct}
    matches = [("hot", p) for p in direct if _range(p, amount, 1 - p["hot_fraction"])]
    ambiguous = False
    for profile in (heal, hot):
        if not profile.get("name") or profile["name"] in names:
            continue
        names.add(profile["name"])
        if profile.get("has_hot") or profile.get("tracking_limit"):
            maximum = _number(profile.get("heal_max"))
            ambiguous = ambiguous or maximum is None or amount <= maximum + .050001
        elif _range(profile, amount):
            matches.append(("instant", profile))
    if diminished:
        _unpriced(payload, "unresolved_activation", "Diminished receipt cannot be matched to a tool's heal range; not charged.")
    elif ambiguous or len(matches) > 1:
        _unpriced(payload, "unresolved_activation", "Receipt fits more than one selected tool; not charged.")
    elif not matches:
        _unpriced(payload, "unresolved_activation",
                  "Receipt matches no selected tool's direct-heal range (capped or other source); not charged.")
    else:
        kind, profile = matches[0]
        other = "" if action == "self" else "Healing another avatar has no supported local loot recipient."
        if _book(payload, profile, kind, event_id, action == "self", other):
            payload["healing_inference"] = (
                "Selected-profile estimate: one activation per direct-heal receipt; HoT ticks are not charged."
                if kind == "hot" else payload["healing_inference"])
            if kind == "hot":
                casts[profile["name"]] = [now, event_id]


def _mark_unknown(state: dict, now: float | None) -> None:
    """Uncertainty from an effect of unknown duration, bounded in time."""
    if now is None:
        state["unknown_pending"] = True  # Window starts at the next timestamped observation.
    else:
        until = max(_number(state.get("uncertain_until")) or 0.0, now + UNKNOWN_EFFECT_WINDOW)
        state["uncertain_until"] = until


def _save_inference_state(conn, session_id, state, before):
    after = json.dumps(state, sort_keys=True)
    if after != before:
        conn.execute(
            "INSERT INTO healing_inference_state(session_id, state) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET state = excluded.state", (session_id, after))


def finalize_healing_event(conn, session_id: str, event_id: int, timestamp: str | None, payload: dict) -> None:
    """Finalize one new event after replay detection; never rewrite old costs."""
    selection = payload.get("healing_selection")
    if not isinstance(selection, dict) or selection.get("version") != 2:
        return
    row = conn.execute("SELECT state FROM healing_inference_state WHERE session_id = ?", (session_id,)).fetchone()
    try:
        state = json.loads(row["state"]) if row else {"version": 1}
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError
    except (ValueError, TypeError):
        _unpriced(payload, "unresolved_activation", "Persisted inference state is invalid; this observation is unpriced.")
        return
    before = json.dumps(state, sort_keys=True)
    action = payload.get("healing_action")
    if selection.get("enabled") is False:
        active = state.get("active")
        if active or action in {"effect_start", "received"}:
            _mark_unknown(state, _time(timestamp))
            if isinstance(active, dict):
                active["observed"] = True
                if active.get("booked_event_id"):
                    payload["healing_invalidates_activation_ids"] = [active["event_id"]]
        _save_inference_state(conn, session_id, state, before)
        _unpriced(payload, "not_selected", "Legacy cutover gap has no healing context; no retrospective inference.")
        return
    # Legacy permanent flag (no time recorded): superseded by the bounded window below.
    state.pop("unknown_effect", None)
    now = _time(timestamp)
    if now is None:
        active = state.get("active")
        if action == "effect_start" or (action == "received" and active):
            _mark_unknown(state, None)
            if isinstance(active, dict) and active.get("booked_event_id"):
                payload["healing_invalidates_activation_ids"] = [active["event_id"]]
        if action == "diminished":
            state["diminished"] = True
        _save_inference_state(conn, session_id, state, before)
        _unpriced(payload, "external_heal" if action == "received" else "missing_timestamp",
                  "External caster or missing timestamp; no paid local activation can be inferred.")
        return
    heal = selection.get("heal") or {}
    hot_choices = {p.get("name"): p for p in (heal, selection.get("hot") or {}) if p.get("name") and p.get("has_hot")}
    active = state.get("active")
    if active is not None and (
        not isinstance(active, dict) or not isinstance(active.get("profile"), dict)
        or isinstance(active.get("time"), bool) or not isinstance(active.get("time"), (int, float))
        or not math.isfinite(active["time"]) or not isinstance(active.get("observed"), bool)
        or type(active.get("event_id")) is not int or active["event_id"] <= 0
    ):
        _unpriced(payload, "unresolved_activation", "Persisted activation is invalid; this observation is unpriced.")
        return
    if state.pop("unknown_pending", False):
        _mark_unknown(state, now)
    if active:
        elapsed = now - active["time"]
        # An effect of unknown duration cannot outlive the longest known HoT.
        duration = _number(active["profile"].get("hot_duration")) or UNKNOWN_EFFECT_WINDOW
        if elapsed < 0:
            _unpriced(payload, "unresolved_activation", "Out-of-order observation predates the retained activation.")
            return
        if duration is not None and elapsed > duration:
            state.pop("active")
            active = None
    uncertain_until = _number(state.get("uncertain_until")) or 0.0
    if uncertain_until and now > uncertain_until:
        state.pop("uncertain_until")
        uncertain_until = 0.0
    if action == "effect_start":
        if active and active.get("booked_event_id"):
            payload["healing_invalidates_activation_ids"] = [active["event_id"]]
        profile = next(iter(hot_choices.values())) if len(hot_choices) == 1 else {}
        external_at = _number(state.get("external_at"))
        duration = _number(profile.get("hot_duration"))
        if not duration:
            _mark_unknown(state, now)
        if active:
            old_duration = _number(active["profile"].get("hot_duration"))
            if old_duration is None:
                _mark_unknown(state, active["time"])
            else:
                state["uncertain_until"] = max(_number(state.get("uncertain_until")) or 0.0,
                                               active["time"] + old_duration, now + (duration or 0))
        state["active"] = {
            "event_id": event_id, "time": now, "profile": profile,
            "observed": False, "overlap": bool(active or uncertain_until), 
            "external": (external_at is not None and 0 <= now - external_at <= 1)
                        or ("external_at" in state and external_at is None),
        }
        if state["active"]["external"]:
            _mark_unknown(state, now)  # Local selection cannot supply an external effect's duration.
        _unpriced(payload, "effect_pending", "Generic effect marker; awaiting outgoing evidence, not proof of local cast.")
    elif action == "received":
        state["external_at"] = now
        if active:
            _mark_unknown(state, now)
            active["external"] = True
            if active.get("booked_event_id"):
                payload["healing_invalidates_activation_ids"] = [active["event_id"]]
        _unpriced(payload, "external_heal", "External caster is explicit; never charge this receipt.")
    elif action == "diminished":
        state["diminished"] = True
        _unpriced(payload, "movement_notice", "Next outgoing observation has diminished/uncertain heal amount.")
    elif action in {"self", "other"}:
        amount = _number(payload.get("healed")) or 0.0
        diminished = bool(state.pop("diminished", False))
        instant = heal if not heal.get("has_hot") else {}
        direct = _direct_receipt_profiles(heal, selection.get("hot") or {})
        if amount <= 0:
            if active:
                active["observed"] = True
            _unpriced(payload, "no_positive_heal", "No positive healing receipt.")
        elif direct:
            if active:
                active["observed"] = True
            _finalize_direct_receipt(payload, state, direct, heal, selection.get("hot") or {},
                                     amount, now, event_id, action, diminished)
        elif _number(state.get("uncertain_until")):
            if active:
                active["observed"] = True
            _unpriced(payload, "unresolved_activation", "Unknown effect lifecycle or overlapping casts; no paid activation inferred.")
        elif active:
            profile = active["profile"]
            maximum = _number(profile.get("heal_max"))
            fraction = _number(profile.get("hot_fraction"))
            instant_match = _range(instant, amount)
            if active["observed"]:
                tick_max = maximum * fraction if maximum is not None and fraction is not None and active.get("booked_event_id") else maximum
                if instant_match and tick_max is not None and amount > tick_max + .050001:
                    _book(payload, instant, "instant", event_id, action == "self" and not diminished,
                          "Heal target/movement is uncertain." if action != "self" or diminished else "")
                elif active.get("booked_event_id") and not instant.get("name") and not active.get("external") and not active.get("overlap"):
                    payload.update({"healing_cost_status": "hot_tick", "healing_activation_event_id": active["event_id"]})
                else:
                    _unpriced(payload, "unresolved_activation", "Tick/instant overlap or unpriced activation; never charge a tick.")
            else:
                active["observed"] = True
                if not profile or active["overlap"] or active["external"] or diminished or action != "self":
                    _unpriced(payload, "unresolved_activation", "Ambiguous, overlapping, external or diminished activation.")
                elif profile.get("hot_trigger") != "use":
                    _unpriced(payload, "unresolved_activation", "Passive/equip effect is not a supported paid cast.")
                elif _cost(profile) is None:
                    _unpriced(payload, "unknown_cost", profile.get("verification_note") or "Decay or consumption is unknown.")
                elif fraction is None or not _number(profile.get("hot_duration")):
                    _unpriced(payload, "unresolved_activation", "Individual HoT initial-heal/duration metadata is unverified; no cast charge.")
                elif not _number(profile.get("uses_per_minute")) or profile["uses_per_minute"] > 60:
                    _unpriced(payload, "unresolved_activation", "Missing/sub-second activation rate cannot be distinguished by this log.")
                elif now - active["time"] > 1:
                    _unpriced(payload, "unresolved_activation", "Delayed first receipt may be a tick/missing initial heal; no cast charge.")
                elif instant.get("name") and (instant_match or (
                    _number(instant.get("heal_min")) is None or amount < instant["heal_min"])):
                    _unpriced(payload, "unresolved_activation", "HoT initial heal and instant/capped heal cannot be distinguished.")
                elif fraction is not None and not _range(profile, amount, 1 - fraction):
                    _unpriced(payload, "unresolved_activation", "First receipt does not match verified initial-heal range; capped/tick is uncertain.")
                else:
                    if _book(payload, profile, "hot", active["event_id"], True):
                        active["booked_event_id"] = event_id
        elif not heal.get("name"):
            _unpriced(payload, "not_selected", "No Heal tool selected; missing-marker HoT is never inferred as a cast.")
        elif "decay" not in heal:
            _unpriced(payload, "tool_not_found", "Selected Heal tool is missing from the catalog.")
        elif heal.get("has_hot") or heal.get("tracking_limit"):
            _unpriced(payload, "unresolved_activation", "Missing paid-cast marker or unsupported activation behavior.")
        elif any((_number(p.get("heal_max")) or math.inf) + .050001 >= amount for p in hot_choices.values()):
            _unpriced(payload, "unresolved_activation", "Selected HoT could explain this receipt, but its activation marker is missing.")
        elif _cost(heal) is None:
            _unpriced(payload, "unknown_cost", heal.get("verification_note") or "Decay or consumption is unknown.")
        elif not _range(heal, amount) or diminished:
            _unpriced(payload, "unresolved_activation", "Capped, diminished or out-of-range receipt does not prove a paid activation.")
        else:
            _book(payload, heal, "instant", event_id, action == "self",
                  "" if action == "self" else "Healing another avatar has no supported local loot recipient.")
    _save_inference_state(conn, session_id, state, before)
