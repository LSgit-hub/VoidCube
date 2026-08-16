from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from types import SimpleNamespace

from systems.memory.config import MemoryServiceConfig
from systems.memory.database import open_memory_sqlite
from systems.memory.memory_service import MemoryService
from systems.memory.profile_store import upsert_profile_memory


def _profile(memory_id: str, predicate: str, value: str):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=memory_id,
        memory_kind="preference",
        subject="user",
        predicate=predicate,
        value=value,
        summary=value,
        confidence=0.9,
        certainty_state="observed",
        status="active",
        valid_from=now,
        valid_to=None,
        evidence_refs=[f"turn:{memory_id}"],
        source_turns=[memory_id],
        supersedes=[],
        conflict_refs=[],
        created_at=now,
    )


def test_profile_cardinality_keeps_collections_and_revises_scalars(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        for profile in (
            _profile("concise", "long_term_preference", "简洁回答"),
            _profile("chinese", "long_term_preference", "中文回答"),
            _profile("language-old", "preferred_language", "English"),
            _profile("language-new", "preferred_language", "中文"),
        ):
            upsert_profile_memory(
                conn,
                profile,
                owner_id="local-user",
                workspace_id="default",
                now=now,
            )
        conn.commit()
        rows = conn.execute(
            "SELECT memory_id, predicate, status, slot_key FROM profile_memories "
            "ORDER BY memory_id"
        ).fetchall()
    finally:
        conn.close()

    by_id = {row[0]: row[1:] for row in rows}
    assert by_id["concise"][1] == "active"
    assert by_id["chinese"][1] == "active"
    assert by_id["concise"][2] != by_id["chinese"][2]
    assert by_id["language-old"][1] == "superseded"
    assert by_id["language-new"][1] == "active"
    assert by_id["language-old"][2] == by_id["language-new"][2]


def test_legacy_resource_fields_migrate_once_and_repair_relations(tmp_path):
    db_path = tmp_path / "memory.db"
    stamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE turns (turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "speaker TEXT NOT NULL, text TEXT NOT NULL, timestamp TEXT NOT NULL, "
            "relevance_score REAL DEFAULT 1.0, decay_factor REAL DEFAULT 0.01, "
            "tags TEXT, metadata TEXT, compression_retry_count INTEGER DEFAULT 0, "
            "compression_retry_after TEXT, compressed_to_tier2 INTEGER DEFAULT 0)"
        )
        conn.executemany(
            "INSERT INTO turns VALUES (?, 'legacy-session', 'user', ?, ?, 1.0, "
            "0.01, '[]', '{}', ?, ?, ?)",
            (
                ("pending", "pending text", stamp, 0, None, 0),
                ("retry", "retry text", stamp, 1, stamp, 0),
                ("compressed", "compressed text", stamp, 0, None, 1),
                ("quarantined", "quarantined text", stamp, 3, None, -1),
            ),
        )
        conn.execute(
            "CREATE TABLE compressed_memories (memory_id TEXT PRIMARY KEY, "
            "memory_type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, "
            "timespan_start TEXT NOT NULL, timespan_end TEXT NOT NULL, importance REAL, "
            "confidence REAL, topics TEXT, entities TEXT, source_turns TEXT, "
            "parent_id TEXT, compressed_at TEXT NOT NULL, compression_level INTEGER, "
            "status TEXT, weight REAL)"
        )
        rows = (
            ("scene-root", "scene", None),
            ("event-linked", "event", "scene-root"),
            ("event-orphan", "event", "missing-scene"),
            ("event-source", "event", None),
            ("scene-derived", "scene", "event-source"),
        )
        conn.executemany(
            "INSERT INTO compressed_memories VALUES (?, ?, ?, ?, ?, ?, 0.8, 0.9, "
            "'[]', '[]', '[]', ?, ?, 0, 'active', 1.0)",
            [(item_id, kind, item_id, item_id, stamp, stamp, parent, stamp)
             for item_id, kind, parent in rows],
        )
        conn.commit()
    finally:
        conn.close()

    service = MemoryService(MemoryServiceConfig(db_path=str(db_path)))
    conn = open_memory_sqlite(service._db_path)
    try:
        turn_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()
        }
        memory_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(compressed_memories)").fetchall()
        }
        statuses = dict(
            conn.execute("SELECT turn_id, compression_status FROM turns").fetchall()
        )
        relations = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT memory_id, timeline_parent_id, derived_from_id "
                "FROM compressed_memories WHERE memory_id IN "
                "('event-linked', 'event-orphan', 'scene-derived')"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "compressed_to_tier2" not in turn_columns
    assert "parent_id" not in memory_columns
    assert statuses == {
        "pending": "pending",
        "retry": "retry_wait",
        "compressed": "compressed",
        "quarantined": "quality_quarantined",
    }
    assert relations["event-linked"] == ("scene-root", None)
    assert relations["event-orphan"] == (None, None)
    assert relations["scene-derived"] == (None, "event-source")
    assert len(list((tmp_path / "backups").glob("memory-*.db"))) == 1
