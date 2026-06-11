"""SQLite storage for PED Hunter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import sqlite3
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


class Store:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else self.default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @staticmethod
    def default_db_path() -> Path:
        root = Path.home() / "AppData" / "Local" / "ped-hunter"
        return root / "ped-hunter.sqlite3"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
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


_SESSION_SUMMARY_SQL = """
    SELECT s.id, s.started_at, s.ended_at, s.activity, s.loadout_snapshot,
           COUNT(e.id) AS events,
           COALESCE(SUM(CASE WHEN e.kind = 'loot' AND json_valid(e.payload) THEN json_extract(e.payload, '$.value') ELSE 0 END), 0) AS loot_value,
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload) THEN json_extract(e.payload, '$.damage') ELSE 0 END), 0) AS combat_damage,
           COALESCE(SUM(CASE WHEN e.kind = 'combat' AND json_valid(e.payload) THEN json_extract(e.payload, '$.shot_cost') ELSE 0 END), 0) AS hunting_cost
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
