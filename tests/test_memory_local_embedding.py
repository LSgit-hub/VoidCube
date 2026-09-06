from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from memai.application.config import MemoryServiceConfig
from memai.application.memory_service import MemoryService, RecallRequest
from memai.repository.sqlite import open_memory_sqlite


pytestmark = [pytest.mark.unit]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def test_char_ngram_embedder_is_deterministic_and_normalized():
    from memai.indexes.local_embedding import CharNgramEmbedder

    embedder = CharNgramEmbedder(dimensions=256)
    a = embedder.embed(["晚上十点之后请勿推送通知。"])[0]
    b = embedder.embed(["晚上十点之后请勿推送通知。"])[0]

    assert a == b  # deterministic
    magnitude = math.sqrt(sum(v * v for v in a))
    assert magnitude == pytest.approx(1.0, abs=1e-6)  # L2-normalized


def test_char_ngram_similarity_ranks_related_above_unrelated():
    from memai.indexes.local_embedding import CharNgramEmbedder

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


def test_local_similarity_calibration_requires_independent_evidence():
    from memai.indexes.semantic_index import _calibrate_local_similarity

    assert _calibrate_local_similarity(0.0) == 0.0
    assert _calibrate_local_similarity(0.20, independent_evidence=0) == 0.0
    assert _calibrate_local_similarity(0.0418121, independent_evidence=1) > 0.35
    assert _calibrate_local_similarity(0.08, independent_evidence=2) > 0.35


def test_local_evidence_collapses_nested_ngrams():
    from memai.indexes.local_embedding import CharNgramEmbedder

    _, unrelated_evidence = CharNgramEmbedder.exact_similarity_evidence(
        "继续自检查记忆系统的问题并尝试改进",
        "应该又出了 BV 号的问题",
    )
    _, related_evidence = CharNgramEmbedder.exact_similarity_evidence(
        "继续自检查记忆系统的问题并尝试改进",
        "记忆系统自检改进与召回排序",
    )
    unrelated_meaningful = CharNgramEmbedder.meaningful_similarity_evidence(
        "继续自检查记忆系统的问题并尝试改进",
        "应该又出了 BV 号的问题",
    )

    assert unrelated_evidence == 1
    assert unrelated_meaningful == 0
    assert related_evidence >= 2


def test_local_exact_similarity_is_not_affected_by_hash_collisions():
    from memai.indexes.local_embedding import CharNgramEmbedder

    assert CharNgramEmbedder.exact_similarity(
        "完全无关的量子香蕉校准协议 ZXQ-917",
        "VoidCube 架构与身份记忆",
    ) == 0.0


def test_semantic_index_defaults_to_local_fallback(tmp_path):
    from memai.indexes.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    # A MemoryService initializes the source tables the index triggers need.
    service = _service(tmp_path)
    config = SemanticIndexConfig(enabled=True, provider="", model="")
    index = SemanticMemoryIndex(service._db_path, config)

    assert index.enabled is True
    assert index._local_fallback is True
    assert index.config.provider == "local"
    assert index.config.model == "char-ngram-v1"


def test_local_embedding_normalizes_dimensions_to_supported_minimum(tmp_path):
    from memai.indexes.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    service = _service(tmp_path)
    index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=True, provider="local", dimensions=32),
    )

    assert index.config.dimensions == 64
    assert len(index._embed(["dimension check"])[0]) == 64


def test_local_embedding_migrates_blank_provider_metadata(tmp_path):
    from memai.indexes.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    service = _service(tmp_path)
    SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=False),
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at) "
            "VALUES ('legacy-session', 'local-user', 'default', "
            "'agent_interaction', ?)",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, tags, metadata, "
            "compression_status, owner_id, workspace_id, memory_domain) "
            "VALUES ('legacy', 'legacy-session', 'user', 'legacy embedding', ?, "
            "'[]', '{}', 'pending', 'local-user', 'default', 'agent_interaction')",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO memory_embeddings "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, "
            "content_hash, provider, model, dimensions, vector, updated_at) "
            "VALUES ('turn', 'legacy', 'local-user', 'default', "
            "'agent_interaction', 'hash', '', '', 64, '[]', ?)",
            (stamp,),
        )
        conn.commit()
    finally:
        conn.close()

    SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=True, provider="", model="", dimensions=64),
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        provider, model = conn.execute(
            "SELECT provider, model FROM memory_embeddings "
            "WHERE memory_id = 'legacy'"
        ).fetchone()
    finally:
        conn.close()

    assert (provider, model) == ("local", "char-ngram-v1")


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
        )
    )


def test_local_semantic_search_applies_as_of_before_candidate_limit(tmp_path):
    from memai.indexes.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

    service = _service(tmp_path)
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, owner_id, workspace_id, memory_domain, created_at) "
            "VALUES ('asof-semantic', 'local-user', 'default', 'agent_interaction', ?)",
            ("2026-01-01T00:00:00+00:00",),
        )
        conn.execute(
            "INSERT INTO turns (turn_id, session_id, speaker, text, timestamp, compression_status, "
            "owner_id, workspace_id, memory_domain) VALUES (?, 'asof-semantic', 'user', ?, ?, 'pending', "
            "'local-user', 'default', 'agent_interaction')",
            (
                "old-semantic",
                "The database migration plan was approved.",
                "2026-01-02T00:00:00+00:00",
            ),
        )
        for index in range(5):
            conn.execute(
                "INSERT INTO turns (turn_id, session_id, speaker, text, timestamp, compression_status, "
                "owner_id, workspace_id, memory_domain) VALUES (?, 'asof-semantic', 'user', ?, ?, 'pending', "
                "'local-user', 'default', 'agent_interaction')",
                (
                    f"future-semantic-{index}",
                    "database migration plan",
                    f"2026-02-{10 + index:02d}T00:00:00+00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(enabled=True, provider="", model=""),
    )
    matches = index.search(
        "database migration plan",
        owner_id="local-user",
        workspace_id="default",
        limit=1,
        as_of="2026-01-31T00:00:00+00:00",
    )

    assert list(matches) == [("turn", "old-semantic")]


@pytest.mark.asyncio
async def test_semantic_recall_surfaces_paraphrase_without_lexical_overlap(
    tmp_path,
):
    from memai.indexes.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

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
            "decay_factor, tags, metadata, compression_status, owner_id, workspace_id, "
            "memory_domain) "
            "VALUES ('ev', 's0', 'user', '晚上十点之后请勿推送通知。', ?, 1.0, 0.01, "
            "'[]', '{}', 'pending', 'local-user', 'default', 'agent_interaction')",
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
    from memai.indexes.semantic_index import SemanticIndexConfig, SemanticMemoryIndex

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
            "decay_factor, tags, metadata, compression_status, owner_id, workspace_id, "
            "memory_domain) VALUES ('architecture', 'unrelated', 'user', "
            "'VoidCube architecture and identity memory', ?, 1.0, 0.01, '[]', '{}', "
            "'pending', 'local-user', 'default', 'agent_interaction')",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    result = await service.recall(
        RecallRequest(query="量子香蕉校准协议 ZXQ-917")
    )

    assert result["results"] == []
    assert result["count"] == 0
    assert result["recall_status"] == "miss"
    assert result["min_score"] == pytest.approx(0.5)
