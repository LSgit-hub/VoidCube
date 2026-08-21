"""Durable transport outbox for completed memory turns."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_DEFAULT_QUEUE_PATHS = {
    "api_a": "runtime/memory/write-outbox.sqlite3",
    "companion": "runtime/memory/companion-write-outbox.sqlite3",
    "gateway": "runtime/memory/gateway-write-outbox.sqlite3",
}


@dataclass(frozen=True, slots=True)
class MemoryOutboxRuntimeSettings:
    queue_paths: Mapping[str, str]
    max_attempts: int = 12
    lease_seconds: float = 30.0
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 60.0
    health_report_interval_seconds: float = 10.0
    shutdown_drain_timeout_seconds: float = 5.0

    def path_for(self, queue_name: str, *, home: str | Path) -> Path:
        if queue_name not in _DEFAULT_QUEUE_PATHS:
            raise ValueError(f"Unknown memory outbox queue: {queue_name}")
        configured = Path(
            str(self.queue_paths.get(queue_name) or _DEFAULT_QUEUE_PATHS[queue_name])
        )
        return configured if configured.is_absolute() else Path(home) / configured

    def create(self, queue_name: str, *, home: str | Path) -> "MemoryWriteOutbox":
        return MemoryWriteOutbox(
            self.path_for(queue_name, home=home),
            max_attempts=self.max_attempts,
            lease_seconds=self.lease_seconds,
            retry_base_seconds=self.retry_base_seconds,
            retry_max_seconds=self.retry_max_seconds,
        )


def load_memory_outbox_settings(
    config: Mapping[str, Any] | None = None,
) -> MemoryOutboxRuntimeSettings:
    """Load the one runtime contract shared by every Mem transport outbox."""
    if config is None:
        try:
            from voidcube.infrastructure.config.configuration import load_config

            config = load_config()
        except Exception:
            config = {}
    memory = config.get("memory") if isinstance(config, Mapping) else {}
    outbox = memory.get("outbox") if isinstance(memory, Mapping) else {}
    outbox = outbox if isinstance(outbox, Mapping) else {}
    paths = outbox.get("paths")
    configured_paths = dict(paths) if isinstance(paths, Mapping) else {}
    return MemoryOutboxRuntimeSettings(
        queue_paths={**_DEFAULT_QUEUE_PATHS, **configured_paths},
        max_attempts=max(1, min(1000, int(outbox.get("max_attempts", 12)))),
        lease_seconds=max(1.0, float(outbox.get("lease_seconds", 30.0))),
        retry_base_seconds=max(
            0.001, float(outbox.get("retry_base_seconds", 2.0))
        ),
        retry_max_seconds=max(
            0.001, float(outbox.get("retry_max_seconds", 60.0))
        ),
        health_report_interval_seconds=max(
            1.0, float(outbox.get("health_report_interval_seconds", 10.0))
        ),
        shutdown_drain_timeout_seconds=max(
            0.0,
            min(60.0, float(outbox.get("shutdown_drain_timeout_seconds", 5.0))),
        ),
    )


def build_outbox_health_report(
    outbox: "MemoryWriteOutbox",
    *,
    queue_name: str,
    session_id: str,
    memory_actor: str,
    memory_domain: str,
) -> dict[str, Any]:
    """Build the canonical health payload shared by all transport queues."""
    return {
        "session_id": str(session_id),
        "outbox_id": outbox.outbox_id,
        "queue_name": str(queue_name),
        "memory_actor": str(memory_actor),
        "memory_domain": str(memory_domain),
        **outbox.health_snapshot(),
    }


def _ensure_column(
    conn: sqlite3.Connection,
    columns: set[str],
    column: str,
    definition: str,
) -> None:
    if column in columns:
        return
    try:
        conn.execute(
            f"ALTER TABLE pending_writes ADD COLUMN {column} {definition}"
        )
    except sqlite3.OperationalError:
        refreshed = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(pending_writes)")
        }
        if column not in refreshed:
            raise
    columns.add(column)


class MemoryWriteOutbox:
    def __init__(
        self,
        path: str | Path,
        *,
        max_attempts: int = 12,
        lease_seconds: float = 30.0,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 60.0,
    ) -> None:
        self.path = Path(path)
        self.max_attempts = max(1, int(max_attempts))
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.retry_base_seconds = max(0.001, float(retry_base_seconds))
        self.retry_max_seconds = max(
            self.retry_base_seconds,
            float(retry_max_seconds),
        )
        self._worker_id = str(uuid.uuid4())
        self.outbox_id = hashlib.sha256(
            str(self.path.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:24]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS pending_writes ("
                    "write_id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                    "next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT, created_at REAL NOT NULL, "
                    "status TEXT NOT NULL DEFAULT 'pending', dead_letter_at REAL, "
                    "lease_owner TEXT, lease_until REAL, first_failed_at REAL)"
                )
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(pending_writes)")
                }
                _ensure_column(
                    conn,
                    columns,
                    "status",
                    "TEXT NOT NULL DEFAULT 'pending'",
                )
                _ensure_column(conn, columns, "dead_letter_at", "REAL")
                _ensure_column(conn, columns, "lease_owner", "TEXT")
                _ensure_column(conn, columns, "lease_until", "REAL")
                _ensure_column(conn, columns, "first_failed_at", "REAL")
                conn.execute("DROP INDEX IF EXISTS idx_pending_writes_due")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pending_writes_due_v2 "
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
            now = time.time()
            available = conn.execute(
                "SELECT 1 FROM pending_writes WHERE "
                "(status = 'pending' AND next_attempt_at <= ?) OR "
                "(status = 'inflight' AND lease_until <= ?) LIMIT 1",
                (now, now),
            ).fetchone()
            if not available:
                return None
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE pending_writes SET status = 'pending', lease_owner = NULL, "
                    "lease_until = NULL WHERE status = 'inflight' AND lease_until <= ?",
                    (now,),
                )
                row = conn.execute(
                    "SELECT write_id, payload, attempts FROM pending_writes "
                    "WHERE status = 'pending' AND next_attempt_at <= ? "
                    "ORDER BY created_at LIMIT 1",
                    (now,),
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE pending_writes SET status = 'inflight', lease_owner = ?, "
                        "lease_until = ? WHERE write_id = ? AND status = 'pending'",
                        (
                            self._worker_id,
                            now + self.lease_seconds,
                            str(row[0]),
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if not row:
            return None
        payload = json.loads(row[1])
        payload["_outbox_attempts"] = int(row[2])
        return payload

    def mark_delivered(self, write_id: str) -> None:
        with closing(self._connect()) as conn:
            with conn:
                deleted = conn.execute(
                    "DELETE FROM pending_writes WHERE write_id = ? AND "
                    "(status = 'pending' OR lease_owner = ?)",
                    (write_id, self._worker_id),
                )
                if deleted.rowcount:
                    conn.execute(
                        "INSERT INTO outbox_state(key, value) VALUES ('last_success_at', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (str(time.time()),),
                    )

    def mark_failed(self, write_id: str, *, attempts: int, error: str) -> None:
        now = time.time()
        delay = min(
            self.retry_max_seconds,
            self.retry_base_seconds * float(2 ** min(max(attempts - 1, 0), 6)),
        )
        status = "dead_letter" if attempts >= self.max_attempts else "pending"
        dead_letter_at = now if status == "dead_letter" else None
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE pending_writes SET attempts = ?, next_attempt_at = ?, last_error = ?, "
                    "status = ?, dead_letter_at = ?, lease_owner = NULL, lease_until = NULL, "
                    "first_failed_at = COALESCE(first_failed_at, ?) "
                    "WHERE write_id = ? AND (status = 'pending' OR lease_owner = ?)",
                    (
                        attempts,
                        now + delay,
                        error[:500],
                        status,
                        dead_letter_at,
                        now,
                        write_id,
                        self._worker_id,
                    ),
                )

    def defer(self, write_id: str, *, delay_seconds: float = 1.0) -> None:
        """Release a claimed item without counting a transport failure."""
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE pending_writes SET status = 'pending', next_attempt_at = ?, "
                    "lease_owner = NULL, lease_until = NULL WHERE write_id = ? "
                    "AND lease_owner = ?",
                    (
                        time.time() + max(0.001, float(delay_seconds)),
                        write_id,
                        self._worker_id,
                    ),
                )

    def has_blocking_writes_before(self, write_id: str) -> bool:
        """Return whether an older active write must precede this item.

        A dead letter is terminal for automatic delivery.  Keeping it in the
        ordering barrier would permanently strand every later write, so only
        pending and inflight rows remain blockers.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT rowid FROM pending_writes WHERE write_id = ?",
                (write_id,),
            ).fetchone()
            if not row:
                return False
            blocker = conn.execute(
                "SELECT 1 FROM pending_writes WHERE rowid < ? AND "
                "status IN ('pending', 'inflight') LIMIT 1",
                (int(row[0]),),
            ).fetchone()
            return blocker is not None

    def pending_count(self) -> int:
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM pending_writes "
                    "WHERE status IN ('pending', 'inflight')"
                ).fetchone()[0]
            )

    def drainable_count(self) -> int:
        """Return writes a bounded shutdown drain can make progress on now."""
        now = time.time()
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM pending_writes WHERE "
                    "(status = 'pending' AND next_attempt_at <= ?) OR "
                    "(status = 'inflight' AND (lease_owner = ? OR lease_until <= ?))",
                    (now, self._worker_id, now),
                ).fetchone()[0]
            )

    def health_snapshot(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            pending, oldest, last_error = conn.execute(
                "SELECT COUNT(*), MIN(created_at), "
                "(SELECT last_error FROM pending_writes "
                "WHERE status IN ('pending', 'inflight', 'dead_letter') "
                "ORDER BY created_at DESC LIMIT 1) "
                "FROM pending_writes WHERE status IN ('pending', 'inflight')"
            ).fetchone()
            inflight = conn.execute(
                "SELECT COUNT(*) FROM pending_writes WHERE status = 'inflight'"
            ).fetchone()[0]
            dead_letter = conn.execute(
                "SELECT COUNT(*) FROM pending_writes WHERE status = 'dead_letter'"
            ).fetchone()[0]
            oldest_failure = conn.execute(
                "SELECT MIN(first_failed_at) FROM pending_writes "
                "WHERE status IN ('pending', 'inflight', 'dead_letter') "
                "AND first_failed_at IS NOT NULL"
            ).fetchone()[0]
            state = conn.execute(
                "SELECT value FROM outbox_state WHERE key = 'last_success_at'"
            ).fetchone()
        return {
            "pending_count": int(pending or 0),
            "inflight_count": int(inflight or 0),
            "dead_letter_count": int(dead_letter or 0),
            "oldest_pending_at": (
                datetime.fromtimestamp(float(oldest), tz=timezone.utc).isoformat()
                if oldest is not None
                else None
            ),
            "oldest_failure_at": (
                datetime.fromtimestamp(
                    float(oldest_failure), tz=timezone.utc
                ).isoformat()
                if oldest_failure is not None
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
                    "UPDATE pending_writes SET status = 'pending', attempts = 0, "
                    "next_attempt_at = 0, last_error = NULL, dead_letter_at = NULL, "
                    "lease_owner = NULL, lease_until = NULL, first_failed_at = NULL "
                    "WHERE write_id = ? AND status = 'dead_letter'",
                    (write_id,),
                )
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
