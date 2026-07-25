from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.semantic_index import SemanticIndexConfig, SemanticMemoryIndex
from systems.memory.tier1_to_tier2_bridge import open_memory_sqlite
from systems.memory.config import MemoryServiceConfig


pytestmark = [pytest.mark.unit]


def _vectors(texts):
    vectors = []
    for text in texts:
        value = str(text).lower()
        if "database migration" in value or "数据库迁移" in value:
            vectors.append([1.0, 0.0, 0.0])
        elif "private-b" in value:
            vectors.append([0.0, 1.0, 0.0])
        else:
            vectors.append([0.0, 0.0, 1.0])
    return vectors


def _service(tmp_path) -> MemoryService:
    return MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))


def _insert_turn(
    service: MemoryService,
    *,
    turn_id: str,
    text: str,
    owner_id: str = "owner-a",
    workspace_id: str = "workspace-a",
) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, '{}')",
            (f"session-{turn_id}", owner_id, workspace_id, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
            "VALUES (?, ?, 'user', ?, ?, 1.0, 0.01, '[]', '{}', 0, ?, ?)",
            (
                turn_id,
                f"session-{turn_id}",
                text,
                stamp,
                owner_id,
                workspace_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _index(service: MemoryService) -> SemanticMemoryIndex:
    return SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(
            enabled=True,
            provider="test-provider",
            model="test-embedding-v2",
            dimensions=3,
        ),
        transport=_vectors,
    )


def test_semantic_index_persists_version_dimensions_and_rebuilds_changed_content(
    tmp_path,
):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="changing", text="数据库迁移采用蓝绿方案。")
    index = _index(service)

    assert index.index_pending() >= 1
    conn = open_memory_sqlite(service._db_path)
    try:
        before = conn.execute(
            "SELECT content_hash, model, dimensions, vector, provider FROM memory_embeddings "
            "WHERE source_type = 'turn' AND memory_id = 'changing'"
        ).fetchone()
        conn.execute(
            "UPDATE turns SET text = ? WHERE turn_id = ?",
            ("改为完全无关的界面主题记录。", "changing"),
        )
        conn.commit()
    finally:
        conn.close()

    assert before[1:3] == ("test-embedding-v2", 3)
    assert json.loads(before[3]) == [1.0, 0.0, 0.0]
    assert before[4] == "test-provider"
    assert index.index_pending() == 1

    conn = open_memory_sqlite(service._db_path)
    try:
        after = conn.execute(
            "SELECT content_hash, vector FROM memory_embeddings "
            "WHERE source_type = 'turn' AND memory_id = 'changing'"
        ).fetchone()
    finally:
        conn.close()
    assert after[0] != before[0]
    assert json.loads(after[1]) == [0.0, 0.0, 1.0]


def test_semantic_index_backfill_advances_beyond_initial_candidate_window(tmp_path):
    service = _service(tmp_path)
    for index in range(11):
        _insert_turn(
            service,
            turn_id=f"backfill-{index:02d}",
            text=f"durable record {index}",
        )
    index = _index(service)

    batch_counts = []
    while count := index.index_pending(limit=2):
        batch_counts.append(count)

    conn = open_memory_sqlite(service._db_path)
    try:
        indexed = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE source_type = 'turn'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert batch_counts
    assert all(count <= 2 for count in batch_counts)
    assert indexed == 11


def test_semantic_index_rebuilds_scope_without_content_change(tmp_path):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="scope-change", text="数据库迁移采用蓝绿方案。")
    index = _index(service)
    assert index.index_pending() >= 1

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE turns SET owner_id = 'owner-b', workspace_id = 'workspace-b' "
            "WHERE turn_id = 'scope-change'"
        )
        conn.commit()
    finally:
        conn.close()

    assert index.index_pending() == 1
    assert ("turn", "scope-change") not in index.search(
        "database migration",
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    assert ("turn", "scope-change") in index.search(
        "database migration",
        owner_id="owner-b",
        workspace_id="workspace-b",
    )


@pytest.mark.asyncio
async def test_semantic_recall_hydrates_nonlexical_match_and_respects_scope(tmp_path):
    service = _service(tmp_path)
    _insert_turn(
        service,
        turn_id="semantic-a",
        text="数据库迁移必须保留可验证的回滚快照。",
    )
    _insert_turn(
        service,
        turn_id="semantic-b",
        text="private-b",
        owner_id="owner-b",
        workspace_id="workspace-b",
    )
    service._semantic_index = _index(service)
    assert service._semantic_index.index_pending() >= 2

    result = await service.recall(
        RecallRequest(
            query="database migration",
            include_tier2=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert [item["id"] for item in result["results"]] == ["semantic-a"]
    assert result["results"][0]["matched_terms"] == []
    assert result["results"][0]["signals"]["semantic"] == 1.0
    assert ("turn", "semantic-b") not in service._semantic_index.search(
        "private-b",
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
