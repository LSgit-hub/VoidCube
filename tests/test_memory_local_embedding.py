from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.database import open_memory_sqlite


pytestmark = [pytest.mark.unit]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def test_char_ngram_embedder_is_deterministic_and_normalized():
    from systems.memory.local_embedding import CharNgramEmbedder

    embedder = CharNgramEmbedder(dimensions=256)
    a = embedder.embed(["晚上十点之后请勿推送通知。"])[0]
    b = embedder.embed(["晚上十点之后请勿推送通知。"])[0]

    assert a == b  # deterministic
    magnitude = math.sqrt(sum(v * v for v in a))
    assert magnitude == pytest.approx(1.0, abs=1e-6)  # L2-normalized


def test_char_ngram_similarity_ranks_related_above_unrelated():
    from systems.memory.local_embedding import CharNgramEmbedder

    embedder = CharNgramEmbedder(dimensions=256)
    related = _cosine(
        embedder.embed(["晚上十点之后请勿推送通知。"])[0],
        embedder.embed(["用户要求晚上几点后不要被打扰?"])[0],
    )
    unrelated = _cosine(
        embedder.embed(["晚上十点之后请勿推送通知。"])[0],
        embedder.embed(["我们讨论了数据库迁移与备份方案。"])[0],
    )

    assert related > unrelated


def test_local_similarity_calibration_maps_exact_feature_overlap():
    from systems.memory.semantic_index import _calibrate_local_similarity

    assert _calibrate_local_similarity(0.0) == 0.0
    assert _calibrate_local_similarity(0.04) > 0.35


def test_local_exact_similarity_is_not_affected_by_hash_collisions():
    from systems.memory.local_embedding import CharNgramEmbedder

    assert CharNgramEmbedder.exact_similarity(
        "完全无关的量子香蕉校准协议 ZXQ-917",
        "VoidCube 架构与身份记忆",
    ) == 0.0


def test_semantic_index_defaults_to_local_fallback(tmp_path):
    from systems.memory.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    # A MemoryService initializes the source tables the index triggers need.
    service = _service(tmp_path)
    config = SemanticIndexConfig(enabled=True, provider="", model="")
    index = SemanticMemoryIndex(service._db_path, config)

    assert index.enabled is True
    assert index._local_fallback is True
    assert index.config.provider == ""


def test_local_embedding_normalizes_dimensions_to_supported_minimum(tmp_path):
    from systems.memory.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    service = _service(tmp_path)
    index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=True, provider="local", dimensions=32),
    )

    assert index.config.dimensions == 64
    assert len(index._embed(["dimension check"])[0]) == 64


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
        )
    )


@pytest.mark.asyncio
async def test_semantic_recall_surfaces_paraphrase_without_lexical_overlap(
    tmp_path,
):
    from systems.memory.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    service = _service(tmp_path)
    service._semantic_index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=True, provider="", model="", dimensions=256),
    )
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
            "VALUES ('s0', 'local-user', 'default', ?, ?, '{}')",
            (now, now),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id, "
            "memory_domain) "
            "VALUES ('ev', 's0', 'user', '晚上十点之后请勿推送通知。', ?, 1.0, 0.01, "
            "'[]', '{}', 0, 'local-user', 'default', 'agent_interaction')",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    assert service._semantic_index.index_pending(limit=1000) >= 1

    result = await service.recall(
        RecallRequest(
            query="用户要求晚上几点后不要被打扰?",
            owner_id="local-user",
            workspace_id="default",
            limit=5,
        )
    )

    assert result["results"]
    top = result["results"][0]
    assert top["id"] == "ev"
    assert top["signals"]["lexical"] == 0.0  # surfaced by semantic, not lexical
    assert top["signals"]["semantic"] > 0.35


@pytest.mark.asyncio
async def test_local_semantic_recall_returns_empty_for_unrelated_query(tmp_path):
    from systems.memory.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    service = _service(tmp_path)
    service._semantic_index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=True, provider="", model="", dimensions=256),
    )
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
            "VALUES ('unrelated', 'local-user', 'default', ?, ?, '{}')",
            (now, now),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id, "
            "memory_domain) VALUES ('architecture', 'unrelated', 'user', "
            "'VoidCube architecture and identity memory', ?, 1.0, 0.01, '[]', '{}', "
            "0, 'local-user', 'default', 'agent_interaction')",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    result = await service.recall(
        RecallRequest(query="量子香蕉校准协议 ZXQ-917", min_score=0.5)
    )

    assert result["results"] == []
    assert result["count"] == 0
    assert result["recall_status"] == "miss"
