from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def get_session(self, session_id: str) -> dict | None:
        now = int(time.time())
        with self.lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, expires_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] <= now:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                return None
            return json.loads(row["payload"])

    def save_session(self, session_id: str, payload: dict, ttl_seconds: int) -> None:
        now = int(time.time())
        expires_at = now + ttl_seconds
        serialized = json.dumps(payload)
        with self.lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, payload, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (session_id, serialized, expires_at, now, now),
            )
            conn.commit()

    def delete_session(self, session_id: str) -> None:
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    def cleanup_sessions(self) -> None:
        now = int(time.time())
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            conn.commit()

    def save_review_report(self, document_name: str, source_type: str, payload: dict) -> int:
        now = int(time.time())
        with self.lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO review_reports (document_name, source_type, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (document_name, source_type, json.dumps(payload), now),
            )
            conn.commit()
            return int(cursor.lastrowid)
