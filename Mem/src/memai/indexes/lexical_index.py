"""SQLite FTS5 index for bounded lexical memory recall."""

from __future__ import annotations

import logging
import sqlite3
from typing import Sequence

from memai.domain.scope import GLOBAL_SCOPE_ID


logger = logging.getLogger(__name__)

_INDEX_VERSION = "3-time-summary"


def setup_memory_fts(conn: sqlite3.Connection) -> bool:
    """Create the unified index, triggers, and one-time existing-data backfill."""
    trigger_names = (
        "memory_fts_turn_insert", "memory_fts_turn_update", "memory_fts_turn_delete",
        "memory_fts_archive_insert", "memory_fts_archive_update", "memory_fts_archive_delete",
        "memory_fts_compressed_insert", "memory_fts_compressed_update", "memory_fts_compressed_delete",
        "memory_fts_profile_insert", "memory_fts_profile_update", "memory_fts_profile_delete",
        "memory_fts_time_summary_insert", "memory_fts_time_summary_update",
        "memory_fts_time_summary_delete",
    )
    existing = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
    ).fetchone()
    if existing:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(memory_fts)").fetchall()
        }
        if "memory_domain" not in columns:
            for trigger_name in trigger_names:
                conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            conn.execute("DROP TABLE memory_fts")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "source_type UNINDEXED, memory_id UNINDEXED, owner_id UNINDEXED, "
            "workspace_id UNINDEXED, memory_domain UNINDEXED, content, tokenize='trigram')"
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
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, content) VALUES "
            "('turn', NEW.turn_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "NEW.text || ' ' || COALESCE(NEW.tags, '')); END"
        ),
        "memory_fts_turn_update": (
            "AFTER UPDATE OF text, tags, owner_id, workspace_id, memory_domain ON turns BEGIN "
            "DELETE FROM memory_fts WHERE source_type = 'turn' AND memory_id = OLD.turn_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
            "VALUES ('turn', NEW.turn_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "NEW.text || ' ' || COALESCE(NEW.tags, '')); END"
        ),
        "memory_fts_turn_delete": (
            "AFTER DELETE ON turns BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'turn' AND memory_id = OLD.turn_id; END"
        ),
        "memory_fts_archive_insert": (
            "AFTER INSERT ON turns_archive BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, content) VALUES "
            "('archive', NEW.turn_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "COALESCE(NEW.original_text, NEW.text_summary, '')); END"
        ),
        "memory_fts_archive_update": (
            "AFTER UPDATE OF original_text, text_summary, owner_id, workspace_id, memory_domain "
            "ON turns_archive BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'archive' AND memory_id = OLD.turn_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
            "VALUES ('archive', NEW.turn_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "COALESCE(NEW.original_text, NEW.text_summary, '')); END"
        ),
        "memory_fts_archive_delete": (
            "AFTER DELETE ON turns_archive BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'archive' AND memory_id = OLD.turn_id; END"
        ),
        "memory_fts_compressed_insert": (
            "AFTER INSERT ON compressed_memories BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, content) VALUES "
            "('compressed', NEW.memory_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "NEW.title || ' ' || NEW.summary || ' ' || COALESCE(NEW.topics, '') || "
            "' ' || COALESCE(NEW.entities, '')); END"
        ),
        "memory_fts_compressed_update": (
            "AFTER UPDATE OF title, summary, topics, entities, owner_id, workspace_id, memory_domain "
            "ON compressed_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'compressed' AND memory_id = OLD.memory_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
            "VALUES ('compressed', NEW.memory_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "NEW.title || ' ' || NEW.summary || ' ' || COALESCE(NEW.topics, '') || "
            "' ' || COALESCE(NEW.entities, '')); END"
        ),
        "memory_fts_compressed_delete": (
            "AFTER DELETE ON compressed_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'compressed' AND memory_id = OLD.memory_id; END"
        ),
        "memory_fts_profile_insert": (
            "AFTER INSERT ON profile_memories BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, content) VALUES "
            "('profile', NEW.memory_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "NEW.subject || ' ' || NEW.predicate || ' ' || NEW.value || ' ' || NEW.summary); END"
        ),
        "memory_fts_profile_update": (
            "AFTER UPDATE OF subject, predicate, value, summary, owner_id, workspace_id, memory_domain "
            "ON profile_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'profile' AND memory_id = OLD.memory_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
            "VALUES ('profile', NEW.memory_id, NEW.owner_id, NEW.workspace_id, NEW.memory_domain, "
            "NEW.subject || ' ' || NEW.predicate || ' ' || NEW.value || ' ' || NEW.summary); END"
        ),
        "memory_fts_profile_delete": (
            "AFTER DELETE ON profile_memories BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'profile' AND memory_id = OLD.memory_id; END"
        ),
        "memory_fts_time_summary_insert": (
            "AFTER INSERT ON time_summaries BEGIN INSERT INTO memory_fts "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, content) VALUES "
            "('time_summary', NEW.summary_id, NEW.owner_id, NEW.workspace_id, "
            "NEW.memory_domain, NEW.title || ' ' || NEW.summary || ' ' || "
            "COALESCE(NEW.outcomes, '') || ' ' || COALESCE(NEW.open_questions, '')); END"
        ),
        "memory_fts_time_summary_update": (
            "AFTER UPDATE OF title, summary, outcomes, open_questions, owner_id, "
            "workspace_id, memory_domain ON time_summaries BEGIN "
            "DELETE FROM memory_fts WHERE source_type = 'time_summary' "
            "AND memory_id = OLD.summary_id; "
            "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, "
            "memory_domain, content) VALUES ('time_summary', NEW.summary_id, NEW.owner_id, "
            "NEW.workspace_id, NEW.memory_domain, NEW.title || ' ' || NEW.summary || ' ' || "
            "COALESCE(NEW.outcomes, '') || ' ' || COALESCE(NEW.open_questions, '')); END"
        ),
        "memory_fts_time_summary_delete": (
            "AFTER DELETE ON time_summaries BEGIN DELETE FROM memory_fts "
            "WHERE source_type = 'time_summary' AND memory_id = OLD.summary_id; END"
        ),
    }
    for name, body in trigger_sql.items():
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
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
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
        "SELECT 'turn', turn_id, owner_id, workspace_id, memory_domain, text || ' ' || COALESCE(tags, '') "
        "FROM turns"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
        "SELECT 'archive', turn_id, owner_id, workspace_id, memory_domain, "
        "COALESCE(original_text, text_summary, '') FROM turns_archive"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
        "SELECT 'compressed', memory_id, owner_id, workspace_id, memory_domain, "
        "title || ' ' || summary || ' ' || COALESCE(topics, '') || ' ' || COALESCE(entities, '') "
        "FROM compressed_memories"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
        "SELECT 'profile', memory_id, owner_id, workspace_id, memory_domain, "
        "subject || ' ' || predicate || ' ' || value || ' ' || summary "
        "FROM profile_memories"
    )
    conn.execute(
        "INSERT INTO memory_fts (source_type, memory_id, owner_id, workspace_id, memory_domain, content) "
        "SELECT 'time_summary', summary_id, owner_id, workspace_id, memory_domain, "
        "title || ' ' || summary || ' ' || COALESCE(outcomes, '') || ' ' || "
        "COALESCE(open_questions, '') FROM time_summaries"
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
    source_domains: Sequence[str] = ("agent_interaction",),
    limit: int,
) -> dict[str, tuple[str, ...]]:
    """Return bounded source IDs matching any safe trigram query term."""
    eligible = []
    short_terms = []
    for raw_term in terms:
        term = str(raw_term or "").strip().lower()
        if len(term) < 2 or term in eligible or term in short_terms:
            continue
        if len(term) == 2:
            short_terms.append(term)
        else:
            eligible.append(term)
    if not (eligible or short_terms) or not _fts_table_exists(conn):
        return {}
    domains = tuple(dict.fromkeys(str(item) for item in source_domains))
    if not domains:
        return {}
    placeholders = ",".join("?" for _ in domains)
    rows = []
    if eligible:
        match_query = " OR ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"' for term in eligible[:32]
        )
        rows.extend(conn.execute(
            "SELECT source_type, memory_id FROM memory_fts WHERE memory_fts MATCH ? "
            "AND ((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?)) "
            f"AND memory_domain IN ({placeholders}) ORDER BY bm25(memory_fts) LIMIT ?",
            (match_query, owner_id, workspace_id, GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID,
             *domains, max(1, min(int(limit), 4000))),
        ).fetchall())
    # Two-character CJK LIKE fallback is intentionally reserved for queries
    # that have no longer anchors. Mixing it with long-term FTS matches turns
    # generic fragments into broad candidates and suppresses graph expansion.
    if short_terms and not eligible:
        short_clauses = " OR ".join("content LIKE ?" for _ in short_terms)
        rows.extend(conn.execute(
            "SELECT source_type, memory_id FROM memory_fts WHERE (" + short_clauses + ") "
            "AND ((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?)) "
            f"AND memory_domain IN ({placeholders}) LIMIT ?",
            [*(f"%{term}%" for term in short_terms), owner_id, workspace_id,
             GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID, *domains, max(1, min(int(limit), 4000))],
        ).fetchall())
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
