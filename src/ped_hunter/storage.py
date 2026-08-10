"""SQLite storage for PED Hunter."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import sqlite3
from typing import Iterator
import uuid


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

    def lifetime_totals(self) -> LifetimeTotals:
        """Return meaningful all-session PED totals and averages."""
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


def _session_from_row(row: sqlite3.Row) -> SessionSummary:
    loot_value = float(row["loot_value"] or 0)
    hunting_cost = float(row["hunting_cost"] or 0)
    loadout_name = None
    loadout_snapshot = None
    if row["loadout_snapshot"]:
        try:
            loaded_snapshot = json.loads(row["loadout_snapshot"])
            if isinstance(loaded_snapshot, dict):
                loadout_snapshot = loaded_snapshot
                loadout_name = _single_line_text(loaded_snapshot.get("name")) or None
        except json.JSONDecodeError:
            loadout_name = None
            loadout_snapshot = None
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
