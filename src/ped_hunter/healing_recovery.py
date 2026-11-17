"""Recover heals a run recorded but left unpriced because of a since-fixed inference defect.

Additive only. Each run's healing observations are replayed, in recorded order, through
the current engine from a fresh inference state, using the tool *names* the user had
selected at the time (each event's own snapshot). Only curated behaviour metadata of those
same tools is refreshed from the current catalog; their recorded costs are kept. An event
is rewritten only when it is currently unpriced and the replay prices it, or identifies it
as a tick of a priced cast. Priced events, markers, external heals and duplicates are never
changed, and nothing is ever removed from a total. Totals, the trend and loot-group
healing are all derived from event payloads, so they follow automatically.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import sqlite3
from typing import Iterable, Mapping

from .catalog import HealingToolRecord
from .healing import finalize_healing_event

UNPRICED = frozenset({"not_selected", "tool_not_found", "missing_timestamp", "unresolved_activation", "unknown_cost"})
BEHAVIOR_KEYS = ("has_hot", "hot_activation", "hot_fraction", "hot_duration", "hot_tick_interval",
                 "hot_trigger", "behavior_source")
DERIVED_KEYS = ("healing_cost", "healing_decay_cost", "healing_ammo_cost", "healing_cost_estimated",
                "healing_cost_status", "healing_limit", "healing_kind", "healing_activation_event_id",
                "healing_allocation_eligible", "healing_allocation_reason", "healing_inference",
                "healing_source", "healing_invalidates_activation_ids")


def _refreshed_selection(selection: dict, tools: Mapping[str, HealingToolRecord]) -> dict:
    refreshed = dict(selection)
    for slot in ("heal", "hot"):
        profile = dict(selection.get(slot) or {})
        record = tools.get(profile.get("name") or "")
        if record is not None and "decay" in profile:  # A catalog snapshot, not a missing tool.
            current = asdict(record)
            profile.update({key: current.get(key) for key in BEHAVIOR_KEYS})
        refreshed[slot] = profile
    return refreshed


def _replay_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE healing_inference_state (session_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
    return conn


def plan_session_recovery(conn: sqlite3.Connection, session_id: str,
                          tools: Mapping[str, HealingToolRecord]) -> dict:
    """Read-only: what replaying this run's heals would change, and the replayed final state."""
    replay = _replay_conn()
    updates: dict[int, dict] = {}
    report = {"session_id": session_id, "observations": 0, "recovered_uses": 0, "recovered_cost": 0.0,
              "reclassified_ticks": 0, "still_unpriced": 0, "kept_priced": 0, "priced_disagreements": 0,
              "by_tool": {}}
    rows = conn.execute(
        "SELECT id, timestamp, payload FROM events WHERE session_id = ? AND kind = 'combat' "
        "AND json_valid(payload) AND json_extract(payload, '$.healing_action') IS NOT NULL ORDER BY id",
        (session_id,)).fetchall()
    for event_id, timestamp, encoded in rows:
        stored = json.loads(encoded)
        selection = stored.get("healing_selection")
        if not isinstance(selection, dict) or selection.get("version") != 2:
            continue
        if stored.get("healing_cost_status") == "duplicate":
            continue
        report["observations"] += 1
        candidate = {key: value for key, value in stored.items() if key not in DERIVED_KEYS}
        candidate["healing_selection"] = _refreshed_selection(selection, tools)
        candidate["healing_tool"] = (selection.get("heal") or {}).get("name")
        finalize_healing_event(replay, session_id, int(event_id), timestamp, candidate)
        status, outcome = stored.get("healing_cost_status"), candidate.get("healing_cost_status")
        for target in candidate.get("healing_invalidates_activation_ids") or ():
            if target in updates and updates[target].get("healing_cost"):
                updates[target].update({"healing_allocation_eligible": False,
                                        "healing_allocation_reason": "Activation later overlapped by another effect."})
        if status == "selected_tool_estimate":
            report["kept_priced"] += 1
            if outcome != status or candidate.get("healing_tool") != stored.get("healing_tool"):
                report["priced_disagreements"] += 1
            continue
        if status not in UNPRICED:
            continue
        if outcome == "selected_tool_estimate" or outcome == "hot_tick":
            recovered = dict(stored)
            for key in DERIVED_KEYS:
                recovered.pop(key, None)
            recovered.update({key: value for key, value in candidate.items()
                              if key in DERIVED_KEYS or key == "healing_tool"})
            recovered["healing_selection"] = selection  # The record of what was selected stays as it was.
            recovered["healing_recovered"] = {
                "previous_status": status, "previous_limit": stored.get("healing_limit"),
                "reason": "Replayed after the permanent unknown-effect flag fix; selected tools and costs unchanged.",
            }
            updates[int(event_id)] = recovered
            if outcome == "hot_tick":
                report["reclassified_ticks"] += 1
            else:
                report["recovered_uses"] += 1
                report["recovered_cost"] += float(candidate["healing_cost"])
                tool = report["by_tool"].setdefault(candidate["healing_tool"], {"uses": 0, "cost": 0.0})
                tool["uses"] += 1
                tool["cost"] += float(candidate["healing_cost"])
        else:
            report["still_unpriced"] += 1
    final = replay.execute("SELECT state FROM healing_inference_state WHERE session_id = ?", (session_id,)).fetchone()
    replay.close()
    report["recovered_cost"] = round(report["recovered_cost"], 6)
    for tool in report["by_tool"].values():
        tool["cost"] = round(tool["cost"], 6)
    return {"report": report, "updates": updates, "final_state": final[0] if final else None}


def recover_sessions(db_path, session_ids: Iterable[str] | None,
                     tools: Mapping[str, HealingToolRecord], *, apply: bool) -> list[dict]:
    """Plan (and optionally apply) recovery in one write transaction; caller holds the ingestion lock."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        ids = list(session_ids) if session_ids else [
            row[0] for row in conn.execute("SELECT id FROM sessions ORDER BY started_at, id")]
        reports = []
        for session_id in ids:
            if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                raise ValueError(f"Unknown run: {session_id}")
            plan = plan_session_recovery(conn, session_id, tools)
            report = plan["report"]
            if apply and plan["updates"]:
                stamp = datetime.now().isoformat(timespec="seconds")
                for event_id, payload in plan["updates"].items():
                    payload["healing_recovered"]["at"] = stamp
                    conn.execute("UPDATE events SET payload = ? WHERE id = ?",
                                 (json.dumps(payload, ensure_ascii=False), event_id))
                # Future observations continue from the corrected lifecycle, not the stuck one.
                if plan["final_state"] is not None and conn.execute(
                        "SELECT ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] is None:
                    conn.execute(
                        "INSERT INTO healing_inference_state(session_id, state) VALUES (?, ?) "
                        "ON CONFLICT(session_id) DO UPDATE SET state = excluded.state",
                        (session_id, plan["final_state"]))
            report["applied"] = bool(apply and plan["updates"])
            if report["observations"]:
                reports.append(report)
        conn.commit() if apply else conn.rollback()
        return reports
    finally:
        conn.close()
