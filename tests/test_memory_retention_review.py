from __future__ import annotations

import json

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from memai.application.memory_service import (
    MemoryService,
    MemoryServiceConfig,
    RetentionReviewRequest,
)
from memai.repository.sqlite import open_memory_sqlite


def _insert_compressed(
    conn,
    *,
    memory_id: str,
    memory_type: str,
    title: str,
    timestamp: str,
    importance: float = 0.1,
    confidence: float = 0.2,
    event_kind: str | None = None,
    timeline_parent_id: str | None = None,
    source_turns: list[str] | None = None,
    pinned: int = 0,
    identity_layer: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO compressed_memories "
        "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
        "importance, confidence, topics, entities, source_turns, timeline_parent_id, "
        "compressed_at, compression_level, status, weight, event_kind, pinned, "
        "identity_layer, owner_id, workspace_id, memory_domain) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?, 0, 'active', "
        "1.0, ?, ?, ?, 'local-user', 'default', 'agent_interaction')",
        (
            memory_id,
            memory_type,
            title,
            title,
            timestamp,
            timestamp,
            importance,
            confidence,
            json.dumps(source_turns or []),
            timeline_parent_id,
            timestamp,
            event_kind,
            pinned,
            identity_layer,
        ),
    )


@pytest.mark.asyncio
async def test_retention_review_reports_dormant_arcs_without_mutating(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    old = "2025-01-01T00:00:00+00:00"
    conn = open_memory_sqlite(service._db_path)
    try:
        _insert_compressed(
            conn,
            memory_id="arc-dormant-candidate",
            memory_type="arc",
            title="Old inactive arc",
            timestamp=old,
            importance=0.9,
            confidence=0.9,
        )
        conn.commit()
    finally:
        conn.close()

    report = await service.review_retention(
        RetentionReviewRequest(reference_time="2025-03-01T00:00:00+00:00")
    )

    assert report["status"] == "dry_run"
    assert report["dry_run"] is True
    assert report["counts"]["dormant_candidates"] == 1
    assert report["dormant_candidates"][0]["memory_id"] == "arc-dormant-candidate"

    conn = open_memory_sqlite(service._db_path)
    try:
        row = conn.execute(
            "SELECT status, access_count, last_accessed_at FROM compressed_memories "
            "WHERE memory_id = 'arc-dormant-candidate'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("active", 0, None)


@pytest.mark.asyncio
async def test_retention_review_selects_only_low_value_represented_events(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    old = "2025-01-01T00:00:00+00:00"
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO turns_archive "
            "(turn_id, session_id, speaker, text_summary, original_text, timestamp, "
            "compressed_at, event_ids, scene_ids, owner_id, workspace_id, memory_domain) "
            "VALUES ('turn-old', 'session-old', 'user', 'old', 'old', ?, ?, '[]', "
            "'[]', 'local-user', 'default', 'agent_interaction')",
            (old, old),
        )
        _insert_compressed(
            conn,
            memory_id="scene-parent",
            memory_type="scene",
            title="Parent summary",
            timestamp=old,
            importance=0.8,
            confidence=0.8,
        )
        _insert_compressed(
            conn,
            memory_id="event-low-value",
            memory_type="event",
            title="Low value progress detail",
            timestamp=old,
            importance=0.1,
            confidence=0.2,
            event_kind="progress",
            timeline_parent_id="scene-parent",
            source_turns=["turn-old"],
        )
        _insert_compressed(
            conn,
            memory_id="event-decision-protected",
            memory_type="event",
            title="Protected decision",
            timestamp=old,
            importance=0.1,
            confidence=0.2,
            event_kind="decision",
            timeline_parent_id="scene-parent",
            source_turns=["turn-old"],
        )
        conn.commit()
    finally:
        conn.close()

    report = await service.review_retention(
        RetentionReviewRequest(reference_time="2026-01-01T00:00:00+00:00")
    )

    assert [item["memory_id"] for item in report["purge_candidates"]] == [
        "event-low-value"
    ]
    protected = {
        item["memory_id"]: item["protected_reasons"]
        for item in report["protected"]
    }
    assert "protected_event_kind" in protected["event-decision-protected"]


@pytest.mark.asyncio
async def test_purge_cycle_purges_and_cleans_indexes(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    old = "2025-01-01T00:00:00+00:00"
    old_candidate = "2025-02-01T00:00:00+00:00"
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO turns_archive "
            "(turn_id, session_id, speaker, text_summary, original_text, timestamp, "
            "compressed_at, event_ids, scene_ids, owner_id, workspace_id, memory_domain) "
            "VALUES (?, ?, 'user', 'old', 'old', ?, ?, '[]', '[]', 'local-user', 'default', 'agent_interaction')",
            ("turn-purge-source", "session-purge-source", old, old),
        )
        _insert_compressed(
            conn,
            memory_id="scene-purge-parent",
            memory_type="scene",
            title="Purge parent",
            timestamp=old,
            importance=0.8,
            confidence=0.8,
        )
        _insert_compressed(
            conn,
            memory_id="event-purge-target",
            memory_type="event",
            title="Old low value detail",
            timestamp=old,
            importance=0.1,
            confidence=0.2,
            event_kind="progress",
            timeline_parent_id="scene-purge-parent",
            source_turns=["turn-purge-source"],
        )
        conn.execute(
            "UPDATE compressed_memories SET retention_state = 'purge_candidate', "
            "purge_candidate_at = ?, purge_reason = ?, activity_state = 'active' "
            "WHERE memory_id = ?",
            (
                old_candidate,
                json.dumps(["old_low_importance"], ensure_ascii=False),
                "event-purge-target",
            ),
        )
        conn.execute(
            "INSERT INTO memory_embeddings "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, content_hash, "
            "provider, model, dimensions, vector, updated_at) "
            "VALUES ('compressed', ?, 'local-user', 'default', 'agent_interaction', ?, ?, ?, ?, ?, ?)",
            (
                "event-purge-target",
                "hash-event-purge-target",
                "local",
                "local-model",
                3,
                json.dumps([0.1, 0.2, 0.3]),
                old,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    deleted = await service._purge_expired_memories()
    assert deleted >= 1

    conn = open_memory_sqlite(service._db_path)
    try:
        row = conn.execute(
            "SELECT status, retention_state, purged_at FROM compressed_memories "
            "WHERE memory_id = 'event-purge-target'"
        ).fetchone()
        embedding_count = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = 'event-purge-target'"
        ).fetchone()[0]
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_id = 'event-purge-target'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert row[0] == "purged"
    assert row[1] == "purged"
    assert row[2]
    assert embedding_count == 0
    assert fts_count == 0

    with pytest.raises(HTTPException, match="Compressed memory not found"):
        await service.get_compressed("event-purge-target")

    trace = await service.trace_compressed_by_turn(
        "turn-purge-source",
        owner_id="local-user",
        workspace_id="default",
    )
    assert trace["compressed_memories"] == []


def test_retention_review_route_is_not_shadowed_by_memory_id_route(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    client = TestClient(service.app)

    response = client.post(
        "/compressed/retention-review",
        json={"reference_time": "2026-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "dry_run"
