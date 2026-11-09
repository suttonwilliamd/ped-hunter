"""SQLite storage for PED Hunter."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import math
import sqlite3
from typing import Iterator
import uuid

from .chart_trails import Sample, bounded_samples
from .hunt_attribution import _validate_decision, _validate_state


@dataclass(slots=True)
class SessionSummary:
    session_id: str
    started_at: str
    ended_at: str | None
    activity: str
    loot_value: float
    combat_damage: float
    hunting_cost: float
    net_value: float
    events: int
    loadout_name: str | None = None
    loadout_snapshot: dict[str, object] | None = None
    repair_shots: int = 0
    repair_decay: float = 0.0
    last_input_cost: float = 0.0
    manufacturing_attempt: int = 0
    manufacturing_attempts_total: int = 0
    manufacturing_attempts_remaining: int = 0


@dataclass(frozen=True, slots=True)
class SessionResumeState:
    """Resume preconditions without aggregating the session's event history."""
    ended_at: str | None
    loadout_snapshot: dict[str, object] | None


@dataclass(slots=True)
class LoadoutRecord:
    id: int | None
    name: str
    weapon: str
    amp: str = ""
    scope: str = ""
    sight_1: str = ""
    sight_2: str = ""
    damage_enhancers: int = 0
    accuracy_enhancers: int = 0
    economy_enhancers: int = 0
    ammo_burn: int = 0
    decay: float = 0.0
    cost_per_shot: float = 0.0
    active: bool = False
    repair_shots: int = 0
    repair_decay_per_shot: float = 0.0
    repair_budget: float = 0.0
    repair_budget_known: bool = False
    repair_items: list[dict[str, object]] | None = None



@dataclass(slots=True)
class LifetimeTotals:
    session_count: int
    active_count: int
    total_loot: float
    total_cost: float
    total_net: float
    total_events: int
    overall_return_pct: float
    avg_return_pct: float
    avg_profit_per_run: float
    best_session: SessionSummary | None = None
    worst_session: SessionSummary | None = None


@dataclass(slots=True)
class SkillGainSummary:
    skill: str
    xp: float
    procs: int
    proc_pct: float


