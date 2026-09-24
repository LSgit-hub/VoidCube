"""Durable approval request lifecycle for crash recovery and audit."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ...infrastructure.config.runtime_paths import get_VoidCube_home


class ApprovalJournal:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or (get_VoidCube_home() / "approval_events.db")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
            command TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, resolved_at REAL
        )""")
        self._conn.commit()

    def request(self, *, session_id: str, turn_id: str, command: str, description: str) -> str:
        request_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO approvals(request_id, session_id, turn_id, command, description, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (request_id, session_id, turn_id, command, description, time.time()),
            )
            self._conn.commit()
        return request_id

    def resolve(self, request_id: str, status: str, reason: str = "") -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE approvals SET status = ?, reason = ?, resolved_at = ? WHERE request_id = ? AND status = 'pending'",
                (str(status), str(reason or ""), time.time(), str(request_id)),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def pending(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE session_id = ? AND status = 'pending' ORDER BY created_at",
                (str(session_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()


__all__ = ["ApprovalJournal"]
