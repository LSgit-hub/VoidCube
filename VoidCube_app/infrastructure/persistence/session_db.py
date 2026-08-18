#!/usr/bin/env python3
"""
SQLite State Store for Voidcube Agent.

Provides persistent session storage with FTS5 full-text search, replacing
the per-session JSONL file approach. Stores session metadata, full message
history, and model configuration for CLI and gateway sessions.

Key design decisions:
- WAL mode for concurrent readers + one writer (gateway multi-platform)
- FTS5 virtual table for fast text search across all session messages
- Compression-triggered session splitting via parent_session_id chains
- Batch runner and RL trajectories are NOT stored here (separate systems)
- Session source tagging for filtering
"""

import json
import hashlib
import logging
import random
import re
import sqlite3
import threading
import time
from pathlib import Path
from .constants import get_VoidCube_home
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SessionSequenceConflictError(RuntimeError):
    """A transcript writer used a stale committed sequence cursor."""


DEFAULT_DB_PATH = get_VoidCube_home() / "state.db"

SCHEMA_VERSION = 10

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    flush_sequence INTEGER DEFAULT 0,
    transcript_revision INTEGER NOT NULL DEFAULT 0,
    transcript_hash TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_details TEXT,
    action_refs TEXT,
    message_id TEXT,
    sequence_no INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