class Store:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else self.default_db_path()
        self.recovery_message: str | None = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @staticmethod
    def default_db_path() -> Path:
        root = Path.home() / "AppData" / "Local" / "ped-hunter"
        return root / "ped-hunter.sqlite3"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        try:
            self._ensure_schema_once()
            self._verify_integrity()
        except sqlite3.DatabaseError as exc:
            if not _is_malformed_database_error(exc):
                raise
            backup_path = self._quarantine_malformed_database()
            self.recovery_message = f"Recovered from malformed database; backup saved to {backup_path}"
            self._ensure_schema_once()
            self._verify_integrity()

    def _ensure_schema_once(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    activity TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    loadout_id INTEGER,
                    loadout_snapshot TEXT,
                    repair_shots INTEGER NOT NULL DEFAULT 0,
                    repair_decay REAL NOT NULL DEFAULT 0.0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp TEXT,
                    kind TEXT NOT NULL,
                    raw_message TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS chart_trails (
                    session_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    samples TEXT NOT NULL,
                    PRIMARY KEY (session_id, event_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TRIGGER IF NOT EXISTS delete_session_chart_trails
                AFTER DELETE ON sessions
                BEGIN
                    DELETE FROM chart_trails WHERE session_id = OLD.id;
                END;
                CREATE TRIGGER IF NOT EXISTS delete_event_chart_trails
                AFTER DELETE ON events
                BEGIN
                    DELETE FROM chart_trails WHERE event_id = OLD.id AND session_id = OLD.session_id;
                END;
                CREATE TABLE IF NOT EXISTS hunt_attribution_state (
                    session_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS hunt_attribution_decisions (
                    session_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    PRIMARY KEY (session_id, event_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS hunt_attribution_pool_lookup
                ON hunt_attribution_decisions (
                    session_id,
                    CASE WHEN json_valid(decision) THEN json_extract(decision, '$.cost_pool_id') END
                );
                CREATE TRIGGER IF NOT EXISTS delete_session_hunt_attribution
                AFTER DELETE ON sessions
                BEGIN
                    DELETE FROM hunt_attribution_state WHERE session_id = OLD.id;
                    DELETE FROM hunt_attribution_decisions WHERE session_id = OLD.id;
                END;
                CREATE TRIGGER IF NOT EXISTS delete_event_hunt_attribution
                AFTER DELETE ON events
                BEGIN
                    DELETE FROM hunt_attribution_decisions WHERE event_id = OLD.id AND session_id = OLD.session_id;
                    DELETE FROM hunt_attribution_state WHERE session_id = OLD.session_id;
                END;
                CREATE TABLE IF NOT EXISTS loadouts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    weapon TEXT NOT NULL,
                    amp TEXT DEFAULT '',
                    scope TEXT DEFAULT '',
                    sight_1 TEXT DEFAULT '',
                    sight_2 TEXT DEFAULT '',
                    damage_enhancers INTEGER DEFAULT 0,
                    accuracy_enhancers INTEGER DEFAULT 0,
                    economy_enhancers INTEGER DEFAULT 0,
                    ammo_burn INTEGER NOT NULL,
                    decay REAL NOT NULL,
                    cost_per_shot REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    repair_shots INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
                CREATE INDEX IF NOT EXISTS idx_loadouts_active ON loadouts(active);
                """
            )
            sessions_repair_added = False
            self._ensure_column(conn, "sessions", "loadout_id", "INTEGER")
            self._ensure_column(conn, "sessions", "loadout_snapshot", "TEXT")
            sessions_repair_added = self._ensure_column(conn, "sessions", "repair_shots", "INTEGER NOT NULL DEFAULT 0") or sessions_repair_added
            sessions_repair_added = self._ensure_column(conn, "sessions", "repair_decay", "REAL NOT NULL DEFAULT 0.0") or sessions_repair_added
            self._ensure_column(conn, "loadouts", "repair_shots", "INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        if sessions_repair_added:
            self._backfill_session_repair_summary()

    def _verify_integrity(self) -> None:
        with self.connect() as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        result = str(row[0] if row else "")
        if result.casefold() != "ok":
            raise sqlite3.DatabaseError(f"database integrity check failed: {result}")

    def _quarantine_malformed_database(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = _unique_backup_path(self.db_path, timestamp)
        if self.db_path.exists():
            self.db_path.replace(backup_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.db_path}{suffix}")
            if sidecar.exists():
                sidecar.replace(_unique_backup_path(sidecar, timestamp))
        return backup_path

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return True
        return False

    def load_chart_trails(self, session_id: str, event_ids: Sequence[int]) -> dict[int, tuple[Sample, ...]]:
        """Load only requested, still-owned event histories; ignore corrupt rows."""
        ids = list(dict.fromkeys(
            event_id for event_id in event_ids
            if isinstance(event_id, int) and not isinstance(event_id, bool) and 0 < event_id <= 2**63 - 1
        ))
        trails: dict[int, tuple[Sample, ...]] = {}
        if not ids:
            return trails
        with self.connect() as conn:
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT t.event_id, t.samples FROM chart_trails t
                    JOIN events e ON e.id = t.event_id AND e.session_id = t.session_id
                    JOIN sessions s ON s.id = t.session_id
                    WHERE t.session_id = ? AND t.event_id IN ({placeholders})
                    """,
                    (session_id, *chunk),
                ).fetchall()
                for row in rows:
                    try:
                        values = json.loads(row["samples"])
                        if not isinstance(values, list):
                            continue
                        history = bounded_samples(values)
                    except (ValueError, TypeError, OverflowError):
                        continue
                    if history:
                        trails[int(row["event_id"])] = history
        return trails

    def save_chart_trails(self, session_id: str, trails: Mapping[int, Sequence[Sample]]) -> None:
        """Upsert bounded observations without changing sessions or events.

        Stale/foreign IDs are ignored. Invalid endpoint values for a retained
        event raise ValueError and leave the entire write uncommitted.
        """
        ids = [
            event_id for event_id in trails
            if isinstance(event_id, int) and not isinstance(event_id, bool) and 0 < event_id <= 2**63 - 1
        ]
        if not ids:
            return
        with self.connect() as conn:
            # Ownership must remain valid until the upsert, even if another
            # connection deletes a session while the HUD is refreshing.
            conn.execute("BEGIN IMMEDIATE")
            owned: set[int] = set()
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                owned.update(int(row["id"]) for row in conn.execute(
                    f"""
                    SELECT e.id FROM events e JOIN sessions s ON s.id = e.session_id
                    WHERE e.session_id = ? AND e.id IN ({placeholders})
                    """,
                    (session_id, *chunk),
                ))
            rows = []
            for event_id in ids:
                if event_id not in owned:
                    continue
                history = bounded_samples(trails[event_id])
                if history:
                    rows.append((session_id, event_id, json.dumps(history, separators=(",", ":"), allow_nan=False)))
            if rows:
                conn.executemany(
                    """
                    INSERT INTO chart_trails (session_id, event_id, samples) VALUES (?, ?, ?)
                    ON CONFLICT(session_id, event_id) DO UPDATE SET samples = excluded.samples
                    WHERE chart_trails.samples != excluded.samples
                    """,
                    rows,
                )
                conn.commit()

    def load_hunt_attribution(self, session_id: str) -> tuple[dict | None, dict[int, dict]]:
        """Load a coherent projection, ignoring corrupt or orphaned accounting.

        A partially decoded pool is unsafe: its pending amount assumes all its
        decisions exist. Reject the whole projection rather than lose input.
        """
        with self.connect() as conn:
            row = conn.execute(
                """SELECT h.state FROM hunt_attribution_state h
                   JOIN sessions s ON s.id = h.session_id WHERE h.session_id = ?""",
                (session_id,),
            ).fetchone()
            if row is None:
                return None, {}
            try:
                state = json.loads(row['state'])
                ids = _validate_state(state)
                decisions = {}
                for record in conn.execute(
                    "SELECT event_id, decision FROM hunt_attribution_decisions WHERE session_id = ?",
                    (session_id,),
                ):
                    event_id = int(record['event_id'])
                    # A rejected/corrupt earlier state may have been replaced by
                    # a new activation. Never backfill decisions across it.
                    if event_id <= state['activation_event_id']:
                        continue
                    decision = json.loads(record['decision'])
                    ids.add(event_id)
                    ids.update(_validate_decision(decision))
                    decisions[event_id] = decision
                self._validate_hunt_ownership(conn, session_id, ids)
                self._validate_hunt_accounting(state, decisions)
            except (KeyError, TypeError, ValueError, OverflowError):
                return None, {}
        return state, decisions

    @staticmethod
    def _validate_hunt_ownership(conn: sqlite3.Connection, session_id: str, ids: set[int]) -> None:
        if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            raise ValueError("Attribution session does not exist")
        owned = set()
        values = list(ids)
        for start in range(0, len(values), 900):
            chunk = values[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            owned.update(int(row['id']) for row in conn.execute(
                f"SELECT id FROM events WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *chunk),
            ))
        if owned != ids:
            raise ValueError("Attribution references a missing or foreign event")

    @staticmethod
    def _validate_hunt_accounting(state: dict, decisions: Mapping[int, dict]) -> None:
        pools: dict[int, list[dict]] = {}
        for decision in decisions.values():
            pools.setdefault(decision['cost_pool_id'], []).append(decision)
        frozen_pending = []
        for pool_id, members in pools.items():
            if pool_id not in decisions or decisions[pool_id]['cost_pool_id'] != pool_id:
                raise ValueError("Attribution pool has no anchor decision")
            total, pending = members[0]['cost_pool_total'], members[0]['cost_unassigned']
            frozen = members[0]['cost_status'].startswith('frozen_')
            if any(member['cost_pool_total'] != total or member['cost_unassigned'] != pending
                   or member['cost_status'].startswith('frozen_') != frozen for member in members):
                raise ValueError("Inconsistent attribution pool")
            if not math.isclose(math.fsum(member['raw_cost'] for member in members), total, abs_tol=1e-9):
                raise ValueError("Attribution pool omits original input")
            if not math.isclose(math.fsum(member['cost'] or 0.0 for member in members) + pending, total, abs_tol=1e-9):
                raise ValueError("Attribution pool does not conserve input")
            if frozen:
                frozen_pending.append(pending)
            elif total:
                active = state['active']
                if active is None or active['id'] != pool_id or active['pending'] != pending:
                    raise ValueError("Attribution pool has no active state")
                if {member['event_id'] for member in active['members']} != {
                    event_id for event_id, decision in decisions.items() if decision['cost_pool_id'] == pool_id
                }:
                    raise ValueError("Attribution pool membership mismatch")
                if any(decisions[member['event_id']]['raw_cost'] != member['raw_cost']
                       for member in active['members']):
                    raise ValueError("Attribution state disagrees with original input")
        active = state['active']
        if active is not None and active['id'] not in pools:
            raise ValueError("Attribution state has no decisions")
        if not math.isclose(math.fsum(frozen_pending), state['frozen_pending'], abs_tol=1e-9):
            raise ValueError("Attribution state loses frozen input")

    def save_hunt_attribution(self, session_id: str, state: dict, decisions: Mapping[int, dict]) -> None:
        """Atomically save bounded state and changed, session-owned overrides.

        Invalid writes fail closed, including foreign IDs and changes to frozen
        decisions. Original events, session totals and loadouts are never written.
        """
        try:
            ids = _validate_state(state)
            for event_id, decision in decisions.items():
                if isinstance(event_id, bool) or not isinstance(event_id, int) or not 0 < event_id <= 2**63 - 1:
                    raise ValueError("Invalid attribution event identifier")
                if event_id <= state['activation_event_id']:
                    raise ValueError("Attribution cannot precede activation")
                ids.add(event_id)
                ids.update(_validate_decision(decision))
            encoded_state = json.dumps(state, separators=(",", ":"), allow_nan=False)
            encoded_decisions = {
                event_id: json.dumps(decision, separators=(",", ":"), allow_nan=False)
                for event_id, decision in decisions.items()
            }
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid attribution record") from exc
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_row = conn.execute(
                "SELECT state FROM hunt_attribution_state WHERE session_id = ?", (session_id,),
            ).fetchone()
            previous_state = None
            reset = (not decisions and state['active'] is None and state['frozen_pending'] == 0
                     and state['activation_event_id'] == state['watermark'])
            if previous_row is not None:
                try:
                    previous_state = json.loads(previous_row['state'])
                    _validate_state(previous_state)
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    if not reset:
                        raise ValueError("Corrupt existing attribution state") from exc
                    previous_state = None
                if previous_state and previous_state['activation_event_id'] != state['activation_event_id']:
                    if not reset or state['activation_event_id'] < previous_state['watermark']:
                        raise ValueError("Attribution activation cannot move backwards")
                    previous_state = None

            pool_ids = {decision['cost_pool_id'] for decision in decisions.values()}
            for snapshot in (previous_state, state):
                if snapshot and snapshot['active']:
                    pool_ids.add(snapshot['active']['id'])
            changed_ids = list(decisions)
            for start in range(0, len(changed_ids), 900):
                chunk = changed_ids[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                for row in conn.execute(
                    f"SELECT decision FROM hunt_attribution_decisions WHERE session_id = ? AND event_id IN ({placeholders})",
                    (session_id, *chunk),
                ):
                    try:
                        old = json.loads(row['decision'])
                        _validate_decision(old)
                    except (KeyError, TypeError, ValueError, OverflowError) as exc:
                        raise ValueError("Corrupt existing attribution decision") from exc
                    pool_ids.add(old['cost_pool_id'])

            # Read only affected pools through the expression index. Frozen
            # history was validated at load and cannot be changed by later saves.
            previous = {}
            pool_expression = "CASE WHEN json_valid(decision) THEN json_extract(decision, '$.cost_pool_id') END"
            pool_values = list(pool_ids)
            for start in range(0, len(pool_values), 900):
                chunk = pool_values[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                for row in conn.execute(
                    f"SELECT event_id, decision FROM hunt_attribution_decisions "
                    f"WHERE session_id = ? AND {pool_expression} IN ({placeholders})",
                    (session_id, *chunk),
                ):
                    event_id = int(row['event_id'])
                    if event_id <= state['activation_event_id']:
                        continue
                    try:
                        old = json.loads(row['decision'])
                        ids.add(event_id)
                        ids.update(_validate_decision(old))
                    except (KeyError, TypeError, ValueError, OverflowError) as exc:
                        raise ValueError("Corrupt existing attribution decision") from exc
                    if event_id in decisions and old != decisions[event_id]:
                        if old['cost_status'].startswith('frozen_'):
                            raise ValueError("Frozen attribution decisions are immutable")
                        if decisions[event_id]['attribution_revision'] <= old['attribution_revision']:
                            raise ValueError("Attribution revisions must increase")
                    previous[event_id] = old
            self._validate_hunt_ownership(conn, session_id, ids)
            combined = previous | decisions
            old_frozen = math.fsum({
                d['cost_pool_id']: d['cost_unassigned'] for d in previous.values()
                if d['cost_status'].startswith('frozen_')
            }.values())
            new_frozen = math.fsum({
                d['cost_pool_id']: d['cost_unassigned'] for d in combined.values()
                if d['cost_status'].startswith('frozen_')
            }.values())
            subset = state | {'frozen_pending': new_frozen}
            self._validate_hunt_accounting(subset, combined)
            expected = math.fsum(((previous_state or {}).get('frozen_pending', 0.0), -old_frozen, new_frozen))
            if not math.isclose(expected, state['frozen_pending'], rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError("Attribution state loses frozen input")
            conn.execute(
                """INSERT INTO hunt_attribution_state (session_id, state) VALUES (?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET state = excluded.state
                   WHERE hunt_attribution_state.state != excluded.state""",
                (session_id, encoded_state),
            )
            conn.executemany(
                """INSERT INTO hunt_attribution_decisions (session_id, event_id, decision) VALUES (?, ?, ?)
                   ON CONFLICT(session_id, event_id) DO UPDATE SET decision = excluded.decision
                   WHERE hunt_attribution_decisions.decision != excluded.decision""",
                [(session_id, event_id, encoded) for event_id, encoded in encoded_decisions.items()],
            )
            conn.commit()

    def start_session(self, activity: str = "hunt", loadout: LoadoutRecord | None = None) -> str:
        session_id = f"ph-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now().isoformat(timespec="seconds")
        loadout_snapshot = json.dumps(loadout_to_dict(loadout), ensure_ascii=False) if loadout else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, started_at, activity, loadout_id, loadout_snapshot, repair_shots, repair_decay)
                VALUES (?, ?, ?, ?, ?, 0, 0.0)
                """,
                (session_id, started_at, activity, loadout.id if loadout else None, loadout_snapshot),
            )
            conn.commit()
        return session_id

    def end_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (datetime.now().isoformat(timespec="seconds"), session_id),
            )
            conn.commit()

    def end_other_active_sessions(self, session_id: str | None = None) -> int:
        """Close stale concurrent sessions before starting/resuming one run."""
        ended_at = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            if session_id is None:
                cursor = conn.execute(
                    "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
                    (ended_at,),
                )
            else:
                cursor = conn.execute(
                    "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL AND id != ?",
                    (ended_at, session_id),
                )
            conn.commit()
            return int(cursor.rowcount)

    def resume_session(self, session_id: str) -> None:
        """Reopen a saved session so new events can be appended to it."""
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))
            conn.commit()

    def update_session_loadout(self, session_id: str, loadout: LoadoutRecord) -> None:
        """Replace a session's stored loadout snapshot with the latest active setup."""
        snapshot = json.dumps(loadout_to_dict(loadout), ensure_ascii=False) if loadout else None
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET loadout_id = ?, loadout_snapshot = ? WHERE id = ?",
                (loadout.id if loadout else None, snapshot, session_id),
            )
            conn.commit()

    def estimate_repair_cost_since_last_repair(self, session_id: str, loadout_name: str | None = None, fallback_decay: float = 0.0) -> float:

        """Estimate repair-terminal decay accrued since the last repair reset.

        New combat rows store repair_decay separately from ammo spend. Legacy rows
        may only have shot_cost, so fall back to counting shot-consuming rows and
        multiplying by the active loadout's decay per shot.
        """
        last_repair_id = 0
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS last_id FROM events WHERE session_id = ? AND kind = 'repair'",
                (session_id,),
            ).fetchone()
            if row:
                last_repair_id = int(row["last_id"] or 0)
            rows = conn.execute(
                """
                SELECT payload FROM events
                WHERE session_id = ? AND id > ? AND kind = 'combat'
                ORDER BY id
                """,
                (session_id, last_repair_id),
            ).fetchall()

        total = 0.0
        fallback_shots = 0
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except json.JSONDecodeError:
                continue
            if loadout_name and payload.get("loadout") not in (None, loadout_name):
                continue
            if "repair_decay" in payload:
                total += float(payload.get("repair_decay") or 0.0)
            elif payload.get("shot_cost") is not None:
                fallback_shots += 1
        if fallback_shots and fallback_decay > 0:
            total += fallback_shots * fallback_decay
        return total

    def add_event(self, session_id: str, event: dict) -> None:
        payload = json.dumps(event.get("payload", {}), ensure_ascii=False)
        timestamp = event.get("timestamp")
        if timestamp is not None and not isinstance(timestamp, str):
            timestamp = str(timestamp)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)",
                (session_id, timestamp, event["kind"], event["raw_message"], payload),
            )
            self._recompute_session_repair_state(conn, session_id)
            conn.commit()

    def allocate_manufacturing_attempt(self, session_id: str, craft_item: str | None = None) -> dict[str, object] | None:
        """Reserve one FIFO manufacturing attempt and persist its attribution."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            offsets = conn.execute("SELECT payload FROM events WHERE session_id = ? AND kind = 'manufacturing_offset' ORDER BY id", (session_id,)).fetchall()
            for row in offsets:
                try:
                    offset = json.loads(row["payload"] or "{}")
                    offset_id = str(offset["offset_id"])
                    total = int(offset.get("attempts_total", 0) or 0)
                    cost = float(offset.get("cost_per_attempt", 0.0) or 0.0)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                used = conn.execute("SELECT COUNT(*) AS used FROM events WHERE session_id = ? AND kind = 'manufacturing_allocation' AND json_extract(payload, '$.offset_id') = ?", (session_id, offset_id)).fetchone()["used"]
                if int(used or 0) >= total:
                    continue
                allocation = {"allocation_id": uuid.uuid4().hex, "offset_id": offset_id, "craft_item": str(craft_item or "").strip(), "attempt_number": int(used or 0) + 1, "attempts_total": total, "attempts_remaining": total - int(used or 0) - 1, "input_cost": cost, "status": "craft_recorded"}
                conn.execute("INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)", (session_id, None, "manufacturing_allocation", "Manufacturing attempt allocated", json.dumps(allocation)))
                conn.commit()
                return allocation
            conn.commit()
        return None

    def add_manufacturing_output(self, session_id: str, event: dict, output_item: str) -> None:
        """Insert manufacturing output and pair it with its allocation atomically."""
        wanted = _normalize_manufacturing_item(output_item)
        payload = dict(event.get("payload", {}))
        timestamp = event.get("timestamp")
        if timestamp is not None and not isinstance(timestamp, str):
            timestamp = str(timestamp)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if wanted:
                rows = conn.execute("SELECT id, payload FROM events WHERE session_id = ? AND kind = 'manufacturing_allocation' ORDER BY id", (session_id,)).fetchall()
                for row in rows:
                    try:
                        allocation = json.loads(row["payload"] or "{}")
                    except json.JSONDecodeError:
                        continue
                    craft_item = _normalize_manufacturing_item(str(allocation.get("craft_item") or ""))
                    if allocation.get("status") != "craft_recorded" or not craft_item or craft_item != wanted:
                        continue
                    allocation["status"] = "paired"
                    allocation["output_item"] = output_item
                    conn.execute("UPDATE events SET payload = ? WHERE id = ?", (json.dumps(allocation), row["id"]))
                    payload.update(allocation)
                    break
            conn.execute("INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)", (session_id, timestamp, event["kind"], event["raw_message"], json.dumps(payload, ensure_ascii=False)))
            conn.commit()

    def _recompute_session_repair_state(self, conn: sqlite3.Connection, session_id: str) -> None:
        session_row = conn.execute("SELECT loadout_id, loadout_snapshot FROM sessions WHERE id = ?", (session_id,)).fetchone()
        loadout_id = int(session_row["loadout_id"]) if session_row and session_row["loadout_id"] is not None else None
        rows = conn.execute(
            """
            SELECT kind, payload
            FROM events
            WHERE session_id = ?
            ORDER BY COALESCE(timestamp, ''), id
            """,
            (session_id,),
        ).fetchall()

        repair_shots = 0
        repair_decay = 0.0
        had_repair = False
        for row in rows:
            try:
                event_payload = json.loads(str(row["payload"] or "{}"))
            except json.JSONDecodeError:
                event_payload = {}
            kind = row["kind"]
            if kind == "combat":
                if event_payload.get("shot_cost") is not None:
                    repair_shots += 1
                repair_decay += float(event_payload.get("repair_decay") or 0.0)
            elif kind == "repair":
                repair_shots = 0
                repair_decay = 0.0
                had_repair = True

        conn.execute(
            "UPDATE sessions SET repair_shots = ?, repair_decay = ? WHERE id = ?",
            (repair_shots, repair_decay, session_id),
        )
        if loadout_id is not None:
            if had_repair:
                loadout_repair_shots = repair_shots
            else:
                starting_shots = 0
                if session_row and session_row["loadout_snapshot"]:
                    try:
                        snapshot = json.loads(str(session_row["loadout_snapshot"]))
                    except json.JSONDecodeError:
                        snapshot = None
                    if isinstance(snapshot, dict):
                        starting_shots = int(snapshot.get("repair_shots", 0) or 0)
                loadout_repair_shots = starting_shots + repair_shots
            conn.execute("UPDATE loadouts SET repair_shots = ? WHERE id = ?", (loadout_repair_shots, loadout_id))
        if had_repair and session_row and session_row["loadout_snapshot"]:
            try:
                snapshot = json.loads(str(session_row["loadout_snapshot"]))
            except json.JSONDecodeError:
                snapshot = None
            if isinstance(snapshot, dict):
                snapshot["repair_shots"] = 0
                conn.execute(
                    "UPDATE sessions SET loadout_snapshot = ? WHERE id = ?",
                    (json.dumps(snapshot, ensure_ascii=False), session_id),
                )

    def save_loadout(self, loadout: LoadoutRecord, *, make_active: bool = False) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            if make_active:
                conn.execute("UPDATE loadouts SET active = 0")
            active = 1 if make_active or loadout.active else 0
            if loadout.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO loadouts (
                        name, weapon, amp, scope, sight_1, sight_2,
                        damage_enhancers, accuracy_enhancers, economy_enhancers,
                        ammo_burn, decay, cost_per_shot, active, repair_shots, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        weapon=excluded.weapon,
                        amp=excluded.amp,
                        scope=excluded.scope,
                        sight_1=excluded.sight_1,
                        sight_2=excluded.sight_2,
                        damage_enhancers=excluded.damage_enhancers,
                        accuracy_enhancers=excluded.accuracy_enhancers,
                        economy_enhancers=excluded.economy_enhancers,
                        ammo_burn=excluded.ammo_burn,
                        decay=excluded.decay,
                        cost_per_shot=excluded.cost_per_shot,
                        active=excluded.active,
                        repair_shots=loadouts.repair_shots,
                        updated_at=excluded.updated_at
                    """,
                    _loadout_values(loadout, active, now, now),
                )
                row = conn.execute("SELECT id FROM loadouts WHERE name = ?", (loadout.name,)).fetchone()
                loadout_id = int(row["id"] if row else cur.lastrowid)
            else:
                existing = conn.execute("SELECT repair_shots FROM loadouts WHERE id = ?", (loadout.id,)).fetchone()
                if existing is not None:
                    loadout.repair_shots = int(existing["repair_shots"] or 0)
                conn.execute(
                    """
                    UPDATE loadouts SET
                        name=?, weapon=?, amp=?, scope=?, sight_1=?, sight_2=?,
                        damage_enhancers=?, accuracy_enhancers=?, economy_enhancers=?,
                        ammo_burn=?, decay=?, cost_per_shot=?, active=?, repair_shots=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        loadout.name,
                        loadout.weapon,
                        loadout.amp,
                        loadout.scope,
                        loadout.sight_1,
                        loadout.sight_2,
                        loadout.damage_enhancers,
                        loadout.accuracy_enhancers,
                        loadout.economy_enhancers,
                        loadout.ammo_burn,
                        loadout.decay,
                        loadout.cost_per_shot,
                        active,
                        loadout.repair_shots,
                        now,
                        loadout.id,
                    ),
                )
                loadout_id = loadout.id
            conn.commit()

        return loadout_id

    def list_loadouts(self) -> list[LoadoutRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM loadouts ORDER BY active DESC, updated_at DESC, name").fetchall()
        return [_loadout_from_row(row) for row in rows]

    def get_loadout(self, loadout_id: int) -> LoadoutRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM loadouts WHERE id = ?", (loadout_id,)).fetchone()
        return _loadout_from_row(row) if row else None

    def get_active_loadout(self) -> LoadoutRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM loadouts WHERE active = 1 ORDER BY updated_at DESC LIMIT 1").fetchone()
        return _loadout_from_row(row) if row else None

    def set_active_loadout(self, loadout_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE loadouts SET active = 0")
            conn.execute("UPDATE loadouts SET active = 1, updated_at = ? WHERE id = ?", (datetime.now().isoformat(timespec="seconds"), loadout_id))
            conn.commit()

    def delete_loadout(self, loadout_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM loadouts WHERE id = ?", (loadout_id,))
            conn.commit()

    def get_current_session(self) -> SessionSummary | None:
        with self.connect() as conn:
            row = conn.execute(_SESSION_SUMMARY_SQL + """
                WHERE s.ended_at IS NULL
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return _session_from_row(row)

    def get_last_contributed_session(self) -> SessionSummary | None:
        with self.connect() as conn:
            row = conn.execute(
                _SESSION_SUMMARY_SQL + """
                GROUP BY s.id
                ORDER BY COALESCE(MAX(e.timestamp), s.started_at) DESC, s.started_at DESC
                LIMIT 1
                """
            ).fetchone()
        return _session_from_row(row) if row else None

    def get_session_resume_state(self, session_id: str) -> SessionResumeState | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT ended_at, loadout_snapshot FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
        return SessionResumeState(row["ended_at"], _loadout_snapshot(row["loadout_snapshot"])) if row else None

    def get_session(self, session_id: str) -> SessionSummary | None:
        with self.connect() as conn:
            row = conn.execute(
                _SESSION_SUMMARY_SQL + """
                WHERE s.id = ?
                GROUP BY s.id
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row else None

    def list_recent_sessions(self, limit: int = 5) -> list[SessionSummary]:
        with self.connect() as conn:
            rows = conn.execute(
                _SESSION_SUMMARY_SQL + """
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def list_all_sessions(self) -> list[SessionSummary]:
        """Return every stored session newest-first for aggregate reporting."""
        with self.connect() as conn:
            rows = conn.execute(
                _SESSION_SUMMARY_SQL + """
                GROUP BY s.id
                ORDER BY s.started_at DESC
                """
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def _backfill_session_repair_summary(self) -> None:
        with self.connect() as conn:
            session_ids = [str(row[0]) for row in conn.execute("SELECT id FROM sessions ORDER BY started_at ASC, id ASC").fetchall()]
        if not session_ids:
            return
        with self.connect() as conn:
            for session_id in session_ids:
                rows = conn.execute(
                    """
                    SELECT kind, payload
                    FROM events
                    WHERE session_id = ?
                    ORDER BY COALESCE(timestamp, ''), id
                    """,
                    (session_id,),
                ).fetchall()
                shots_since_repair = 0
                decay_since_repair = 0.0
                for row in rows:
                    try:
                        payload = json.loads(str(row["payload"] or "{}"))
                    except json.JSONDecodeError:
                        payload = {}
                    if row["kind"] == "combat":
                        if payload.get("shot_cost") is not None:
                            shots_since_repair += 1
                        decay_since_repair += float(payload.get("repair_decay") or 0.0)
                    elif row["kind"] == "repair":
                        shots_since_repair = 0
                        decay_since_repair = 0.0
                conn.execute(
                    "UPDATE sessions SET repair_shots = ?, repair_decay = ? WHERE id = ?",
                    (shots_since_repair, decay_since_repair, session_id),
                )
            conn.commit()

    def lifetime_totals(self, sessions: list[SessionSummary] | None = None) -> LifetimeTotals:
        """Return all-session totals, optionally reusing an already read snapshot."""
        if sessions is None:
            sessions = self.list_all_sessions()
        total_loot = sum(session.loot_value for session in sessions)
        total_cost = sum(session.hunting_cost for session in sessions)
        total_net = total_loot - total_cost
        sessions_with_cost = [session for session in sessions if session.hunting_cost > 0]
        return_pcts = [session.loot_value / session.hunting_cost * 100.0 for session in sessions_with_cost]
        return LifetimeTotals(
            session_count=len(sessions),
            active_count=sum(1 for session in sessions if session.ended_at is None),
            total_loot=total_loot,
            total_cost=total_cost,
            total_net=total_net,
            total_events=sum(session.events for session in sessions),
            overall_return_pct=(total_loot / total_cost * 100.0) if total_cost > 0 else 0.0,
            avg_return_pct=(sum(return_pcts) / len(return_pcts)) if return_pcts else 0.0,
            avg_profit_per_run=(total_net / len(sessions)) if sessions else 0.0,
            best_session=max(sessions, key=lambda session: session.net_value) if sessions else None,
            worst_session=min(sessions, key=lambda session: session.net_value) if sessions else None,
        )

    def skill_gains_for_session(self, session_id: str) -> list[SkillGainSummary]:
        """Return LootNanny-style skill gain totals for a session.

        Skill events are stored as JSON payloads with ``skill`` and ``xp`` keys.
        Malformed legacy payloads are skipped so one bad local row does not break
        the session view.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM events
                WHERE session_id = ? AND kind = 'skill'
                """,
                (session_id,),
            ).fetchall()

        totals: dict[str, float] = {}
        procs: dict[str, int] = {}
        for row in rows:
            try:
                payload = json.loads(str(row["payload"] or "{}"))
                skill = str(payload.get("skill") or "").strip()
                xp = float(payload.get("xp", 0) or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not skill:
                continue
            totals[skill] = totals.get(skill, 0.0) + xp
            procs[skill] = procs.get(skill, 0) + 1

        total_procs = sum(procs.values())
        return [
            SkillGainSummary(
                skill=skill,
                xp=xp,
                procs=procs[skill],
                proc_pct=(procs[skill] / total_procs * 100.0) if total_procs else 0.0,
            )
            for skill, xp in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        ]


_SESSION_SUMMARY_SQL = """
    SELECT s.id, s.started_at, s.ended_at, s.activity, s.loadout_snapshot,
           s.repair_shots, s.repair_decay,
           COUNT(e.id) AS events,
           COALESCE(SUM(CASE
               WHEN e.kind = 'loot'
                    AND json_valid(e.payload)
                    AND NOT (
                        lower(trim(COALESCE(json_extract(e.payload, '$.item_name'), ''))) IN ('oil', 'universal ammo')
                        OR lower(trim(COALESCE(json_extract(e.payload, '$.item_name'), ''))) LIKE '% ingot'
                    )
               THEN json_extract(e.payload, '$.value')
               ELSE 0
           END), 0) AS loot_value,
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload) THEN json_extract(e.payload, '$.damage') ELSE 0 END), 0) AS combat_damage,
           COALESCE(SUM(CASE
                WHEN e.kind = 'combat' AND json_valid(e.payload) THEN
                    COALESCE(json_extract(e.payload, '$.shot_cost'), json_extract(e.payload, '$.ammo_cost'), 0)
                WHEN e.kind = 'repair' AND json_valid(e.payload) THEN
                    COALESCE(json_extract(e.payload, '$.estimated_cost'), json_extract(e.payload, '$.repair_cost'), 0)
                WHEN e.kind IN ('craft', 'manufacturing_offset') AND json_valid(e.payload) THEN json_extract(e.payload, '$.total_cost')
                ELSE 0
            END), 0) AS hunting_cost,
           COALESCE((SELECT json_extract(e2.payload, '$.input_cost') FROM events e2 WHERE e2.session_id = s.id AND e2.kind IN ('craft', 'loot') AND json_valid(e2.payload) AND json_type(e2.payload, '$.input_cost') IS NOT NULL ORDER BY e2.id DESC LIMIT 1), 0) AS last_input_cost,
           COALESCE((SELECT json_extract(e2.payload, '$.attempt_number') FROM events e2 WHERE e2.session_id = s.id AND e2.kind IN ('craft', 'loot') AND json_valid(e2.payload) AND json_type(e2.payload, '$.attempt_number') IS NOT NULL ORDER BY e2.id DESC LIMIT 1), 0) AS manufacturing_attempt,
           COALESCE((SELECT json_extract(e2.payload, '$.attempts_total') FROM events e2 WHERE e2.session_id = s.id AND e2.kind IN ('craft', 'loot') AND json_valid(e2.payload) AND json_type(e2.payload, '$.attempts_total') IS NOT NULL ORDER BY e2.id DESC LIMIT 1), 0) AS manufacturing_attempts_total,
           COALESCE((SELECT json_extract(e2.payload, '$.attempts_remaining') FROM events e2 WHERE e2.session_id = s.id AND e2.kind IN ('craft', 'loot') AND json_valid(e2.payload) AND json_type(e2.payload, '$.attempts_remaining') IS NOT NULL ORDER BY e2.id DESC LIMIT 1), 0) AS manufacturing_attempts_remaining
    FROM sessions s
    LEFT JOIN events e ON e.session_id = s.id
"""



def _single_line_text(value: object | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.splitlines()[0].strip()


def _loadout_snapshot(encoded: str | None) -> dict[str, object] | None:
    if encoded:
        try:
            snapshot = json.loads(encoded)
            if isinstance(snapshot, dict):
                return snapshot
        except json.JSONDecodeError:
            pass
    return None


def _session_from_row(row: sqlite3.Row) -> SessionSummary:
    loot_value = float(row["loot_value"] or 0)
    hunting_cost = float(row["hunting_cost"] or 0)
    loadout_snapshot = _loadout_snapshot(row["loadout_snapshot"])
    loadout_name = _single_line_text(loadout_snapshot.get("name")) or None if loadout_snapshot else None
    return SessionSummary(
        session_id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        activity=row["activity"],
        loot_value=loot_value,
        combat_damage=float(row["combat_damage"] or 0),
        hunting_cost=hunting_cost,
        net_value=loot_value - hunting_cost,
        events=int(row["events"] or 0),
        loadout_name=loadout_name,
        loadout_snapshot=loadout_snapshot,
        repair_shots=int(row["repair_shots"] or 0),
        repair_decay=float(row["repair_decay"] or 0),
        last_input_cost=float(row["last_input_cost"] or 0),
        manufacturing_attempt=int(row["manufacturing_attempt"] or 0),
        manufacturing_attempts_total=int(row["manufacturing_attempts_total"] or 0),
        manufacturing_attempts_remaining=int(row["manufacturing_attempts_remaining"] or 0),
    )



def _loadout_values(loadout: LoadoutRecord, active: int, created_at: str, updated_at: str) -> tuple:
    return (
        loadout.name,
        loadout.weapon,
        loadout.amp,
        loadout.scope,
        loadout.sight_1,
        loadout.sight_2,
        loadout.damage_enhancers,
        loadout.accuracy_enhancers,
        loadout.economy_enhancers,
        loadout.ammo_burn,
        loadout.decay,
        loadout.cost_per_shot,
        active,
        loadout.repair_shots,
        created_at,
        updated_at,
    )


def _loadout_from_row(row: sqlite3.Row) -> LoadoutRecord:
    return LoadoutRecord(
        id=int(row["id"]),
        name=_single_line_text(row["name"]),
        weapon=_single_line_text(row["weapon"]),
        amp=_single_line_text(row["amp"]),
        scope=_single_line_text(row["scope"]),
        sight_1=_single_line_text(row["sight_1"]),
        sight_2=_single_line_text(row["sight_2"]),
        damage_enhancers=int(row["damage_enhancers"] or 0),
        accuracy_enhancers=int(row["accuracy_enhancers"] or 0),
        economy_enhancers=int(row["economy_enhancers"] or 0),
        ammo_burn=int(row["ammo_burn"] or 0),
        decay=float(row["decay"] or 0),
        cost_per_shot=float(row["cost_per_shot"] or 0),
        active=bool(row["active"]),
        repair_shots=int(row["repair_shots"] or 0),
    )



def loadout_to_dict(loadout: LoadoutRecord | None) -> dict[str, object] | None:
    if loadout is None:
        return None
    return {
        "id": loadout.id,
        "name": _single_line_text(loadout.name),
        "weapon": _single_line_text(loadout.weapon),
        "amp": _single_line_text(loadout.amp),
        "scope": _single_line_text(loadout.scope),
        "sight_1": _single_line_text(loadout.sight_1),
        "sight_2": _single_line_text(loadout.sight_2),
        "damage_enhancers": loadout.damage_enhancers,
        "accuracy_enhancers": loadout.accuracy_enhancers,
        "economy_enhancers": loadout.economy_enhancers,
        "ammo_burn": loadout.ammo_burn,
        "decay": loadout.decay,
        "cost_per_shot": loadout.cost_per_shot,
        "repair_shots": loadout.repair_shots,
        "repair_decay_per_shot": loadout.repair_decay_per_shot,
        "repair_budget": loadout.repair_budget,
        "repair_budget_known": loadout.repair_budget_known,
        "repair_items": loadout.repair_items or [],
    }



def _normalize_manufacturing_item(value: str) -> str:
    normalized = " ".join(value.casefold().strip().split())
    for suffix in (" blueprint", " bp"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip()
    return normalized


def _is_malformed_database_error(exc: sqlite3.DatabaseError) -> bool:
    message = str(exc).casefold()
    return "malformed" in message or "integrity check failed" in message or "not a database" in message


def _unique_backup_path(path: Path, timestamp: str) -> Path:
    candidate = path.with_name(f"{path.name}.corrupt-{timestamp}.bak")
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}.corrupt-{timestamp}-{index}.bak")
        if not candidate.exists():
            return candidate
        index += 1
