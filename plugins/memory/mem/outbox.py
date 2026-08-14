"""Durable transport outbox for completed memory turns."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryWriteOutbox:
    def __init__(self, path: str | Path, *, max_attempts: int = 12) -> None:
        self.path = Path(path)
        self.max_attempts = max(1, int(max_attempts))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS pending_writes ("
                    "write_id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                    "next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT, created_at REAL NOT NULL, "
                    "status TEXT NOT NULL DEFAULT 'pending', dead_letter_at REAL)"
                )
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(pending_writes)")
                }
                if "status" not in columns:
                    conn.execute(
                        "ALTER TABLE pending_writes ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
                    )
                if "dead_letter_at" not in columns:
                    conn.execute(
                        "ALTER TABLE pending_writes ADD COLUMN dead_letter_at REAL"
                    )
                conn.execute("DROP INDEX IF EXISTS idx_pending_writes_due")
                conn.execute(
                    "CREATE INDEX idx_pending_writes_due "
                    "ON pending_writes(status, next_attempt_at, created_at)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS outbox_state ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
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
                "WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY created_at LIMIT 1",
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
                conn.execute(
                    "INSERT INTO outbox_state(key, value) VALUES ('last_success_at', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(time.time()),),
                )

    def mark_failed(self, write_id: str, *, attempts: int, error: str) -> None:
        delay = min(60.0, float(2 ** min(max(attempts, 1), 6)))
        status = "dead_letter" if attempts >= self.max_attempts else "pending"
        dead_letter_at = time.time() if status == "dead_letter" else None
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE pending_writes SET attempts = ?, next_attempt_at = ?, last_error = ?, "
                    "status = ?, dead_letter_at = ? "
                    "WHERE write_id = ?",
                    (attempts, time.time() + delay, error[:500], status, dead_letter_at, write_id),
                )

    def pending_count(self) -> int:
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM pending_writes WHERE status = 'pending'"
                ).fetchone()[0]
            )

    def health_snapshot(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            pending, oldest, last_error = conn.execute(
                "SELECT COUNT(*), MIN(created_at), "
                "(SELECT last_error FROM pending_writes WHERE status = 'pending' "
                "ORDER BY created_at DESC LIMIT 1) "
                "FROM pending_writes WHERE status = 'pending'"
            ).fetchone()
            dead_letter = conn.execute(
                "SELECT COUNT(*) FROM pending_writes WHERE status = 'dead_letter'"
            ).fetchone()[0]
            state = conn.execute(
                "SELECT value FROM outbox_state WHERE key = 'last_success_at'"
            ).fetchone()
        return {
            "pending_count": int(pending or 0),
            "dead_letter_count": int(dead_letter or 0),
            "oldest_pending_at": (
                datetime.fromtimestamp(float(oldest), tz=timezone.utc).isoformat()
                if oldest is not None
                else None
            ),
            "last_success_at": (
                datetime.fromtimestamp(float(state[0]), tz=timezone.utc).isoformat()
                if state
                else None
            ),
            "last_error": str(last_error) if last_error else None,
            "max_attempts": self.max_attempts,
        }

    def requeue_dead_letter(self, write_id: str) -> bool:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "UPDATE pending_writes SET status = 'pending', next_attempt_at = 0, "
                    "dead_letter_at = NULL WHERE write_id = ? AND status = 'dead_letter'",
                    (write_id,),
                )
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