CREATE TABLE IF NOT EXISTS session_goals (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_goals_status ON session_goals(status);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class SessionDB:
    """
    SQLite-backed session storage with FTS5 search.

    Thread-safe for the common gateway pattern (multiple reader threads,
    single writer via WAL mode). Each method opens its own cursor.
    """

    # ── Write-contention tuning ──
    # With multiple VoidCube processes (gateway + CLI sessions + worktree agents)
    # all sharing one state.db, WAL write-lock contention causes visible TUI
    # freezes.  SQLite's built-in busy handler uses a deterministic sleep
    # schedule that causes convoy effects under high concurrency.
    #
    # Instead, we keep the SQLite timeout short (1s) and handle retries at the
    # application level with random jitter, which naturally staggers competing
    # writers and avoids the convoy.
    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020   # 20ms
    _WRITE_RETRY_MAX_S = 0.150   # 150ms
    # Attempt a PASSIVE WAL checkpoint every N successful writes.
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._write_count = 0
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            # Short timeout — application-level retry with random jitter
            # handles contention instead of sitting in SQLite's internal
            # busy handler for up to 30s.
            timeout=1.0,
            # Autocommit mode: Python's default isolation_level="" auto-starts
            # transactions on DML, which conflicts with our explicit
            # BEGIN IMMEDIATE.  None = we manage transactions ourselves.
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._init_schema()

    # ── Core write helper ──

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a write transaction with BEGIN IMMEDIATE and jitter retry.

        *fn* receives the connection and should perform INSERT/UPDATE/DELETE
        statements.  The caller must NOT call ``commit()`` — that's handled
        here after *fn* returns.

        BEGIN IMMEDIATE acquires the WAL write lock at transaction start
        (not at commit time), so lock contention surfaces immediately.
        On ``database is locked``, we release the Python lock, sleep a
        random 20-150ms, and retry — breaking the convoy pattern that
        SQLite's built-in deterministic backoff creates.

        Returns whatever *fn* returns.
        """
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.commit()
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
                # Success — periodic best-effort checkpoint.
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                err_msg = str(exc).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    last_err = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        jitter = random.uniform(
                            self._WRITE_RETRY_MIN_S,
                            self._WRITE_RETRY_MAX_S,
                        )
                        time.sleep(jitter)
                        continue
                # Non-lock error or retries exhausted — propagate.
                raise
        # Retries exhausted (shouldn't normally reach here).
        raise last_err or sqlite3.OperationalError(
            "database is locked after max retries"
        )

    def _try_wal_checkpoint(self) -> None:
        """Best-effort PASSIVE WAL checkpoint.  Never blocks, never raises.

        Flushes committed WAL frames back into the main DB file for any
        frames that no other connection currently needs.  Keeps the WAL
        from growing unbounded when many processes hold persistent
        connections.
        """
        try:
            with self._lock:
                result = self._conn.execute(
                    "PRAGMA wal_checkpoint(PASSIVE)"
                ).fetchone()
                if result and result[1] > 0:
                    logger.debug(
                        "WAL checkpoint: %d/%d pages checkpointed",
                        result[2], result[1],
                    )
        except Exception:
            pass  # Best effort — never fatal.

    def close(self):
        """Close the database connection.

        Attempts a PASSIVE WAL checkpoint first so that exiting processes
        help keep the WAL file from growing unbounded.
        """
        with self._lock:
            if self._conn:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass
                self._conn.close()
                self._conn = None

    def _init_schema(self):
        """Create tables and FTS if they don't exist, run migrations."""
        cursor = self._conn.cursor()

        cursor.executescript(SCHEMA_SQL)

        # Check schema version and run migrations
        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            current_version = row["version"] if isinstance(row, sqlite3.Row) else row[0]
            if current_version < 2:
                # v2: add finish_reason column to messages
                try:
                    cursor.execute("ALTER TABLE messages ADD COLUMN finish_reason TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 2")
            if current_version < 3:
                # v3: add title column to sessions
                try:
                    cursor.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 3")
            if current_version < 4:
                # v4: add unique index on title (NULLs allowed, only non-NULL must be unique)
                try:
                    cursor.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique "
                        "ON sessions(title) WHERE title IS NOT NULL"
                    )
                except sqlite3.OperationalError:
                    pass  # Index already exists
                cursor.execute("UPDATE schema_version SET version = 4")
            if current_version < 5:
                new_columns = [
                    ("cache_read_tokens", "INTEGER DEFAULT 0"),
                    ("cache_write_tokens", "INTEGER DEFAULT 0"),
                    ("reasoning_tokens", "INTEGER DEFAULT 0"),
                    ("billing_provider", "TEXT"),
                    ("billing_base_url", "TEXT"),
                    ("billing_mode", "TEXT"),
                    ("estimated_cost_usd", "REAL"),
                    ("actual_cost_usd", "REAL"),
                    ("cost_status", "TEXT"),
                    ("cost_source", "TEXT"),
                    ("pricing_version", "TEXT"),
                ]
                for name, column_type in new_columns:
                    try:
                        # name and column_type come from the hardcoded tuple above,
                        # not user input. Double-quote identifier escaping is applied
                        # as defense-in-depth; SQLite DDL cannot be parameterized.
                        safe_name = name.replace('"', '""')
                        cursor.execute(f'ALTER TABLE sessions ADD COLUMN "{safe_name}" {column_type}')
                    except sqlite3.OperationalError:
                        pass
                cursor.execute("UPDATE schema_version SET version = 5")
            if current_version < 6:
                # v6: add reasoning columns to messages table — preserves assistant
                # reasoning text and structured reasoning_details across gateway
                # session turns.  Without these, reasoning chains are lost on
                # session reload, breaking multi-turn reasoning continuity for
                # providers that replay reasoning (OpenRouter, OpenAI, Nous).
                for col_name, col_type in [
                    ("reasoning", "TEXT"),
                    ("reasoning_details", "TEXT"),
                ]:
                    try:
                        safe = col_name.replace('"', '""')
                        cursor.execute(
                            f'ALTER TABLE messages ADD COLUMN "{safe}" {col_type}'
                        )
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 6")
            if current_version < 7:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS session_goals (
                        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                        objective TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        reason TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_session_goals_status "
                    "ON session_goals(status)"
                )
                cursor.execute("UPDATE schema_version SET version = 7")
            if current_version < 8:
                for table, name, column_type in (
                    ("sessions", "flush_sequence", "INTEGER DEFAULT 0"),
                    ("messages", "message_id", "TEXT"),
                    ("messages", "sequence_no", "INTEGER"),
                ):
                    try:
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN "{name}" {column_type}'
                        )
                    except sqlite3.OperationalError:
                        pass
                session_rows = cursor.execute(
                    "SELECT DISTINCT session_id FROM messages"
                ).fetchall()
                for session_row in session_rows:
                    session_id = session_row[0]
                    message_rows = cursor.execute(
                        "SELECT id FROM messages WHERE session_id = ? ORDER BY id",
                        (session_id,),
                    ).fetchall()
                    for sequence_no, message_row in enumerate(message_rows, 1):
                        row_id = message_row[0]
                        cursor.execute(
                            "UPDATE messages SET message_id = ?, sequence_no = ? WHERE id = ?",
                            (f"legacy:{session_id}:{row_id}", sequence_no, row_id),
                        )
                    cursor.execute(
                        "UPDATE sessions SET flush_sequence = ? WHERE id = ?",
                        (len(message_rows), session_id),
                    )
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_message_id "
                    "ON messages(message_id)"
                )
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_sequence "
                    "ON messages(session_id, sequence_no)"
                )
                cursor.execute("UPDATE schema_version SET version = 8")
            if current_version < 9:
                try:
                    cursor.execute("ALTER TABLE messages ADD COLUMN action_refs TEXT")
                except sqlite3.OperationalError:
                    pass
                cursor.execute("UPDATE schema_version SET version = 9")
            if current_version < 10:
                for name, column_type in (
                    ("transcript_revision", "INTEGER NOT NULL DEFAULT 0"),
                    ("transcript_hash", "TEXT NOT NULL DEFAULT ''"),
                ):
                    try:
                        cursor.execute(
                            f'ALTER TABLE sessions ADD COLUMN "{name}" {column_type}'
                        )
                    except sqlite3.OperationalError:
                        pass
                session_rows = cursor.execute("SELECT id FROM sessions").fetchall()
                for session_row in session_rows:
                    session_id = str(session_row[0])
                    message_count = int(
                        cursor.execute(
                            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                            (session_id,),
                        ).fetchone()[0]
                    )
                    cursor.execute(
                        "UPDATE sessions SET transcript_revision = ?, "
                        "transcript_hash = ? WHERE id = ?",
                        (
                            1 if message_count else 0,
                            self._transcript_hash_from_connection(cursor, session_id),
                            session_id,
                        ),
                    )
                cursor.execute("UPDATE schema_version SET version = 10")

        # Unique title index — always ensure it exists (safe to run after migrations
        # since the title column is guaranteed to exist at this point)
        try:
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique "
                "ON sessions(title) WHERE title IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass  # Index already exists
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_message_id "
            "ON messages(message_id)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_sequence "
            "ON messages(session_id, sequence_no)"
        )

        # FTS5 setup (separate because CREATE VIRTUAL TABLE can't be in executescript with IF NOT EXISTS reliably)
        try:
            cursor.execute("SELECT * FROM messages_fts LIMIT 0")
        except sqlite3.OperationalError:
            cursor.executescript(FTS_SQL)

        self._conn.commit()

    # =========================================================================
    # Session lifecycle
    # =========================================================================

    def create_session(
        self,
        session_id: str,
        source: str,
        model: str = None,
        model_config: Dict[str, Any] = None,
        system_prompt: str = None,
        user_id: str = None,
        parent_session_id: str = None,
    ) -> str:
        """Create a new session record. Returns the session_id."""
        def _do(conn):
            conn.execute(
                """INSERT OR IGNORE INTO sessions (id, source, user_id, model, model_config,
                   system_prompt, parent_session_id, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    source,
                    user_id,
                    model,
                    json.dumps(model_config, ensure_ascii=False) if model_config else None,
                    system_prompt,
                    parent_session_id,
                    time.time(),
                ),
            )
        self._execute_write(_do)
        return session_id

    def end_session(self, session_id: str, end_reason: str) -> None:
        """Mark a session as ended."""
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                (time.time(), end_reason, session_id),
            )
        self._execute_write(_do)

    def reopen_session(self, session_id: str) -> None:
        """Clear ended_at/end_reason so a session can be resumed."""
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET ended_at = NULL, end_reason = NULL WHERE id = ?",
                (session_id,),
            )
        self._execute_write(_do)

    def update_system_prompt(self, session_id: str, system_prompt: str) -> None:
        """Store the full assembled system prompt snapshot."""
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                (system_prompt, session_id),
            )
        self._execute_write(_do)

    def update_token_counts(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        estimated_cost_usd: Optional[float] = None,
        actual_cost_usd: Optional[float] = None,
        cost_status: Optional[str] = None,
        cost_source: Optional[str] = None,
        pricing_version: Optional[str] = None,
        billing_provider: Optional[str] = None,
        billing_base_url: Optional[str] = None,
        billing_mode: Optional[str] = None,
        absolute: bool = False,
    ) -> None:
        """Update token counters and backfill model if not already set.

        When *absolute* is False (default), values are **incremented** — use
        this for per-API-call deltas (CLI path).

        When *absolute* is True, values are **set directly** — use this when
        the caller already holds cumulative totals (gateway path, where the
        cached agent accumulates across messages).
        """
        if absolute:
            sql = """UPDATE sessions SET
                   input_tokens = ?,
                   output_tokens = ?,
                   cache_read_tokens = ?,
                   cache_write_tokens = ?,
                   reasoning_tokens = ?,
                   estimated_cost_usd = COALESCE(?, 0),
                   actual_cost_usd = CASE
                       WHEN ? IS NULL THEN actual_cost_usd
                       ELSE ?
                   END,
                   cost_status = COALESCE(?, cost_status),
                   cost_source = COALESCE(?, cost_source),
                   pricing_version = COALESCE(?, pricing_version),
                   billing_provider = COALESCE(billing_provider, ?),
                   billing_base_url = COALESCE(billing_base_url, ?),
                   billing_mode = COALESCE(billing_mode, ?),
                   model = COALESCE(model, ?)
                   WHERE id = ?"""
        else:
            sql = """UPDATE sessions SET
                   input_tokens = input_tokens + ?,
                   output_tokens = output_tokens + ?,
                   cache_read_tokens = cache_read_tokens + ?,
                   cache_write_tokens = cache_write_tokens + ?,
                   reasoning_tokens = reasoning_tokens + ?,
                   estimated_cost_usd = COALESCE(estimated_cost_usd, 0) + COALESCE(?, 0),
                   actual_cost_usd = CASE
                       WHEN ? IS NULL THEN actual_cost_usd
                       ELSE COALESCE(actual_cost_usd, 0) + ?
                   END,
                   cost_status = COALESCE(?, cost_status),
                   cost_source = COALESCE(?, cost_source),
                   pricing_version = COALESCE(?, pricing_version),
                   billing_provider = COALESCE(billing_provider, ?),
                   billing_base_url = COALESCE(billing_base_url, ?),
                   billing_mode = COALESCE(billing_mode, ?),
                   model = COALESCE(model, ?)
                   WHERE id = ?"""
        params = (
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            reasoning_tokens,
            estimated_cost_usd,
            actual_cost_usd,
            actual_cost_usd,
            cost_status,
            cost_source,
            pricing_version,
            billing_provider,
            billing_base_url,
            billing_mode,
            model,
            session_id,
        )
        def _do(conn):
            conn.execute(sql, params)
        self._execute_write(_do)

    def ensure_session(
        self,
        session_id: str,
        source: str = "unknown",
        model: str = None,
    ) -> None:
        """Ensure a session row exists, creating it with minimal metadata if absent.

        Used by _flush_messages_to_session_db to recover from a failed
        create_session() call (e.g. transient SQLite lock at agent startup).
        INSERT OR IGNORE is safe to call even when the row already exists.
        """
        def _do(conn):
            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (id, source, model, started_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, source, model, time.time()),
            )
        self._execute_write(_do)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a session by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def get_flush_sequence(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT flush_sequence FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0] or 0) if row else 0

    @staticmethod
    def transcript_hash(messages: List[Dict[str, Any]]) -> str:
        """Return a stable hash for the semantic transcript fields."""
        fields = (
            "role",
            "content",
            "tool_call_id",
            "tool_calls",
            "tool_name",
            "finish_reason",
            "reasoning",
            "reasoning_details",
            "action_refs",
        )
        normalized = [
            {field: message.get(field) for field in fields}
            for message in messages
        ]
        canonical = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _transcript_hash_from_connection(
        cls,
        conn: sqlite3.Connection,
        session_id: str,
    ) -> str:
        rows = conn.execute(
            "SELECT role, content, tool_call_id, tool_calls, tool_name, "
            "finish_reason, reasoning, reasoning_details, action_refs "
            "FROM messages WHERE session_id = ? ORDER BY sequence_no",
            (session_id,),
        ).fetchall()
        messages: List[Dict[str, Any]] = []
        json_fields = {"tool_calls", "reasoning_details", "action_refs"}
        for row in rows:
            message = dict(row)
            for field in json_fields:
                value = message.get(field)
                if value is not None:
                    try:
                        message[field] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        pass
            messages.append(message)
        return cls.transcript_hash(messages)

    def get_transcript_snapshot(self, session_id: str) -> Dict[str, Any]:
        """Read transcript metadata and messages from one SQLite snapshot."""
        with self._lock:
            session = self._conn.execute(
                "SELECT flush_sequence, transcript_revision, transcript_hash "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"Unknown session: {session_id}")
            messages = self._get_messages_as_conversation(self._conn, session_id)
        return {
            "flush_sequence": int(session["flush_sequence"] or 0),
            "transcript_revision": int(session["transcript_revision"] or 0),
            "transcript_hash": str(session["transcript_hash"] or "")
            or self.transcript_hash(messages),
            "messages": messages,
        }

    def get_session_goal(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return the one goal owned by a session, if present."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, objective, status, reason, created_at, updated_at "
                "FROM session_goals WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_session_goal(self, session_id: str, objective: str) -> Dict[str, Any]:
        """Create an active goal for a session that does not already have one."""
        now = time.time()

        def _do(conn):
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                (session_id, "cli", now),
            )
            conn.execute(
                "INSERT INTO session_goals "
                "(session_id, objective, status, reason, created_at, updated_at) "
                "VALUES (?, ?, 'active', NULL, ?, ?)",
                (session_id, objective, now, now),
            )

        self._execute_write(_do)
        return self.get_session_goal(session_id) or {}

    def update_session_goal(
        self,
        session_id: str,
        status: str,
        reason: str | None = None,
    ) -> bool:
        """Update a goal terminal status and optional explanation."""
        if status not in {"completed", "blocked"}:
            raise ValueError(f"Unsupported goal status: {status}")
        now = time.time()

        def _do(conn):
            cursor = conn.execute(
                "UPDATE session_goals SET status = ?, reason = ?, updated_at = ? "
                "WHERE session_id = ? AND status = 'active'",
                (status, reason, now, session_id),
            )
            return cursor.rowcount

        return bool(self._execute_write(_do))

    def clear_session_goal(self, session_id: str) -> bool:
        """Delete a non-active goal from a session."""
        def _do(conn):
            cursor = conn.execute(
                "DELETE FROM session_goals WHERE session_id = ? AND status != 'active'",
                (session_id,),
            )
            return cursor.rowcount

        return bool(self._execute_write(_do))

    def resolve_session_id(self, session_id_or_prefix: str) -> Optional[str]:
        """Resolve an exact or uniquely prefixed session ID to the full ID.

        Returns the exact ID when it exists. Otherwise treats the input as a
        prefix and returns the single matching session ID if the prefix is
        unambiguous. Returns None for no matches or ambiguous prefixes.
        """
        exact = self.get_session(session_id_or_prefix)
        if exact:
            return exact["id"]

        escaped = (
            session_id_or_prefix
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id FROM sessions WHERE id LIKE ? ESCAPE '\\' ORDER BY started_at DESC LIMIT 2",
                (f"{escaped}%",),
            )
            matches = [row["id"] for row in cursor.fetchall()]
        if len(matches) == 1:
            return matches[0]
        return None

    def resolve_resumable_session_id(self, session_id: str) -> str:
        """Resolve a logical session to its latest non-empty compression segment."""
        with self._lock:
            row = self._conn.execute(
                """
                WITH RECURSIVE compression_lineage(id, end_reason) AS (
                    SELECT id, end_reason FROM sessions WHERE id = ?
                    UNION ALL
                    SELECT child.id, child.end_reason
                    FROM sessions child
                    JOIN compression_lineage parent
                      ON child.parent_session_id = parent.id
                    WHERE parent.end_reason = 'compression'
                )
                SELECT segment.id
                FROM sessions segment
                JOIN compression_lineage lineage ON lineage.id = segment.id
                WHERE EXISTS (
                    SELECT 1 FROM messages WHERE session_id = segment.id
                )
                ORDER BY COALESCE(
                    (SELECT MAX(timestamp) FROM messages WHERE session_id = segment.id),
                    segment.started_at
                ) DESC, segment.started_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return str(row["id"]) if row else session_id

    # Maximum length for session titles
    MAX_TITLE_LENGTH = 100

    @staticmethod
    def sanitize_title(title: Optional[str]) -> Optional[str]:
        """Validate and sanitize a session title.

        - Strips leading/trailing whitespace
        - Removes ASCII control characters (0x00-0x1F, 0x7F) and problematic
          Unicode control chars (zero-width, RTL/LTR overrides, etc.)
        - Collapses internal whitespace runs to single spaces
        - Normalizes empty/whitespace-only strings to None
        - Enforces MAX_TITLE_LENGTH

        Returns the cleaned title string or None.
        Raises ValueError if the title exceeds MAX_TITLE_LENGTH after cleaning.
        """
        if not title:
            return None

        # Remove ASCII control characters (0x00-0x1F, 0x7F) but keep
        # whitespace chars (\t=0x09, \n=0x0A, \r=0x0D) so they can be
        # normalized to spaces by the whitespace collapsing step below
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', title)

        # Remove problematic Unicode control characters:
        # - Zero-width chars (U+200B-U+200F, U+FEFF)
        # - Directional overrides (U+202A-U+202E, U+2066-U+2069)
        # - Object replacement (U+FFFC), interlinear annotation (U+FFF9-U+FFFB)
        cleaned = re.sub(
            r'[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]',
            '', cleaned,
        )

        # Collapse internal whitespace runs and strip
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        if not cleaned:
            return None

        if len(cleaned) > SessionDB.MAX_TITLE_LENGTH:
            raise ValueError(
                f"Title too long ({len(cleaned)} chars, max {SessionDB.MAX_TITLE_LENGTH})"
            )

        return cleaned

    def set_session_title(self, session_id: str, title: str) -> bool:
        """Set or update a session's title.

        Returns True if session was found and title was set.
        Raises ValueError if title is already in use by another session,
        or if the title fails validation (too long, invalid characters).
        Empty/whitespace-only strings are normalized to None (clearing the title).
        """
        title = self.sanitize_title(title)
        def _do(conn):
            if title:
                # Check uniqueness (allow the same session to keep its own title)
                cursor = conn.execute(
                    "SELECT id FROM sessions WHERE title = ? AND id != ?",
                    (title, session_id),
                )
                conflict = cursor.fetchone()
                if conflict:
                    raise ValueError(
                        f"Title '{title}' is already in use by session {conflict['id']}"
                    )
            cursor = conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            return cursor.rowcount
        rowcount = self._execute_write(_do)
        return rowcount > 0

    def get_session_title(self, session_id: str) -> Optional[str]:
        """Get the title for a session, or None."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            )
            row = cursor.fetchone()
        return row["title"] if row else None

    def get_session_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Look up a session by exact title. Returns session dict or None."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM sessions WHERE title = ?", (title,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def resolve_session_by_title(self, title: str) -> Optional[str]:
        """Resolve a title to a session ID, preferring the latest in a lineage.

        If the exact title exists, returns that session's ID.
        If not, searches for "title #N" variants and returns the latest one.
        If the exact title exists AND numbered variants exist, returns the
        latest numbered variant (the most recent continuation).
        """
        # First try exact match
        exact = self.get_session_by_title(title)

        # Also search for numbered variants: "title #2", "title #3", etc.
        # Escape SQL LIKE wildcards (%, _) in the title to prevent false matches
        escaped = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, title, started_at FROM sessions "
                "WHERE title LIKE ? ESCAPE '\\' ORDER BY started_at DESC",
                (f"{escaped} #%",),
            )
            numbered = cursor.fetchall()

        if numbered:
            # Return the most recent numbered variant
            return numbered[0]["id"]
        elif exact:
            return exact["id"]
        return None

    def get_next_title_in_lineage(self, base_title: str) -> str:
        """Generate the next title in a lineage (e.g., "my session" → "my session #2").

        Strips any existing " #N" suffix to find the base name, then finds
        the highest existing number and increments.
        """
        # Strip existing #N suffix to find the true base
        match = re.match(r'^(.*?) #(\d+)$', base_title)
        if match:
            base = match.group(1)
        else:
            base = base_title

        # Find all existing numbered variants
        # Escape SQL LIKE wildcards (%, _) in the base to prevent false matches
        escaped = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            cursor = self._conn.execute(
                "SELECT title FROM sessions WHERE title = ? OR title LIKE ? ESCAPE '\\'",
                (base, f"{escaped} #%"),
            )
            existing = [row["title"] for row in cursor.fetchall()]

        if not existing:
            return base  # No conflict, use the base name as-is

        # Find the highest number
        max_num = 1  # The unnumbered original counts as #1
        for t in existing:
            m = re.match(r'^.* #(\d+)$', t)
            if m:
                max_num = max(max_num, int(m.group(1)))

        return f"{base} #{max_num + 1}"

    def list_sessions_rich(
        self,
        source: str = None,
        exclude_sources: List[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_children: bool = False,
        exclude_id_prefixes: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """List sessions with preview (first user message) and last active timestamp.

        Returns dicts with keys: id, source, model, title, started_at, ended_at,
        message_count, preview (first 60 chars of first user message),
        last_active (timestamp of last message).

        Uses a single query with correlated subqueries instead of N+2 queries.

        By default, child sessions (subagent runs, compression continuations)
        are excluded.  Pass ``include_children=True`` to include them.
        """
        where_clauses = []
        params = []

        if not include_children:
            where_clauses.append("s.parent_session_id IS NULL")

        if source:
            where_clauses.append("s.source = ?")
            params.append(source)
        if exclude_sources:
            placeholders = ",".join("?" for _ in exclude_sources)
            where_clauses.append(f"s.source NOT IN ({placeholders})")
            params.extend(exclude_sources)
        for prefix in exclude_id_prefixes or []:
            normalized = str(prefix or "")
            if not normalized:
                continue
            escaped = (
                normalized.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            where_clauses.append("s.id NOT LIKE ? ESCAPE '\\'")
            params.append(f"{escaped}%")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT s.*,
                COALESCE(
                    (SELECT SUBSTR(REPLACE(REPLACE(m.content, X'0A', ' '), X'0D', ' '), 1, 63)
                     FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'user' AND m.content IS NOT NULL
                     ORDER BY m.timestamp, m.id LIMIT 1),
                    ''
                ) AS _preview_raw,
                COALESCE(
                    (SELECT MAX(m2.timestamp) FROM messages m2 WHERE m2.session_id = s.id),
                    s.started_at
                ) AS last_active
            FROM sessions s
            {where_sql}
            ORDER BY last_active DESC, s.started_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        sessions = []
        for row in rows:
            s = dict(row)
            # Build the preview from the raw substring
            raw = s.pop("_preview_raw", "").strip()
            if raw:
                text = raw[:60]
                s["preview"] = text + ("..." if len(raw) > 60 else "")
            else:
                s["preview"] = ""
            sessions.append(s)

        return sessions

    # =========================================================================
    # Message storage
    # =========================================================================

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str = None,
        tool_name: str = None,
        tool_calls: Any = None,
        tool_call_id: str = None,
        token_count: int = None,
        finish_reason: str = None,
        reasoning: str = None,
        reasoning_details: Any = None,
        action_refs: Any = None,
    ) -> int:
        """Append one message through the transactional batch contract."""
        message = {
            "role": role,
            "content": content,
            "tool_name": tool_name,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
            "token_count": token_count,
            "finish_reason": finish_reason,
            "reasoning": reasoning,
            "reasoning_details": reasoning_details,
            "action_refs": action_refs,
        }
        row_ids = self.append_messages_batch(
            session_id,
            [message],
            allocate_sequences=True,
        )
        return row_ids[0]

    @staticmethod
    def stable_message_id(
        session_id: str,
        sequence_no: int,
        message: Dict[str, Any],
    ) -> str:
        canonical = json.dumps(
            message,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"msg:{session_id}:{sequence_no}:{digest}"

    def append_messages_batch(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        expected_flush_sequence: int | None = None,
        expected_revision: int | None = None,
        expected_prefix_hash: str | None = None,
        allocate_sequences: bool = False,
    ) -> List[int]:
        """Atomically append an idempotent ordered message batch."""
        batch = [dict(message) for message in messages]
        if not batch:
            return []
        if not allocate_sequences:
            for message in batch:
                if not message.get("message_id") or not message.get("sequence_no"):
                    raise ValueError(
                        "Batch messages require stable message_id and sequence_no"
                    )

        def _do(conn):
            session = conn.execute(
                "SELECT id, flush_sequence, transcript_revision, transcript_hash "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"Unknown session: {session_id}")
            current_sequence = int(session["flush_sequence"] or 0)
            current_revision = int(session["transcript_revision"] or 0)
            current_hash = str(session["transcript_hash"] or "")
            if not current_hash and current_sequence == 0:
                current_hash = self.transcript_hash([])
            if (
                expected_flush_sequence is not None
                and current_sequence != int(expected_flush_sequence)
            ):
                raise SessionSequenceConflictError(
                    f"stale session cursor for {session_id}: "
                    f"expected {expected_flush_sequence}, current {current_sequence}"
                )
            if expected_revision is not None and current_revision != int(
                expected_revision
            ):
                raise SessionSequenceConflictError(
                    f"stale transcript revision for {session_id}: "
                    f"expected {expected_revision}, current {current_revision}"
                )
            if (
                expected_prefix_hash is not None
                and current_hash != expected_prefix_hash
            ):
                raise SessionSequenceConflictError(
                    f"transcript prefix mismatch for {session_id}"
                )
            if allocate_sequences:
                for offset, message in enumerate(batch, 1):
                    sequence_no = current_sequence + offset
                    message["sequence_no"] = sequence_no
                    message["message_id"] = self.stable_message_id(
                        session_id, sequence_no, message
                    )
            elif expected_flush_sequence is not None:
                expected_numbers = list(
                    range(current_sequence + 1, current_sequence + len(batch) + 1)
                )
                actual_numbers = [int(item["sequence_no"]) for item in batch]
                if actual_numbers != expected_numbers:
                    raise SessionSequenceConflictError(
                        "Batch sequence numbers are not contiguous from the committed cursor"
                    )

            row_ids: List[int] = []
            inserted = False
            for message in batch:
                sequence_no = int(message["sequence_no"])
                message_id = str(message["message_id"])
                existing = conn.execute(
                    "SELECT id, session_id, sequence_no FROM messages "
                    "WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["session_id"]) != session_id
                        or int(existing["sequence_no"]) != sequence_no
                    ):
                        raise SessionSequenceConflictError(
                            f"message_id {message_id} is bound to another sequence"
                        )
                    row_ids.append(int(existing["id"]))
                    continue
                occupied = conn.execute(
                    "SELECT message_id FROM messages "
                    "WHERE session_id = ? AND sequence_no = ?",
                    (session_id, sequence_no),
                ).fetchone()
                if occupied is not None:
                    raise SessionSequenceConflictError(
                        f"sequence {sequence_no} for {session_id} is already committed"
                    )
                tool_calls = message.get("tool_calls")
                tool_calls_json = (
                    json.dumps(tool_calls, ensure_ascii=False)
                    if tool_calls is not None
                    else None
                )
                reasoning_details = message.get("reasoning_details")
                reasoning_details_json = (
                    json.dumps(reasoning_details, ensure_ascii=False)
                    if reasoning_details is not None
                    else None
                )
                action_refs = message.get("action_refs")
                action_refs_json = (
                    json.dumps(action_refs, ensure_ascii=False)
                    if action_refs is not None
                    else None
                )
                cursor = conn.execute(
                    """INSERT INTO messages (
                       message_id, session_id, sequence_no, role, content,
                       tool_call_id, tool_calls, tool_name, timestamp, token_count,
                       finish_reason, reasoning, reasoning_details, action_refs
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        message_id,
                        session_id,
                        sequence_no,
                        str(message.get("role") or "unknown"),
                        message.get("content"),
                        message.get("tool_call_id"),
                        tool_calls_json,
                        message.get("tool_name"),
                        float(message.get("timestamp") or time.time()),
                        message.get("token_count"),
                        message.get("finish_reason"),
                        message.get("reasoning"),
                        reasoning_details_json,
                        action_refs_json,
                    ),
                )
                row_ids.append(int(cursor.lastrowid))
                inserted = True

            counts = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(sequence_no), 0) "
                "FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            tool_rows = conn.execute(
                "SELECT tool_calls FROM messages WHERE session_id = ? "
                "AND tool_calls IS NOT NULL",
                (session_id,),
            ).fetchall()
            tool_call_count = 0
            for row in tool_rows:
                try:
                    calls = json.loads(row[0])
                    tool_call_count += len(calls) if isinstance(calls, list) else 1
                except (json.JSONDecodeError, TypeError):
                    tool_call_count += 1
            transcript_hash = self._transcript_hash_from_connection(conn, session_id)
            conn.execute(
                "UPDATE sessions SET message_count = ?, tool_call_count = ?, "
                "flush_sequence = ?, transcript_revision = ?, transcript_hash = ? "
                "WHERE id = ?",
                (
                    int(counts[0]),
                    tool_call_count,
                    int(counts[1]),
                    current_revision + (1 if inserted else 0),
                    transcript_hash,
                    session_id,
                ),
            )
            return row_ids

        return self._execute_write(_do)

    def replace_messages(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        expected_revision: int,
        expected_transcript_hash: str,
    ) -> List[int]:
        """Atomically replace a transcript after an explicit history rewrite."""
        replacement = [dict(message) for message in messages]

        def _do(conn):
            session = conn.execute(
                "SELECT transcript_revision, transcript_hash FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"Unknown session: {session_id}")
            current_revision = int(session["transcript_revision"] or 0)
            current_hash = str(session["transcript_hash"] or "")
            if not current_hash:
                current_hash = self._transcript_hash_from_connection(conn, session_id)
            if current_revision != int(expected_revision):
                raise SessionSequenceConflictError(
                    f"stale transcript revision for {session_id}: "
                    f"expected {expected_revision}, current {current_revision}"
                )
            if current_hash != str(expected_transcript_hash):
                raise SessionSequenceConflictError(
                    f"transcript replacement base mismatch for {session_id}"
                )

            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            row_ids: List[int] = []
            tool_call_count = 0
            for sequence_no, message in enumerate(replacement, 1):
                message["sequence_no"] = sequence_no
                message["message_id"] = self.stable_message_id(
                    session_id,
                    sequence_no,
                    message,
                )
                tool_calls = message.get("tool_calls")
                if tool_calls is not None:
                    tool_call_count += len(tool_calls) if isinstance(tool_calls, list) else 1
                tool_calls_json = (
                    json.dumps(tool_calls, ensure_ascii=False)
                    if tool_calls is not None
                    else None
                )
                reasoning_details = message.get("reasoning_details")
                reasoning_details_json = (
                    json.dumps(reasoning_details, ensure_ascii=False)
                    if reasoning_details is not None
                    else None
                )
                action_refs = message.get("action_refs")
                action_refs_json = (
                    json.dumps(action_refs, ensure_ascii=False)
                    if action_refs is not None
                    else None
                )
                cursor = conn.execute(
                    """INSERT INTO messages (
                       message_id, session_id, sequence_no, role, content,
                       tool_call_id, tool_calls, tool_name, timestamp, token_count,
                       finish_reason, reasoning, reasoning_details, action_refs
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        message["message_id"],
                        session_id,
                        sequence_no,
                        str(message.get("role") or "unknown"),
                        message.get("content"),
                        message.get("tool_call_id"),
                        tool_calls_json,
                        message.get("tool_name"),
                        float(message.get("timestamp") or time.time()),
                        message.get("token_count"),
                        message.get("finish_reason"),
                        message.get("reasoning"),
                        reasoning_details_json,
                        action_refs_json,
                    ),
                )
                row_ids.append(int(cursor.lastrowid))

            conn.execute(
                "UPDATE sessions SET message_count = ?, tool_call_count = ?, "
                "flush_sequence = ?, transcript_revision = ?, transcript_hash = ? "
                "WHERE id = ?",
                (
                    len(replacement),
                    tool_call_count,
                    len(replacement),
                    current_revision + 1,
                    self.transcript_hash(replacement),
                    session_id,
                ),
            )
            return row_ids

        return self._execute_write(_do)

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Load all messages for a session, ordered by timestamp."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, session_id, role, content, tool_call_id, tool_calls, "
                "tool_name, timestamp, token_count, finish_reason, reasoning, reasoning_details, "
                "action_refs, message_id, sequence_no FROM messages WHERE session_id = ? "
                "ORDER BY sequence_no",
                (session_id,),
            )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            msg = dict(row)
            if msg.get("tool_calls"):
                try:
                    msg["tool_calls"] = json.loads(msg["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to deserialize tool_calls in get_messages, falling back to []")
                    msg["tool_calls"] = []
            if msg.get("action_refs"):
                try:
                    msg["action_refs"] = json.loads(msg["action_refs"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to deserialize action_refs in get_messages")
                    msg["action_refs"] = []
            result.append(msg)
        return result

    def get_messages_as_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Load messages in the OpenAI conversation format (role + content dicts).
        Used by the gateway to restore conversation history.
        """
        with self._lock:
            return self._get_messages_as_conversation(self._conn, session_id)

    @staticmethod
    def _get_messages_as_conversation(
        conn: sqlite3.Connection,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            "SELECT role, content, tool_call_id, tool_calls, tool_name, "
            "finish_reason, reasoning, reasoning_details, action_refs, timestamp "
            "FROM messages WHERE session_id = ? ORDER BY sequence_no",
            (session_id,),
        ).fetchall()
        messages = []
        for row in rows:
            msg = {"role": row["role"], "content": row["content"]}
            if row["timestamp"]:
                msg["timestamp"] = row["timestamp"]
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            if row["tool_name"]:
                msg["tool_name"] = row["tool_name"]
            if row["finish_reason"]:
                msg["finish_reason"] = row["finish_reason"]
            if row["action_refs"]:
                try:
                    msg["action_refs"] = json.loads(row["action_refs"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to deserialize action_refs in conversation replay")
                    msg["action_refs"] = []
            if row["tool_calls"]:
                try:
                    msg["tool_calls"] = json.loads(row["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to deserialize tool_calls in conversation replay, falling back to []")
                    msg["tool_calls"] = []
            # Restore reasoning fields on assistant messages so providers
            # that replay reasoning (OpenRouter, OpenAI, Nous) receive
            # coherent multi-turn reasoning context.
            if row["role"] == "assistant":
                if row["reasoning"]:
                    msg["reasoning"] = row["reasoning"]
                if row["reasoning_details"]:
                    try:
                        msg["reasoning_details"] = json.loads(row["reasoning_details"])
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Failed to deserialize reasoning_details, falling back to None")
                        msg["reasoning_details"] = None
            messages.append(msg)
        return messages

    def truncate_last_user_turn(self, session_id: str) -> int:
        """Delete the latest user message and all later messages atomically."""
        def _do(conn):
            boundary = conn.execute(
                "SELECT sequence_no FROM messages "
                "WHERE session_id = ? AND role = 'user' "
                "ORDER BY sequence_no DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if boundary is None:
                return 0

            cursor = conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND "
                "sequence_no >= ?",
                (session_id, boundary["sequence_no"]),
            )
            remaining_tool_calls = conn.execute(
                "SELECT tool_calls FROM messages "
                "WHERE session_id = ? AND tool_calls IS NOT NULL",
                (session_id,),
            ).fetchall()
            tool_call_count = 0
            for row in remaining_tool_calls:
                try:
                    calls = json.loads(row["tool_calls"])
                    tool_call_count += len(calls) if isinstance(calls, list) else 1
                except (json.JSONDecodeError, TypeError):
                    continue
            message_count = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(sequence_no), 0) "
                "FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            conn.execute(
                "UPDATE sessions SET message_count = ?, tool_call_count = ?, "
                "flush_sequence = ?, transcript_revision = transcript_revision + 1, "
                "transcript_hash = ? "
                "WHERE id = ?",
                (
                    int(message_count[0]),
                    tool_call_count,
                    int(message_count[1]),
                    self._transcript_hash_from_connection(conn, session_id),
                    session_id,
                ),
            )
            return cursor.rowcount

        return self._execute_write(_do)

    # =========================================================================
    # Search
    # =========================================================================

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Sanitize user input for safe use in FTS5 MATCH queries.

        FTS5 has its own query syntax where characters like ``"``, ``(``, ``)``,
        ``+``, ``*``, ``{``, ``}`` and bare boolean operators (``AND``, ``OR``,
        ``NOT``) have special meaning.  Passing raw user input directly to
        MATCH can cause ``sqlite3.OperationalError``.

        Strategy:
        - Preserve properly paired quoted phrases (``"exact phrase"``)
        - Strip unmatched FTS5-special characters that would cause errors
        - Wrap unquoted hyphenated and dotted terms in quotes so FTS5
          matches them as exact phrases instead of splitting on the
          hyphen/dot (e.g. ``chat-send``, ``P2.2``, ``my-app.config.ts``)
        """
        # Step 1: Extract balanced double-quoted phrases and protect them
        # from further processing via numbered placeholders.
        _quoted_parts: list = []

        def _preserve_quoted(m: re.Match) -> str:
            _quoted_parts.append(m.group(0))
            return f"\x00Q{len(_quoted_parts) - 1}\x00"

        sanitized = re.sub(r'"[^"]*"', _preserve_quoted, query)

        # Step 2: Strip remaining (unmatched) FTS5-special characters
        sanitized = re.sub(r'[+{}()\"^]', " ", sanitized)

        # Step 3: Collapse repeated * (e.g. "***") into a single one,
        # and remove leading * (prefix-only needs at least one char before *)
        sanitized = re.sub(r"\*+", "*", sanitized)
        sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)

        # Step 4: Remove dangling boolean operators at start/end that would
        # cause syntax errors (e.g. "hello AND" or "OR world")
        sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
        sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())

        # Step 5: Wrap unquoted dotted and/or hyphenated terms in double
        # quotes.  FTS5's tokenizer splits on dots and hyphens, turning
        # ``chat-send`` into ``chat AND send`` and ``P2.2`` into ``p2 AND 2``.
        # Quoting preserves phrase semantics.  A single pass avoids the
        # double-quoting bug that would occur if dotted and hyphenated
        # patterns were applied sequentially (e.g. ``my-app.config``).
        sanitized = re.sub(r"\b(\w+(?:[.-]\w+)+)\b", r'"\1"', sanitized)

        # Step 6: Restore preserved quoted phrases
        for i, quoted in enumerate(_quoted_parts):
            sanitized = sanitized.replace(f"\x00Q{i}\x00", quoted)

        return sanitized.strip()

    def search_messages(
        self,
        query: str,
        source_filter: List[str] = None,
        exclude_sources: List[str] = None,
        role_filter: List[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Full-text search across session messages using FTS5.

        Supports FTS5 query syntax:
          - Simple keywords: "docker deployment"
          - Phrases: '"exact phrase"'
          - Boolean: "docker OR kubernetes", "python NOT java"
          - Prefix: "deploy*"

        Returns matching messages with session metadata, content snippet,
        and surrounding context (1 message before and after the match).
        """
        if not query or not query.strip():
            return []

        query = self._sanitize_fts5_query(query)
        if not query:
            return []

        # Build WHERE clauses dynamically
        where_clauses = ["messages_fts MATCH ?"]
        params: list = [query]

        if source_filter is not None:
            source_placeholders = ",".join("?" for _ in source_filter)
            where_clauses.append(f"s.source IN ({source_placeholders})")
            params.extend(source_filter)

        if exclude_sources is not None:
            exclude_placeholders = ",".join("?" for _ in exclude_sources)
            where_clauses.append(f"s.source NOT IN ({exclude_placeholders})")
            params.extend(exclude_sources)

        if role_filter:
            role_placeholders = ",".join("?" for _ in role_filter)
            where_clauses.append(f"m.role IN ({role_placeholders})")
            params.extend(role_filter)

        where_sql = " AND ".join(where_clauses)
        params.extend([limit, offset])

        sql = f"""
            SELECT
                m.id,
                m.session_id,
                m.role,
                snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
                m.content,
                m.timestamp,
                m.tool_name,
                s.source,
                s.model,
                s.started_at AS session_started
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE {where_sql}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """

        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
            except sqlite3.OperationalError:
                # FTS5 query syntax error despite sanitization — return empty
                return []
            matches = [dict(row) for row in cursor.fetchall()]

        # Add surrounding context (1 message before + after each match).
        # Done outside the lock so we don't hold it across N sequential queries.
        for match in matches:
            try:
                with self._lock:
                    ctx_cursor = self._conn.execute(
                        """SELECT role, content FROM messages
                           WHERE session_id = ? AND id >= ? - 1 AND id <= ? + 1
                           ORDER BY id""",
                        (match["session_id"], match["id"], match["id"]),
                    )
                    context_msgs = [
                        {"role": r["role"], "content": (r["content"] or "")[:200]}
                        for r in ctx_cursor.fetchall()
                    ]
                match["context"] = context_msgs
            except Exception:
                match["context"] = []

        # Remove full content from result (snippet is enough, saves tokens)
        for match in matches:
            match.pop("content", None)

        return matches

    def search_sessions(
        self,
        source: str = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by source."""
        with self._lock:
            if source:
                cursor = self._conn.execute(
                    "SELECT * FROM sessions WHERE source = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (source, limit, offset),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Utility
    # =========================================================================

    def session_count(self, source: str = None) -> int:
        """Count sessions, optionally filtered by source."""
        with self._lock:
            if source:
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE source = ?", (source,)
                )
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM sessions")
            return cursor.fetchone()[0]

    def message_count(self, session_id: str = None) -> int:
        """Count messages, optionally for a specific session."""
        with self._lock:
            if session_id:
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
                )
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM messages")
            return cursor.fetchone()[0]

    # =========================================================================
    # Export and cleanup
    # =========================================================================

    def export_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Export a single session with all its messages as a dict."""
        session = self.get_session(session_id)
        if not session:
            return None
        messages = self.get_messages(session_id)
        return {**session, "messages": messages}

    def export_all(self, source: str = None) -> List[Dict[str, Any]]:
        """
        Export all sessions (with messages) as a list of dicts.
        Suitable for writing to a JSONL file for backup/analysis.
        """
        sessions = self.search_sessions(source=source, limit=100000)
        results = []
        for session in sessions:
            messages = self.get_messages(session["id"])
            results.append({**session, "messages": messages})
        return results

    def clear_messages(self, session_id: str) -> None:
        """Delete all messages for a session and reset its counters."""
        def _do(conn):
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            conn.execute(
                "UPDATE sessions SET message_count = 0, tool_call_count = 0 WHERE id = ?",
                (session_id,),
            )
        self._execute_write(_do)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages.

        Child sessions are orphaned (parent_session_id set to NULL) rather
        than cascade-deleted, so they remain accessible independently.
        Returns True if the session was found and deleted.
        """
        def _do(conn):
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)
            )
            if cursor.fetchone()[0] == 0:
                return False
            # Orphan child sessions so FK constraint is satisfied
            conn.execute(
                "UPDATE sessions SET parent_session_id = NULL "
                "WHERE parent_session_id = ?",
                (session_id,),
            )
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return True
        return self._execute_write(_do)

    def prune_sessions(self, older_than_days: int = 90, source: str = None) -> int:
        """Delete sessions older than N days. Returns count of deleted sessions.

        Only prunes ended sessions (not active ones).  Child sessions outside
        the prune window are orphaned (parent_session_id set to NULL) rather
        than cascade-deleted.
        """
        cutoff = time.time() - (older_than_days * 86400)

        def _do(conn):
            if source:
                cursor = conn.execute(
                    """SELECT id FROM sessions
                       WHERE started_at < ? AND ended_at IS NOT NULL AND source = ?""",
                    (cutoff, source),
                )
            else:
                cursor = conn.execute(
                    "SELECT id FROM sessions WHERE started_at < ? AND ended_at IS NOT NULL",
                    (cutoff,),
                )
            session_ids = set(row["id"] for row in cursor.fetchall())

            if not session_ids:
                return 0

            # Orphan any sessions whose parent is about to be deleted
            placeholders = ",".join("?" * len(session_ids))
            conn.execute(
                f"UPDATE sessions SET parent_session_id = NULL "
                f"WHERE parent_session_id IN ({placeholders})",
                list(session_ids),
            )

            for sid in session_ids:
                conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            return len(session_ids)

        return self._execute_write(_do)
