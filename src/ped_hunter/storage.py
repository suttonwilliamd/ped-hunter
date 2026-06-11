
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
    events: int


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
                    notes TEXT DEFAULT ''
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
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
                """
            )
            conn.commit()

    def start_session(self, activity: str = "hunt") -> str:
        session_id = f"ph-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at, activity) VALUES (?, ?, ?)",
                (session_id, started_at, activity),
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

    def get_current_session(self) -> SessionSummary | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.id, s.started_at, s.ended_at, s.activity,
                       COUNT(e.id) AS events,
                       COALESCE(SUM(CASE WHEN e.kind = 'loot' THEN json_extract(e.payload, '$.value') ELSE 0 END), 0) AS loot_value,
                       COALESCE(SUM(CASE WHEN e.kind = 'combat' THEN json_extract(e.payload, '$.damage') ELSE 0 END), 0) AS combat_damage
                FROM sessions s
                LEFT JOIN events e ON e.session_id = s.id
                WHERE s.ended_at IS NULL
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return SessionSummary(
            session_id=row["id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            activity=row["activity"],
            loot_value=float(row["loot_value"] or 0),
            combat_damage=float(row["combat_damage"] or 0),
            events=int(row["events"] or 0),
        )

    def list_recent_sessions(self, limit: int = 5) -> list[SessionSummary]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.started_at, s.ended_at, s.activity,
                       COUNT(e.id) AS events,
                       COALESCE(SUM(CASE WHEN e.kind = 'loot' THEN json_extract(e.payload, '$.value') ELSE 0 END), 0) AS loot_value,
                       COALESCE(SUM(CASE WHEN e.kind = 'combat' THEN json_extract(e.payload, '$.damage') ELSE 0 END), 0) AS combat_damage
                FROM sessions s
                LEFT JOIN events e ON e.session_id = s.id
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SessionSummary(
                session_id=row["id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                activity=row["activity"],
                loot_value=float(row["loot_value"] or 0),
                combat_damage=float(row["combat_damage"] or 0),
                events=int(row["events"] or 0),
            )
            for row in rows
        ]
