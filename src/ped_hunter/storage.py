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
                    loadout_snapshot TEXT
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
                CREATE INDEX IF NOT EXISTS idx_loadouts_active ON loadouts(active);
                """
            )
            self._ensure_column(conn, "sessions", "loadout_id", "INTEGER")
            self._ensure_column(conn, "sessions", "loadout_snapshot", "TEXT")
            conn.commit()

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
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def start_session(self, activity: str = "hunt", loadout: LoadoutRecord | None = None) -> str:
        session_id = f"ph-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now().isoformat(timespec="seconds")
        loadout_snapshot = json.dumps(loadout_to_dict(loadout), ensure_ascii=False) if loadout else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, started_at, activity, loadout_id, loadout_snapshot)
                VALUES (?, ?, ?, ?, ?)
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
            conn.commit()

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
                        ammo_burn, decay, cost_per_shot, active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        updated_at=excluded.updated_at
                    """,
                    _loadout_values(loadout, active, now, now),
                )
                row = conn.execute("SELECT id FROM loadouts WHERE name = ?", (loadout.name,)).fetchone()
                loadout_id = int(row["id"] if row else cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE loadouts SET
                        name=?, weapon=?, amp=?, scope=?, sight_1=?, sight_2=?,
                        damage_enhancers=?, accuracy_enhancers=?, economy_enhancers=?,
                        ammo_burn=?, decay=?, cost_per_shot=?, active=?, updated_at=?
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
           COUNT(e.id) AS events,
           COALESCE(SUM(CASE
               WHEN e.kind = 'loot'
                    AND json_valid(e.payload)
                    AND COALESCE(lower(trim(json_extract(e.payload, '$.item_name'))), '') != 'universal ammo'
               THEN json_extract(e.payload, '$.value')
               ELSE 0
           END), 0) AS loot_value,
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload) THEN json_extract(e.payload, '$.damage') ELSE 0 END), 0) AS combat_damage,
           COALESCE(SUM(CASE
               WHEN e.kind = 'combat' AND json_valid(e.payload) THEN json_extract(e.payload, '$.shot_cost')
               WHEN e.kind = 'craft' AND json_valid(e.payload) THEN json_extract(e.payload, '$.total_cost')
               ELSE 0
           END), 0) AS hunting_cost
    FROM sessions s
    LEFT JOIN events e ON e.session_id = s.id
"""


def _session_from_row(row: sqlite3.Row) -> SessionSummary:
    loot_value = float(row["loot_value"] or 0)
    hunting_cost = float(row["hunting_cost"] or 0)
    loadout_name = None
    if row["loadout_snapshot"]:
        try:
            loadout_name = json.loads(row["loadout_snapshot"]).get("name")
        except json.JSONDecodeError:
            loadout_name = None
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
        created_at,
        updated_at,
    )


def _loadout_from_row(row: sqlite3.Row) -> LoadoutRecord:
    return LoadoutRecord(
        id=int(row["id"]),
        name=row["name"],
        weapon=row["weapon"],
        amp=row["amp"] or "",
        scope=row["scope"] or "",
        sight_1=row["sight_1"] or "",
        sight_2=row["sight_2"] or "",
        damage_enhancers=int(row["damage_enhancers"] or 0),
        accuracy_enhancers=int(row["accuracy_enhancers"] or 0),
        economy_enhancers=int(row["economy_enhancers"] or 0),
        ammo_burn=int(row["ammo_burn"] or 0),
        decay=float(row["decay"] or 0),
        cost_per_shot=float(row["cost_per_shot"] or 0),
        active=bool(row["active"]),
    )


def loadout_to_dict(loadout: LoadoutRecord | None) -> dict[str, object] | None:
    if loadout is None:
        return None
    return {
        "id": loadout.id,
        "name": loadout.name,
        "weapon": loadout.weapon,
        "amp": loadout.amp,
        "scope": loadout.scope,
        "sight_1": loadout.sight_1,
        "sight_2": loadout.sight_2,
        "damage_enhancers": loadout.damage_enhancers,
        "accuracy_enhancers": loadout.accuracy_enhancers,
        "economy_enhancers": loadout.economy_enhancers,
        "ammo_burn": loadout.ammo_burn,
        "decay": loadout.decay,
        "cost_per_shot": loadout.cost_per_shot,
    }


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
