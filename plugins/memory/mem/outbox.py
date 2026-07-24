"""Durable transport outbox for completed memory turns."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class MemoryWriteOutbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS pending_writes ("
                    "write_id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                    "next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT, created_at REAL NOT NULL)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pending_writes_due "
                    "ON pending_writes(next_attempt_at, created_at)"
                )

    def enqueue(self, payload: dict[str, Any]) -> None:
        write_id = str(payload["write_id"])
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_writes "
                    "(write_id, payload, attempts, next_attempt_at, created_at) "
                    "VALUES (?, ?, 0, 0, ?)",
                    (write_id, json.dumps(payload, ensure_ascii=False), time.time()),
                )

    def next_due(self) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT write_id, payload, attempts FROM pending_writes "
                "WHERE next_attempt_at <= ? ORDER BY created_at LIMIT 1",
                (time.time(),),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row[1])
        payload["_outbox_attempts"] = int(row[2])
        return payload

    def mark_delivered(self, write_id: str) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM pending_writes WHERE write_id = ?", (write_id,))

    def mark_failed(self, write_id: str, *, attempts: int, error: str) -> None:
        delay = min(60.0, float(2 ** min(max(attempts, 1), 6)))
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE pending_writes SET attempts = ?, next_attempt_at = ?, last_error = ? "
                    "WHERE write_id = ?",
                    (attempts, time.time() + delay, error[:500], write_id),
                )

    def pending_count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM pending_writes").fetchone()[0])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
