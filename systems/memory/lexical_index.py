"""SQLite FTS5 index for bounded lexical memory recall."""

from __future__ import annotations

import logging
import sqlite3
from typing import Sequence

from systems.memory.scope import GLOBAL_SCOPE_ID


logger = logging.getLogger(__name__)

_INDEX_VERSION = "1"


def setup_memory_fts(conn: sqlite3.Connection) -> bool:
    """Create the unified index, triggers, and one-time existing-data backfill."""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "source_type UNINDEXED, memory_id UNINDEXED, owner_id UNINDEXED, "
            "workspace_id UNINDEXED, content, tokenize='trigram')"
        )
    except sqlite3.OperationalError as exc:
        logger.warning("FTS5 trigram memory index is unavailable: %s", exc)
        return False

    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_index_metadata ("
        "index_name TEXT PRIMARY KEY, version TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    trigger_sql = {
        "memory_fts_turn_insert": (
            "AFTER INSERT ON turns BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, content) VALUES "
            "('turn', NEW.turn_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.text || ' ' || COALESCE(NEW.tags, '')); END"
        ),
        "memory_fts_turn_update": (
            "AFTER UPDATE OF text, tags, owner_id, workspace_id ON turns BEGIN "
            "DELETE FROM memory_fts WHERE source_type = 'turn' AND memory_id = OLD.turn_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
            "VALUES ('turn', NEW.turn_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.text || ' ' || COALESCE(NEW.tags, '')); END"
        ),
        "memory_fts_turn_delete": (
            "AFTER DELETE ON turns BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'turn' AND memory_id = OLD.turn_id; END"
        ),
        "memory_fts_archive_insert": (
            "AFTER INSERT ON turns_archive BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, content) VALUES "
            "('archive', NEW.turn_id, NEW.owner_id, NEW.workspace_id, "
            "COALESCE(NEW.original_text, NEW.text_summary, '')); END"
        ),
        "memory_fts_archive_update": (
            "AFTER UPDATE OF original_text, text_summary, owner_id, workspace_id "
            "ON turns_archive BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'archive' AND memory_id = OLD.turn_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
            "VALUES ('archive', NEW.turn_id, NEW.owner_id, NEW.workspace_id, "
            "COALESCE(NEW.original_text, NEW.text_summary, '')); END"
        ),
        "memory_fts_archive_delete": (
            "AFTER DELETE ON turns_archive BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'archive' AND memory_id = OLD.turn_id; END"
        ),
        "memory_fts_compressed_insert": (
            "AFTER INSERT ON compressed_memories BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, content) VALUES "
            "('compressed', NEW.memory_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.title || ' ' || NEW.summary || ' ' || COALESCE(NEW.topics, '') || "
            "' ' || COALESCE(NEW.entities, '')); END"
        ),
        "memory_fts_compressed_update": (
            "AFTER UPDATE OF title, summary, topics, entities, owner_id, workspace_id "
            "ON compressed_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'compressed' AND memory_id = OLD.memory_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
            "VALUES ('compressed', NEW.memory_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.title || ' ' || NEW.summary || ' ' || COALESCE(NEW.topics, '') || "
            "' ' || COALESCE(NEW.entities, '')); END"
        ),
        "memory_fts_compressed_delete": (
            "AFTER DELETE ON compressed_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'compressed' AND memory_id = OLD.memory_id; END"
        ),
        "memory_fts_profile_insert": (
            "AFTER INSERT ON profile_memories BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, content) VALUES "
            "('profile', NEW.memory_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.subject || ' ' || NEW.predicate || ' ' || NEW.value || ' ' || NEW.summary); END"
        ),
        "memory_fts_profile_update": (
            "AFTER UPDATE OF subject, predicate, value, summary, owner_id, workspace_id "
            "ON profile_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'profile' AND memory_id = OLD.memory_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
            "VALUES ('profile', NEW.memory_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.subject || ' ' || NEW.predicate || ' ' || NEW.value || ' ' || NEW.summary); END"
        ),
        "memory_fts_profile_delete": (
            "AFTER DELETE ON profile_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'profile' AND memory_id = OLD.memory_id; END"
        ),
    }
    for name, body in trigger_sql.items():
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {body}")

    current = conn.execute(
        "SELECT version FROM memory_index_metadata WHERE index_name = 'memory_fts'"
    ).fetchone()
    if not current or str(current[0]) != _INDEX_VERSION:
        _rebuild_memory_fts(conn)
    return True


def _rebuild_memory_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM memory_fts")
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
        "SELECT 'turn', turn_id, owner_id, workspace_id, text || ' ' || COALESCE(tags, '') "
        "FROM turns"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
        "SELECT 'archive', turn_id, owner_id, workspace_id, "
        "COALESCE(original_text, text_summary, '') FROM turns_archive"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
        "SELECT 'compressed', memory_id, owner_id, workspace_id, "
        "title || ' ' || summary || ' ' || COALESCE(topics, '') || ' ' || COALESCE(entities, '') "
        "FROM compressed_memories"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, content) "
        "SELECT 'profile', memory_id, owner_id, workspace_id, "
        "subject || ' ' || predicate || ' ' || value || ' ' || summary "
        "FROM profile_memories"
    )
    conn.execute(
        "INSERT INTO memory_index_metadata (index_name, version, updated_at) "
        "VALUES ('memory_fts', ?, CURRENT_TIMESTAMP) ON CONFLICT(index_name) DO UPDATE SET "
        "version = excluded.version, updated_at = excluded.updated_at",
        (_INDEX_VERSION,),
    )


def search_memory_fts(
    conn: sqlite3.Connection,
    terms: Sequence[str],
    *,
    owner_id: str,
    workspace_id: str,
    limit: int,
) -> dict[str, tuple[str, ...]]:
    """Return bounded source IDs matching any safe trigram query term."""
    eligible = []
    for raw_term in terms:
        term = str(raw_term or "").strip().lower()
        if len(term) < 3 or term in eligible:
            continue
        eligible.append(term)
    if not eligible or not _fts_table_exists(conn):
        return {}
    match_query = " OR ".join(
        f'"{term.replace(chr(34), chr(34) * 2)}"' for term in eligible[:32]
    )
    rows = conn.execute(
        "SELECT source_type, memory_id FROM memory_fts WHERE memory_fts MATCH ? "
        "AND ((owner_id = ? AND workspace_id = ?) OR "
        "(owner_id = ? AND workspace_id = ?)) ORDER BY bm25(memory_fts) LIMIT ?",
        (
            match_query,
            owner_id,
            workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
            max(1, min(int(limit), 4000)),
        ),
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for source_type, memory_id in rows:
        grouped.setdefault(str(source_type), []).append(str(memory_id))
    return {key: tuple(values) for key, values in grouped.items()}


def _fts_table_exists(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
        ).fetchone()
    )
