"""Persistent result cache for Mem LLM calls.

Caching bounds the LLM cost of memory extraction and summarization: the same
turn batch (Tier 1 → Tier 2 compression), the same memory escalation, or the
same purge review re-runs deterministically against the same input, so a
content-addressed cache avoids re-billing for unchanged inputs.

The cache lives in the same SQLite database as the rest of memory state, so it
is backed up and restored with the memory store.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

CACHE_TABLE = "mem_llm_cache"

# Task names used as cache namespaces.
TASK_EXTRACT = "extractor.events"
TASK_ESCALATE = "escalate"
TASK_PURGE_REVIEW = "purge_review"


def build_cache_key(task: str, model: str, input_text: str) -> str:
    """Content-addressed key for one LLM result."""
    raw = f"{task}\x00{model or ''}\x00{input_text or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def setup_llm_cache(conn) -> None:
    """Create the cache table (idempotent)."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (
            cache_key TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            input_hash TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_mem_llm_cache_task "
        f"ON {CACHE_TABLE}(task, model)"
    )


def get_cached(conn, cache_key: str) -> Any | None:
    """Return the parsed cached result for ``cache_key``, or None."""
    row = conn.execute(
        f"SELECT result FROM {CACHE_TABLE} WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def put_cached(
    conn,
    *,
    cache_key: str,
    task: str,
    model: str,
    input_text: str,
    result: Any,
) -> None:
    """Store (or refresh) one cached LLM result."""
    conn.execute(
        f"INSERT INTO {CACHE_TABLE} "
        f"(cache_key, task, model, input_hash, result, created_at) "
        f"VALUES (?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(cache_key) DO UPDATE SET "
        f"result = excluded.result, created_at = excluded.created_at",
        (
            cache_key,
            task,
            model or "",
            build_cache_key(task, model, input_text),
            json.dumps(result, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def clear_cache(conn, *, task: str | None = None) -> int:
    """Delete cached results, optionally for one task. Returns rows deleted."""
    if task:
        cursor = conn.execute(f"DELETE FROM {CACHE_TABLE} WHERE task = ?", (task,))
    else:
        cursor = conn.execute(f"DELETE FROM {CACHE_TABLE}")
    return cursor.rowcount


def open_cached(
    db_path,
    cache_key: str,
) -> Any | None:
    """Open a short-lived connection and read one cached result."""
    from systems.memory.database import open_memory_sqlite

    conn = open_memory_sqlite(db_path)
    try:
        setup_llm_cache(conn)
        return get_cached(conn, cache_key)
    finally:
        conn.close()


def store_cached(
    db_path,
    *,
    cache_key: str,
    task: str,
    model: str,
    input_text: str,
    result: Any,
) -> None:
    """Open a short-lived connection and store one cached result."""
    from systems.memory.database import open_memory_sqlite

    conn = open_memory_sqlite(db_path)
    try:
        setup_llm_cache(conn)
        put_cached(
            conn,
            cache_key=cache_key,
            task=task,
            model=model,
            input_text=input_text,
            result=result,
        )
        conn.commit()
    finally:
        conn.close()
