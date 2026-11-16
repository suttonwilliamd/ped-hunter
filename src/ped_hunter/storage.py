"""SQLite storage for PED Hunter."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import json
import math
import sqlite3
import threading
from typing import Iterator
import uuid

from .chart_trails import Sample, bounded_samples
from .hunt_attribution import _validate_decision, _validate_state
from .healing import finalize_healing_event


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
    healing_cost: float = 0.0
    estimated_healing_uses: int = 0
    unpriced_heal_events: int = 0
    accounting_revision: int = 0


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
        # session id -> mutation token at this Store's last full durability replay
        self._repair_replayed: dict[str, int] = {}
        self._held = threading.local()
        # session id -> (mutation token, last skill event id, xp by skill, gains by skill)
        self._skill_totals: dict[str, tuple] = {}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @staticmethod
    def default_db_path() -> Path:
        root = Path.home() / "AppData" / "Local" / "ped-hunter"
        return root / "ped-hunter.sqlite3"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        held = getattr(self._held, "conn", None)
        if held is not None:
            try:
                yield held
            finally:
                # What closing a private connection did: drop anything uncommitted.
                if held.in_transaction:
                    held.rollback()
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Reuse one connection for this thread's calls inside the block.

        Opening and closing a connection per call costs several milliseconds
        (the last close checkpoints the write-ahead log), which dominates a
        batch of appended events or one display refresh. Each call still
        commits by itself and the connection closes with the block, so the
        database file is complete on disk between operations, as before.
        """
        if getattr(self._held, "conn", None) is not None:
            yield
            return
        with self.connect() as conn:
            self._held.conn = conn
            try:
                yield
            finally:
                self._held.conn = None

    def get_selected_healing_tool(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'healing_tool'").fetchone()
        return row["value"] if row else None

    def set_selected_healing_tool(self, name: str | None) -> None:
        name = name.strip() if name else None
        with self.connect() as conn:
            if name:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('healing_tool', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (name,))
            else:
                conn.execute("DELETE FROM app_settings WHERE key = 'healing_tool'")
            conn.commit()

    def get_selected_hot_tool(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'hot_tool'").fetchone()
        return row["value"] if row else None

    def set_selected_hot_tool(self, name: str | None) -> None:
        name = name.strip() if name else None
        with self.connect() as conn:
            if name:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('hot_tool', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (name,))
            else:
                conn.execute("DELETE FROM app_settings WHERE key = 'hot_tool'")
            conn.commit()

    def _ensure_schema(self) -> None:
        # A write-ahead log left behind means the last writer did not close
        # cleanly (crash, kill, power loss): the case a deep check exists for.
        try:
            unclean = Path(f"{self.db_path}-wal").stat().st_size > 0
        except OSError:
            unclean = False
        try:
            self._ensure_schema_once()
            self._verify_integrity(force=unclean)
        except sqlite3.DatabaseError as exc:
            if not _is_malformed_database_error(exc):
                raise
            backup_path = self._quarantine_malformed_database()
            self.recovery_message = f"Recovered from malformed database; backup saved to {backup_path}"
            self._ensure_schema_once()
            self._verify_integrity(force=True)

    def _ensure_schema_once(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
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
                CREATE TABLE IF NOT EXISTS event_exclusions (
                    event_id INTEGER PRIMARY KEY,
                    excluded_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS event_accounting (
                    session_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK(revision >= 0),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE VIEW IF NOT EXISTS accounted_events AS
                    SELECT e.* FROM events e
                    WHERE NOT EXISTS (SELECT 1 FROM event_exclusions x WHERE x.event_id = e.id);
                CREATE TABLE IF NOT EXISTS chart_trail_versions (
                    session_id TEXT NOT NULL,
                    exclusion_key TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    samples TEXT NOT NULL,
                    PRIMARY KEY(session_id, exclusion_key, event_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TRIGGER IF NOT EXISTS delete_event_exclusion_history
                AFTER DELETE ON events
                BEGIN
                    DELETE FROM event_exclusions WHERE event_id = OLD.id;
                    DELETE FROM chart_trail_versions WHERE event_id = OLD.id;
                END;
                CREATE TRIGGER IF NOT EXISTS delete_session_exclusion_history
                AFTER DELETE ON sessions
                BEGIN
                    DELETE FROM event_exclusions WHERE event_id IN
                        (SELECT id FROM events WHERE session_id = OLD.id);
                    DELETE FROM event_accounting WHERE session_id = OLD.id;
                    DELETE FROM chart_trail_versions WHERE session_id = OLD.id;
                END;
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
                CREATE TABLE IF NOT EXISTS healing_inference_state (
                    session_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TRIGGER IF NOT EXISTS delete_session_healing_inference
                AFTER DELETE ON sessions BEGIN
                    DELETE FROM healing_inference_state WHERE session_id = OLD.id;
                END;

                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
                CREATE INDEX IF NOT EXISTS idx_events_healing_identity
                ON events(session_id, timestamp, raw_message)
                WHERE kind = 'combat' AND
                  CASE WHEN json_valid(payload) THEN json_type(payload, '$.healed') END IN ('integer', 'real');
                CREATE INDEX IF NOT EXISTS idx_loadouts_active ON loadouts(active);
                """
            )
            conn.executescript(_SUMMARY_CACHE_SCHEMA)
            sessions_repair_added = False
            self._ensure_column(conn, "sessions", "loadout_id", "INTEGER")
            self._ensure_column(conn, "sessions", "loadout_snapshot", "TEXT")
            sessions_repair_added = self._ensure_column(conn, "sessions", "repair_shots", "INTEGER NOT NULL DEFAULT 0") or sessions_repair_added
            sessions_repair_added = self._ensure_column(conn, "sessions", "repair_decay", "REAL NOT NULL DEFAULT 0.0") or sessions_repair_added
            self._ensure_column(conn, "loadouts", "repair_shots", "INTEGER NOT NULL DEFAULT 0")
            conn.execute("BEGIN IMMEDIATE")
            if self._ensure_column(conn, "loadouts", "sort_order", "INTEGER"):
                # Seed once from the existing visible order, without changing
                # equipment fields, timestamps, active choice or session data.
                rows = conn.execute("SELECT id FROM loadouts ORDER BY active DESC, updated_at DESC, name, id").fetchall()
                conn.executemany("UPDATE loadouts SET sort_order = ? WHERE id = ?",
                                 ((index, row["id"]) for index, row in enumerate(rows)))
            conn.commit()
        if sessions_repair_added:
            self._backfill_session_repair_summary()

    def _verify_integrity(self, *, force: bool = False) -> None:
        """Full page/index verification, which reads the whole file.

        Opening the schema already rejects a file that is not a database. The
        deep check runs after an unclean close and otherwise weekly, so a large
        history does not delay every launch by its full read time.
        """
        now = datetime.now()
        with self.connect() as conn:
            if not force:
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key = ?", (_INTEGRITY_KEY,)).fetchone()
                try:
                    checked = datetime.fromisoformat(row["value"]) if row else None
                except (TypeError, ValueError):
                    checked = None
                if checked is not None and timedelta(0) <= now - checked < _INTEGRITY_INTERVAL:
                    return
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = str(row[0] if row else "")
            if result.casefold() != "ok":
                raise sqlite3.DatabaseError(f"database integrity check failed: {result}")
            try:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_INTEGRITY_KEY, now.isoformat(timespec="seconds")))
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Read-only or busy: the next open checks again.

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

    @staticmethod
    def _accounting_revision(conn: sqlite3.Connection, session_id: str) -> int:
        row = conn.execute("SELECT revision FROM event_accounting WHERE session_id = ?", (session_id,)).fetchone()
        return int(row[0]) if row else 0

    def get_accounting_revision(self, session_id: str) -> int:
        with self.connect() as conn:
            return self._accounting_revision(conn, session_id)

    def list_event_history_sessions(self) -> list[dict]:
        """Browse every stored run without aggregating its event history."""
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id, started_at, ended_at, loadout_snapshot FROM sessions ORDER BY started_at DESC, id DESC")]

    def loot_event_history(
        self, session_id: str, *, before_id: int | None = None,
        event_id: int | None = None, excluded: bool | None = None, limit: int = 101,
    ) -> list[dict]:
        """Keyset-paged original receipts, including their reversible status."""
        conditions = ["e.session_id = ?", "e.kind = 'loot'"]
        params: list[object] = [session_id]
        if before_id is not None:
            conditions.append("e.id < ?")
            params.append(before_id)
        if event_id is not None:
            conditions.append("e.id = ?")
            params.append(event_id)
        if excluded is not None:
            conditions.append("x.event_id IS NOT NULL" if excluded else "x.event_id IS NULL")
        params.append(max(1, min(int(limit), 501)))
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT e.*, x.excluded_at, x.event_id IS NOT NULL AS excluded "
                "FROM events e LEFT JOIN event_exclusions x ON x.event_id = e.id "
                "WHERE " + " AND ".join(conditions) + " ORDER BY e.id DESC LIMIT ?", params)]

    def set_loot_excluded(self, session_id: str, event_id: int, excluded: bool) -> bool:
        """Rebuild one run atomically without deleting or repricing originals."""
        from .event_exclusion import change_loot_exclusion
        return change_loot_exclusion(self, session_id, event_id, excluded)

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

    def save_chart_trails(
        self, session_id: str, trails: Mapping[int, Sequence[Sample]], *, accounting_revision: int = 0,
    ) -> None:
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
            if accounting_revision != self._accounting_revision(conn, session_id):
                raise ValueError("Event accounting changed; rebuild chart trails before saving")
            owned: set[int] = set()
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                owned.update(int(row["id"]) for row in conn.execute(
                    f"""
                    SELECT e.id FROM accounted_events e JOIN sessions s ON s.id = e.session_id
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
            conn.execute("BEGIN")
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
                if state.get('accounting_revision', 0) != self._accounting_revision(conn, session_id):
                    return None, {}
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
        retained = {pool['id']: pool for pool in state.get('closed_pools', [])}
        if state['active'] is not None:
            retained[state['active']['id']] = state['active']
        for pool_id, members in pools.items():
            if pool_id not in decisions or decisions[pool_id]['cost_pool_id'] != pool_id:
                raise ValueError("Attribution pool has no anchor decision")
            total, pending = members[0]['cost_pool_total'], members[0]['cost_unassigned']
            frozen = members[0]['cost_status'].startswith('frozen_')
            lifecycle = members[0]['cost_status'].split('shared_', 1)[0]
            if any(member['cost_pool_total'] != total or member['cost_unassigned'] != pending
                   or member['cost_status'].split('shared_', 1)[0] != lifecycle for member in members):
                raise ValueError("Inconsistent attribution pool")
            if not math.isclose(math.fsum(member['raw_cost'] for member in members), total, abs_tol=1e-9):
                raise ValueError("Attribution pool omits original input")
            if not math.isclose(math.fsum(member['cost'] or 0.0 for member in members) + pending, total, abs_tol=1e-9):
                raise ValueError("Attribution pool does not conserve input")
            if frozen:
                if pool_id in retained:
                    raise ValueError("Hard-finalized input cannot also remain reconsiderable")
                frozen_pending.append(pending)
            elif total or pool_id in retained or lifecycle == 'closed_':
                pool = retained.get(pool_id)
                if pool is None or pool['pending'] != pending:
                    raise ValueError("Attribution pool has no retained state")
                expected_lifecycle = '' if pool is state['active'] else 'closed_'
                if lifecycle != expected_lifecycle:
                    raise ValueError("Attribution pool lifecycle mismatch")
                if {member['event_id'] for member in pool['members']} != {
                    event_id for event_id, decision in decisions.items() if decision['cost_pool_id'] == pool_id
                }:
                    raise ValueError("Attribution pool membership mismatch")
                if any(decisions[member['event_id']]['raw_cost'] != member['raw_cost']
                       for member in pool['members']):
                    raise ValueError("Attribution state disagrees with original input")
        if any(pool_id not in pools for pool_id in retained):
            raise ValueError("Attribution state has no decisions")
        if not math.isclose(math.fsum(frozen_pending), state['frozen_pending'], abs_tol=1e-9):
            raise ValueError("Attribution state loses frozen input")

    def save_hunt_attribution(
        self, session_id: str, state: dict, decisions: Mapping[int, dict], *,
        observations: Mapping[int, Sample] | None = None,
        healing_allocations: Mapping[int, float] | None = None,
    ) -> None:
        """Commit decisions, bounded state and existing fan observations together.

        Hard-finalized v2 history is immutable. A v1 upgrade may reopen complete
        eligible pools; raw event prices, sessions and loadouts are never written.
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
            samples = {}
            healing = {}
            for event_id, amount in (healing_allocations or {}).items():
                if event_id not in (observations or {}) or isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
                    raise ValueError("Invalid fan healing allocation")
                healing[event_id] = float(amount)
            for event_id, values in (observations or {}).items():
                if event_id not in decisions:
                    raise ValueError("Fan observation has no changed decision")
                sample = bounded_samples([values])[0]
                weapon_cost = decisions[event_id]['cost']
                amount = healing.get(event_id, 0.0)
                expected = None if weapon_cost is None else math.fsum((weapon_cost, amount))
                if (weapon_cost is None and amount) or sample[0] != expected:
                    raise ValueError("Fan cost disagrees with weapon decision plus assigned healing")
                samples[event_id] = sample
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid attribution record") from exc
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if state.get('accounting_revision', 0) != self._accounting_revision(conn, session_id):
                raise ValueError("Event accounting changed; rebuild attribution before saving")
            if healing:
                booked = conn.execute(
                    """SELECT COALESCE(SUM(json_extract(payload, '$.healing_cost')), 0)
                    FROM accounted_events WHERE session_id = ? AND kind = 'combat'
                    AND json_valid(payload) AND json_extract(payload, '$.healing_allocation_eligible') = 1""",
                    (session_id,),
                ).fetchone()[0]
                if math.fsum(healing.values()) > float(booked) + 1e-9:
                    raise ValueError("Fan healing allocation exceeds booked eligible healing")
            previous_row = conn.execute(
                "SELECT state FROM hunt_attribution_state WHERE session_id = ?", (session_id,),
            ).fetchone()
            previous_state = None
            reset = (not decisions and state['active'] is None and not state.get('closed_pools')
                     and state['frozen_pending'] == 0 and state['activation_event_id'] == state['watermark'])
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
                if snapshot:
                    pool_ids.update(pool['id'] for pool in snapshot.get('closed_pools', []))
                    if snapshot['active']:
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

            # Read complete affected pools through the expression index. Closed
            # pools remain revisable; hard-finalized history cannot be reopened.
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
                        if old['raw_cost'] != decisions[event_id]['raw_cost']:
                            raise ValueError("Attribution cannot reprice original input")
                        if old['cost_status'].startswith('frozen_'):
                            reopened = (
                                previous_state is not None and previous_state['version'] == 1
                                and state['version'] == 2
                                and any(event_id in {member['event_id'] for member in pool['members']}
                                        for pool in state.get('closed_pools', []))
                            )
                            if not reopened:
                                raise ValueError("Hard-finalized attribution decisions are immutable")
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
            for event_id, decision in decisions.items():
                row = conn.execute(
                    "SELECT samples FROM chart_trails WHERE session_id = ? AND event_id = ?",
                    (session_id, event_id),
                ).fetchone()
                if row is None:
                    continue  # Never invent unobserved historical fan samples.
                old = previous.get(event_id)
                if event_id not in samples:
                    if old is None or old['cost'] != decision['cost']:
                        raise ValueError("Changed fan endpoint requires an atomic observation")
                    continue
                try:
                    history = bounded_samples(json.loads(row['samples']))
                except (TypeError, ValueError, OverflowError):
                    continue  # A corrupt history is not provenance to recreate.
                if history and history[-1] != samples[event_id]:
                    history = bounded_samples((*history, samples[event_id]))
                    conn.execute(
                        "UPDATE chart_trails SET samples = ? WHERE session_id = ? AND event_id = ?",
                        (json.dumps(history, separators=(",", ":"), allow_nan=False), session_id, event_id),
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

    def add_event(self, session_id: str, event: dict) -> dict:
        """Book an event and return its stored payload, including replay status."""
        payload = event.get("payload", {})
        healing = event["kind"] == "combat" and isinstance(payload, dict) and "healing_action" in payload
        if healing:
            payload = dict(payload)
        timestamp = event.get("timestamp")
        if timestamp is not None and not isinstance(timestamp, str):
            timestamp = str(timestamp)
        with self.connect() as conn:
            duplicate = None
            if healing:
                conn.execute("BEGIN IMMEDIATE")
                # Exact same-run timestamp/message replays retain an audit row
                # but no second charge, including legacy retained healing.
                duplicate = conn.execute(
                    """SELECT id FROM events
                    WHERE session_id = ? AND timestamp = ? AND raw_message = ?
                      AND kind = 'combat' AND
                      CASE WHEN json_valid(payload) THEN json_type(payload, '$.healed') END IN ('integer', 'real')
                    ORDER BY id LIMIT 1""",
                    (session_id, timestamp, event["raw_message"]),
                ).fetchone()
                if duplicate:
                    for key in ("healing_cost", "healing_decay_cost", "healing_ammo_cost", "healing_cost_estimated"):
                        payload.pop(key, None)
                    payload["healing_cost_status"] = "duplicate"
                    payload["healing_duplicate_of_event_id"] = duplicate["id"]
            cursor = conn.execute(
                "INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)",
                (session_id, timestamp, event["kind"], event["raw_message"], json.dumps(payload, ensure_ascii=False)),
            )
            if healing and not duplicate and isinstance(payload.get("healing_selection"), dict):
                finalize_healing_event(conn, session_id, int(cursor.lastrowid), timestamp, payload)
                conn.execute("UPDATE events SET payload = ? WHERE id = ?",
                             (json.dumps(payload, ensure_ascii=False), cursor.lastrowid))
                # Completing the row inserted by this same transaction is not an
                # edit of committed history: no reader has seen the earlier text.
                conn.execute("UPDATE session_event_mutations SET mutations = mutations - 1 WHERE session_id = ?",
                             (session_id,))
            if not healing:  # Healing is not a weapon shot or repair reset.
                self._advance_session_repair_state(conn, session_id, event["kind"], timestamp, payload)
            conn.commit()
        return payload

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

    def _advance_session_repair_state(
        self, conn: sqlite3.Connection, session_id: str, kind: str, timestamp: str | None, payload: object,
    ) -> None:
        """Apply one appended event to the durability counters without a replay.

        The counters are the shots/decay after the run's last repair in
        (timestamp, id) order. An appended shot that sorts after every repair
        adds to them; anything else (a repair, an out-of-order line, history
        edited since this Store last replayed it) takes the full replay.
        """
        mutations = conn.execute(
            "SELECT mutations FROM session_event_mutations WHERE session_id = ?", (session_id,)).fetchone()
        token = int(mutations[0]) if mutations else 0
        repairs = conn.execute(
            "SELECT MAX(COALESCE(timestamp, '')), COUNT(*) FROM events WHERE session_id = ? AND kind = 'repair'",
            (session_id,)).fetchone()
        if (kind == "repair" or not isinstance(payload, dict) or self._repair_replayed.get(session_id) != token
                or (kind == "combat" and repairs[1] and repairs[0] > (timestamp or ""))):
            self._recompute_session_repair_state(conn, session_id)
            self._repair_replayed[session_id] = token
            return
        session_row = conn.execute(
            "SELECT loadout_id, loadout_snapshot, repair_shots, repair_decay FROM sessions WHERE id = ?",
            (session_id,)).fetchone()
        if session_row is None:
            self._recompute_session_repair_state(conn, session_id)
            return
        repair_shots = int(session_row["repair_shots"] or 0)
        repair_decay = float(session_row["repair_decay"] or 0.0)
        if kind == "combat":
            if payload.get("shot_cost") is not None:
                repair_shots += 1
            repair_decay += float(payload.get("repair_decay") or 0.0)
        self._store_session_repair_state(conn, session_id, session_row, repair_shots, repair_decay, bool(repairs[1]))

    def _recompute_session_repair_state(self, conn: sqlite3.Connection, session_id: str) -> None:
        session_row = conn.execute("SELECT loadout_id, loadout_snapshot FROM sessions WHERE id = ?", (session_id,)).fetchone()
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
        self._store_session_repair_state(conn, session_id, session_row, repair_shots, repair_decay, had_repair)

    @staticmethod
    def _store_session_repair_state(conn, session_id, session_row, repair_shots, repair_decay, had_repair) -> None:
        loadout_id = int(session_row["loadout_id"]) if session_row and session_row["loadout_id"] is not None else None
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
                        ammo_burn, decay, cost_per_shot, active, repair_shots, created_at, updated_at, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM loadouts))
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
            rows = conn.execute("SELECT * FROM loadouts ORDER BY sort_order, id").fetchall()
        return [_loadout_from_row(row) for row in rows]

    def reorder_loadouts(self, loadout_ids: list[int]) -> None:
        """Persist an exact saved-ID permutation; never activate or save equipment."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actual = {int(row["id"]) for row in conn.execute("SELECT id FROM loadouts")}
            if (any(type(item) is not int for item in loadout_ids)
                    or len(loadout_ids) != len(actual) or set(loadout_ids) != actual):
                raise ValueError("Saved setups changed; refresh the list before reordering.")
            conn.executemany("UPDATE loadouts SET sort_order = ? WHERE id = ?",
                             ((index, item) for index, item in enumerate(loadout_ids)))
            conn.commit()

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
        sessions = self._session_summaries("WHERE s.ended_at IS NULL ORDER BY s.started_at DESC LIMIT 1")
        return sessions[0] if sessions else None

    def get_last_contributed_session(self) -> SessionSummary | None:
        last: dict[str, str | None] = {}
        sessions = self._session_summaries("ORDER BY s.started_at DESC", last_timestamps=last)
        if not sessions:
            return None
        return max(sessions, key=lambda item: (last[item.session_id] or item.started_at, item.started_at))

    def get_session_resume_state(self, session_id: str) -> SessionResumeState | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT ended_at, loadout_snapshot FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
        return SessionResumeState(row["ended_at"], _loadout_snapshot(row["loadout_snapshot"])) if row else None

    def get_session(self, session_id: str) -> SessionSummary | None:
        sessions = self._session_summaries("WHERE s.id = ?", (session_id,))
        return sessions[0] if sessions else None

    def list_recent_sessions(self, limit: int = 5) -> list[SessionSummary]:
        return self._session_summaries("ORDER BY s.started_at DESC LIMIT ?", (limit,))

    def list_all_sessions(self) -> list[SessionSummary]:
        """Return every stored session newest-first for aggregate reporting."""
        return self._session_summaries("ORDER BY s.started_at DESC")

    def _session_summaries(
        self, selection: str, params: tuple = (), *, last_timestamps: dict | None = None,
    ) -> list[SessionSummary]:
        """Summaries from saved per-run totals plus only the events added since.

        Recorded events are append-only, so a run's totals up to an event ID
        stay valid until its membership changes (accounting revision) or a
        stored row is edited/deleted (mutation token, kept by triggers for any
        writer). Either change replays that run from its events.
        """
        summaries: list[SessionSummary] = []
        stale: list[tuple] = []
        with self.connect() as conn:
            conn.execute("BEGIN")  # One snapshot for the tokens and the events they describe.
            for row in conn.execute(_SESSION_ROWS_SQL + selection, params).fetchall():
                session_id = row["id"]
                tokens = (int(row["accounting_revision"]), int(row["mutations"]))
                totals, upto = None, 0
                if row["totals"] is not None and tokens == (row["cached_revision"], row["cached_mutations"]):
                    try:
                        totals = json.loads(row["totals"])
                        if not isinstance(totals, dict) or set(totals) != _TOTALS_SHAPE:
                            totals = None
                    except ValueError:
                        totals = None
                    upto = int(row["upto_event_id"]) if totals is not None else 0
                newest = int(conn.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM events WHERE session_id = ?", (session_id,)).fetchone()[0])
                cached = totals is not None
                if not cached or newest > upto:
                    span = (session_id, upto, newest)
                    delta = conn.execute(_SESSION_DELTA_SQL, span).fetchone()
                    latest = conn.execute(_SESSION_LATEST_SQL, span * len(_LATEST_KEYS)).fetchone()
                    base = totals or dict.fromkeys(_TOTALS_SHAPE, 0)
                    totals = {key: base[key] + (delta[key] or 0) for key in _SUM_KEYS}
                    for key in _LATEST_KEYS:
                        totals[key] = base[key] if latest[key] is None else latest[key]
                    stamps = [value for value in (base["last_timestamp"], delta["last_timestamp"]) if value]
                    totals["last_timestamp"] = max(stamps) if stamps else None
                    if not cached or newest - upto >= _TOTALS_SAVE_EVERY or row["ended_at"] is not None:
                        stale.append((session_id, newest, *tokens, json.dumps(totals)))
                if last_timestamps is not None:
                    last_timestamps[session_id] = totals["last_timestamp"]
                summaries.append(_session_from_row(row, totals))
        if stale:
            self._save_session_totals(stale)
        return summaries

    def _save_session_totals(self, entries: list[tuple]) -> None:
        """Best effort: a busy or read-only database only means recomputing later."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=0)
        except sqlite3.Error:
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
            for session_id, upto, revision, mutations, totals in entries:
                current = conn.execute(
                    """SELECT COALESCE(a.revision, 0), COALESCE(m.mutations, 0) FROM sessions s
                       LEFT JOIN event_accounting a ON a.session_id = s.id
                       LEFT JOIN session_event_mutations m ON m.session_id = s.id WHERE s.id = ?""",
                    (session_id,)).fetchone()
                if current is None or tuple(current) != (revision, mutations):
                    continue  # History changed after it was read; never publish the older view.
                conn.execute(
                    """INSERT INTO session_totals (session_id, upto_event_id, revision, mutations, totals)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(session_id) DO UPDATE SET upto_event_id = excluded.upto_event_id,
                           revision = excluded.revision, mutations = excluded.mutations, totals = excluded.totals
                       WHERE excluded.upto_event_id >= session_totals.upto_event_id
                          OR excluded.revision != session_totals.revision
                          OR excluded.mutations != session_totals.mutations""",
                    (session_id, upto, revision, mutations, totals))
            conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()

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
            conn.execute("BEGIN")
            mutations = conn.execute(
                "SELECT mutations FROM session_event_mutations WHERE session_id = ?", (session_id,)).fetchone()
            token = int(mutations[0]) if mutations else 0
            known = self._skill_totals.get(session_id)
            if known is None or known[0] != token:
                known = (token, 0, {}, {})  # first read, or stored rows were edited: replay
            rows = conn.execute(
                """
                SELECT id, payload
                FROM events
                WHERE session_id = ? AND kind = 'skill' AND id > ?
                ORDER BY id
                """,
                (session_id, known[1]),
            ).fetchall()

        totals: dict[str, float] = dict(known[2])
        procs: dict[str, int] = dict(known[3])
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
        if rows:  # published complete; later reads copy, never modify, these totals
            self._skill_totals[session_id] = (token, int(rows[-1]["id"]), totals, procs)

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


_INTEGRITY_KEY = "storage.integrity_checked_at"
_INTEGRITY_INTERVAL = timedelta(days=7)
_TOTALS_SAVE_EVERY = 256  # appended events before a live run's saved totals advance

_SUMMARY_CACHE_SCHEMA = """
    CREATE INDEX IF NOT EXISTS idx_events_session_kind ON events(session_id, kind);
    CREATE TABLE IF NOT EXISTS session_totals (
        session_id TEXT PRIMARY KEY,
        upto_event_id INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        mutations INTEGER NOT NULL,
        totals TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS session_event_mutations (
        session_id TEXT PRIMARY KEY,
        mutations INTEGER NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS count_event_update
    AFTER UPDATE OF session_id, timestamp, kind, payload ON events
    WHEN NOT (OLD.kind = 'manufacturing_allocation' AND NEW.kind = OLD.kind AND NEW.session_id = OLD.session_id)
    BEGIN
        INSERT OR IGNORE INTO session_event_mutations (session_id, mutations) VALUES (OLD.session_id, 0);
        INSERT OR IGNORE INTO session_event_mutations (session_id, mutations) VALUES (NEW.session_id, 0);
        UPDATE session_event_mutations SET mutations = mutations + 1
        WHERE session_id IN (OLD.session_id, NEW.session_id);
    END;
    CREATE TRIGGER IF NOT EXISTS count_event_delete
    AFTER DELETE ON events
    BEGIN
        INSERT OR IGNORE INTO session_event_mutations (session_id, mutations) VALUES (OLD.session_id, 0);
        UPDATE session_event_mutations SET mutations = mutations + 1 WHERE session_id = OLD.session_id;
    END;
    CREATE TRIGGER IF NOT EXISTS count_event_exclusion_insert
    AFTER INSERT ON event_exclusions
    BEGIN
        INSERT OR IGNORE INTO session_event_mutations (session_id, mutations)
            SELECT session_id, 0 FROM events WHERE id = NEW.event_id;
        UPDATE session_event_mutations SET mutations = mutations + 1
        WHERE session_id = (SELECT session_id FROM events WHERE id = NEW.event_id);
    END;
    CREATE TRIGGER IF NOT EXISTS count_event_exclusion_delete
    AFTER DELETE ON event_exclusions
    BEGIN
        INSERT OR IGNORE INTO session_event_mutations (session_id, mutations)
            SELECT session_id, 0 FROM events WHERE id = OLD.event_id;
        UPDATE session_event_mutations SET mutations = mutations + 1
        WHERE session_id = (SELECT session_id FROM events WHERE id = OLD.event_id);
    END;
    CREATE TRIGGER IF NOT EXISTS delete_session_totals
    AFTER DELETE ON sessions
    BEGIN
        DELETE FROM session_totals WHERE session_id = OLD.id;
        DELETE FROM session_event_mutations WHERE session_id = OLD.id;
    END;
"""

_SUM_KEYS = ("events", "loot_value", "combat_damage", "healing_cost", "estimated_healing_uses",
             "unpriced_heal_events", "hunting_cost")
_LATEST_KEYS = ("last_input_cost", "manufacturing_attempt", "manufacturing_attempts_total",
                "manufacturing_attempts_remaining")
_TOTALS_SHAPE = frozenset((*_SUM_KEYS, *_LATEST_KEYS, "last_timestamp"))

_SESSION_ROWS_SQL = """
    SELECT s.id, s.started_at, s.ended_at, s.activity, s.loadout_snapshot,
           s.repair_shots, s.repair_decay, COALESCE(a.revision, 0) AS accounting_revision,
           COALESCE(m.mutations, 0) AS mutations, t.upto_event_id, t.totals,
           t.revision AS cached_revision, t.mutations AS cached_mutations
    FROM sessions s
    LEFT JOIN event_accounting a ON a.session_id = s.id
    LEFT JOIN session_event_mutations m ON m.session_id = s.id
    LEFT JOIN session_totals t ON t.session_id = s.id
"""

# Totals of one run's accounted events with upto < id <= newest.
_SESSION_DELTA_SQL = """
    SELECT COUNT(e.id) AS events,
           MAX(e.timestamp) AS last_timestamp,
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
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload)
               THEN json_extract(e.payload, '$.healing_cost') ELSE 0 END), 0) AS healing_cost,
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload)
               AND json_extract(e.payload, '$.healing_cost_status') = 'selected_tool_estimate'
               THEN 1 ELSE 0 END), 0) AS estimated_healing_uses,
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload)
               AND json_extract(e.payload, '$.healing_cost_status') IN
                   ('not_selected', 'tool_not_found', 'missing_timestamp', 'unresolved_activation', 'unknown_cost')
               THEN 1 ELSE 0 END), 0) AS unpriced_heal_events,
           COALESCE(SUM(CASE
                WHEN e.kind = 'combat' AND json_valid(e.payload) THEN
                    COALESCE(json_extract(e.payload, '$.shot_cost'), json_extract(e.payload, '$.ammo_cost'), 0)
                    + COALESCE(json_extract(e.payload, '$.healing_cost'), 0)
                WHEN e.kind = 'repair' AND json_valid(e.payload) THEN
                    COALESCE(json_extract(e.payload, '$.estimated_cost'), json_extract(e.payload, '$.repair_cost'), 0)
                WHEN e.kind IN ('craft', 'manufacturing_offset') AND json_valid(e.payload) THEN json_extract(e.payload, '$.total_cost')
                ELSE 0
            END), 0) AS hunting_cost
    FROM accounted_events e
    WHERE e.session_id = ? AND e.id > ? AND e.id <= ?
"""


def _latest_value_sql(field: str) -> str:
    # NULL: no such receipt in the span (keep the saved value); a JSON null reads as 0, as before.
    return (f"(SELECT COALESCE(json_extract(e.payload, '$.{field}'), 0) FROM accounted_events e "
            "WHERE e.session_id = ? AND e.id > ? AND e.id <= ? AND e.kind IN ('craft', 'loot') "
            f"AND json_valid(e.payload) AND json_type(e.payload, '$.{field}') IS NOT NULL "
            "ORDER BY e.id DESC LIMIT 1)")


_SESSION_LATEST_SQL = "SELECT " + ", ".join(
    f"{_latest_value_sql(field)} AS {key}" for key, field in zip(
        _LATEST_KEYS, ("input_cost", "attempt_number", "attempts_total", "attempts_remaining")))



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


def _session_from_row(row: sqlite3.Row, totals: Mapping[str, object]) -> SessionSummary:
    loot_value = float(totals["loot_value"] or 0)
    hunting_cost = float(totals["hunting_cost"] or 0)
    loadout_snapshot = _loadout_snapshot(row["loadout_snapshot"])
    loadout_name = _single_line_text(loadout_snapshot.get("name")) or None if loadout_snapshot else None
    return SessionSummary(
        session_id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        activity=row["activity"],
        loot_value=loot_value,
        combat_damage=float(totals["combat_damage"] or 0),
        hunting_cost=hunting_cost,
        net_value=loot_value - hunting_cost,
        events=int(totals["events"] or 0),
        loadout_name=loadout_name,
        loadout_snapshot=loadout_snapshot,
        repair_shots=int(row["repair_shots"] or 0),
        repair_decay=float(row["repair_decay"] or 0),
        last_input_cost=float(totals["last_input_cost"] or 0),
        manufacturing_attempt=int(totals["manufacturing_attempt"] or 0),
        manufacturing_attempts_total=int(totals["manufacturing_attempts_total"] or 0),
        manufacturing_attempts_remaining=int(totals["manufacturing_attempts_remaining"] or 0),
        healing_cost=float(totals["healing_cost"] or 0),
        estimated_healing_uses=int(totals["estimated_healing_uses"] or 0),
        unpriced_heal_events=int(totals["unpriced_heal_events"] or 0),
        accounting_revision=int(row["accounting_revision"]),
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
