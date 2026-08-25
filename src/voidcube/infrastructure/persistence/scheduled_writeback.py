"""SQLite-backed completion outbox for scheduled API-A executions."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict
from .sqlite_owner import SQLiteOwnerLease


class SqliteScheduledWritebackOutbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_lease = SQLiteOwnerLease(self.path, "scheduled-writeback-owner")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS pending_writebacks ("
                    "run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                    "attempts INTEGER NOT NULL DEFAULT 0, "
                    "next_attempt_at REAL NOT NULL DEFAULT 0, "
                    "last_error TEXT NOT NULL DEFAULT '', "
                    "dead_letter INTEGER NOT NULL DEFAULT 0, "
                    "created_at REAL NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_scheduled_writebacks_due "
                    "ON pending_writebacks(dead_letter, next_attempt_at, created_at)"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def close(self) -> None:
        self._owner_lease.close()

    def enqueue(self, run_id: str, payload: Dict[str, Any]) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT OR REPLACE INTO pending_writebacks "
                    "(run_id, payload, attempts, next_attempt_at, last_error, "
                    "dead_letter, created_at) VALUES (?, ?, 0, 0, '', 0, ?)",
                    (run_id, json.dumps(payload, ensure_ascii=False), time.time()),
                )

    def next_due(self) -> Dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT run_id, payload, attempts FROM pending_writebacks "
                "WHERE dead_letter = 0 AND next_attempt_at <= ? "
                "ORDER BY created_at LIMIT 1",
                (time.time(),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[1])
        payload["_outbox_run_id"] = str(row[0])
        payload["_outbox_attempts"] = int(row[2])
        return payload

    def mark_delivered(self, run_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM pending_writebacks WHERE run_id = ?", (run_id,)
                )

    def mark_failed(self, run_id: str, *, attempts: int, error: str) -> None:
        delay = min(60.0, float(2 ** min(max(attempts, 1), 6)))
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_writebacks SET attempts = ?, "
                    "next_attempt_at = ?, last_error = ? WHERE run_id = ?",
                    (attempts, time.time() + delay, error[:1000], run_id),
                )

    def mark_dead(self, run_id: str, *, attempts: int, error: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_writebacks SET attempts = ?, "
                    "last_error = ?, dead_letter = 1 WHERE run_id = ?",
                    (attempts, error[:1000], run_id),
                )

    def pending_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM pending_writebacks "
                    "WHERE dead_letter = 0"
                ).fetchone()[0]
            )


__all__ = ["SqliteScheduledWritebackOutbox"]
