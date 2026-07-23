from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import DurableMemoryCreate, MemoryService, RecallRequest
from systems.memory.recall import build_recall_plan, normalize_text
from systems.memory.tier1_to_tier2_bridge import open_memory_sqlite


pytestmark = [pytest.mark.unit]


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
            recall_max_context_chars=1200,
        )
    )


def _insert_turn(
    service: MemoryService,
    *,
    turn_id: str,
    session_id: str,
    text: str,
    timestamp: datetime,
    speaker: str = "user",
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = timestamp.isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, created_at, updated_at, metadata) VALUES (?, ?, ?, ?)",
            (session_id, stamp, stamp, "{}"),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                turn_id,
                session_id,
                speaker,
                text,
                stamp,
                1.0,
                0.01,
                "[]",
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_compressed(
    service: MemoryService,
    *,
    memory_id: str,
    title: str,
    summary: str,
    timestamp: datetime,
    importance: float = 0.8,
    topics: list[str] | None = None,
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = timestamp.isoformat()
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, compressed_at, "
            "status, weight, event_kind) "
            "VALUES (?, 'event', ?, ?, ?, ?, ?, 0.9, ?, '[]', '[]', ?, "
            "'active', 0.8, 'decision')",
            (
                memory_id,
                title,
                summary,
                stamp,
                stamp,
                importance,
                json.dumps(topics or [], ensure_ascii=False),
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_multilingual_plan_extracts_concepts_instead_of_only_the_whole_sentence():
    plan = build_recall_plan("我们之前关于数据库迁移做了什么决定？")

    assert "数据库" in plan.terms
    assert "迁移" in plan.terms
    assert "决定" in plan.terms
    assert plan.recency_intent is True
    assert normalize_text(plan.query) == plan.normalized_query


def test_today_uses_the_callers_local_calendar_day_in_utc_boundaries():
    local_now = datetime(2026, 7, 23, 9, 30, tzinfo=timezone(timedelta(hours=8)))

    plan = build_recall_plan("今天讨论了什么", now=local_now)

    assert plan.timespan_start == "2026-07-22T16:00:00+00:00"
    assert plan.timespan_end == "2026-07-23T16:00:00+00:00"


@pytest.mark.asyncio
async def test_recall_mixes_recent_tier1_and_durable_tier2(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="turn-preference",
        session_id="older-session",
        text="用户要求数据库迁移必须先创建可验证备份。",
        timestamp=now - timedelta(days=2),
    )
    _insert_turn(
        service,
        turn_id="turn-unrelated",
        session_id="current-session",
        text="今天讨论了界面颜色。",
        timestamp=now,
    )
    _insert_compressed(
        service,
        memory_id="event-migration",
        title="数据库迁移决策",
        summary="团队决定迁移前运行完整性检查并保留回滚备份。",
        timestamp=now - timedelta(days=40),
        topics=["数据库", "迁移"],
    )

    result = await service.recall(
        RecallRequest(
            query="我们之前关于数据库迁移做了什么决定？",
            current_session_id="current-session",
            limit=5,
        )
    )

    assert {item["tier"] for item in result["results"]} == {"tier1", "tier2"}
    assert [item["score"] for item in result["results"]] == sorted(
        [item["score"] for item in result["results"]], reverse=True
    )
    assert "event-migration" in {item["id"] for item in result["results"]}
    assert "turn-preference" in {item["id"] for item in result["results"]}
    assert "turn-unrelated" not in {item["id"] for item in result["results"]}
    assert result["query_plan"]["method"] == "lexical_concept_hybrid"
    assert result["query_plan"]["intent"] == "specific_memory"
    assert "Relevant recalled memory:" in result["context"]
    assert "id=event-migration" in result["context"]
    assert "score=" in result["context"]
    assert result["trace_id"]
    health = await service.health_check()
    assert health["recall"]["requests"] == 1
    assert health["recall"]["hits"] == 1
    assert health["recall"]["last_result_count"] == 2
    assert health["recall"]["last_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_recency_intent_falls_back_to_latest_tier1_turns(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="turn-old",
        session_id="session-old",
        text="较早的讨论内容。",
        timestamp=now - timedelta(days=5),
    )
    _insert_turn(
        service,
        turn_id="turn-latest",
        session_id="session-new",
        text="最新讨论确认发布前必须运行回归测试。",
        timestamp=now - timedelta(hours=1),
    )

    result = await service.recall(
        RecallRequest(query="最近我们聊了什么？", include_tier2=False, limit=1)
    )

    assert result["count"] == 1
    assert result["results"][0]["id"] == "turn-latest"
    assert result["query_plan"]["intent"] == "recent_conversation"


@pytest.mark.asyncio
async def test_recent_conversation_does_not_compete_with_identity_tier2(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="turn-latest",
        session_id="current-session",
        text="我们刚刚确认先修复真实对话召回。",
        timestamp=now - timedelta(minutes=5),
    )
    _insert_compressed(
        service,
        memory_id="identity-anchor",
        title="高权重身份锚点",
        summary="这是长期身份信息，不是最近一次对话。",
        timestamp=now,
        importance=1.0,
    )

    result = await service.recall(
        RecallRequest(
            query="我们上次讨论了什么？",
            current_session_id="current-session",
            limit=5,
        )
    )

    assert [item["id"] for item in result["results"]] == ["turn-latest"]
    assert {item["tier"] for item in result["results"]} == {"tier1"}


@pytest.mark.asyncio
async def test_specific_recall_keeps_best_same_session_result(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="correct-current",
        session_id="current-session",
        text="记忆系统失效的根因是 memai 包版本冲突。",
        timestamp=now,
    )
    _insert_turn(
        service,
        turn_id="older-other",
        session_id="older-session",
        text="旧分析认为记忆系统失效是因为压缩从未运行。",
        timestamp=now - timedelta(days=1),
    )

    result = await service.recall(
        RecallRequest(
            query="之前记忆系统为什么失效",
            current_session_id="current-session",
            include_tier2=False,
            limit=1,
        )
    )

    assert result["results"][0]["id"] == "correct-current"
    assert result["results"][0]["signals"]["same_session"] is True


@pytest.mark.asyncio
async def test_concept_expansion_recalls_synonymous_failure_wording(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="failure-root-cause",
        session_id="session-1",
        text="记忆服务失效源于包版本冲突。",
        timestamp=now,
    )
    _insert_turn(
        service,
        turn_id="story-distractor",
        session_id="session-2",
        text="这是关于记忆、工作、历史、原因和长期成长的完整故事。",
        timestamp=now + timedelta(minutes=1),
    )

    result = await service.recall(
        RecallRequest(
            query="记忆为什么不工作",
            include_tier2=False,
            limit=3,
        )
    )

    assert result["results"][0]["id"] == "failure-root-cause"
    assert "失效" in result["results"][0]["matched_terms"]


@pytest.mark.asyncio
async def test_failure_cause_terms_outrank_generic_memory_analysis(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="generic-analysis",
        session_id="older-session",
        text="记忆系统有很多历史失败和不可用问题，需要继续分析原因。",
        timestamp=now,
    )
    _insert_turn(
        service,
        turn_id="exact-root-cause",
        session_id="root-cause-session",
        text="记忆系统失效的根因是 memai 包版本冲突。",
        timestamp=now - timedelta(days=1),
    )

    result = await service.recall(
        RecallRequest(
            query="之前记忆系统为什么失效",
            include_tier2=False,
            limit=2,
        )
    )

    assert result["results"][0]["id"] == "exact-root-cause"


@pytest.mark.asyncio
async def test_recall_trace_is_persisted_with_selected_evidence(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="trace-turn",
        session_id="trace-session",
        text="数据库迁移必须保留回滚证据。",
        timestamp=now,
    )

    result = await service.recall(
        RecallRequest(
            query="数据库迁移证据",
            current_session_id="trace-session",
            request_source="auto_prefetch",
        )
    )
    traces = await service.list_recall_traces(session_id="trace-session")

    assert traces["count"] == 1
    trace = traces["traces"][0]
    assert trace["trace_id"] == result["trace_id"]
    assert trace["request_source"] == "auto_prefetch"
    assert trace["status"] == "hit"
    assert trace["selected_results"][0]["id"] == "trace-turn"


@pytest.mark.asyncio
async def test_explicit_durable_memory_is_idempotent_and_recallable(tmp_path):
    service = _service(tmp_path)
    request = DurableMemoryCreate(
        title="Deployment rollback decision",
        summary="Always create a verified backup before database migration.",
        topics=["database", "migration"],
        evidence_refs=["turn:turn-decision"],
        event_kind="decision",
        importance=0.95,
    )

    first = await service.remember(request)
    second = await service.remember(request)
    recalled = await service.recall(
        RecallRequest(query="database migration backup", include_tier1=False)
    )

    assert first["memory"]["memory_id"] == second["memory"]["memory_id"]
    assert recalled["results"][0]["id"] == first["memory"]["memory_id"]
    assert recalled["results"][0]["evidence_refs"] == ["turn:turn-decision"]


@pytest.mark.asyncio
async def test_recall_enforces_full_context_budget(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="turn-long",
        session_id="session-long",
        text="数据库迁移决定：" + "必须保留证据。" * 200,
        timestamp=now,
    )

    result = await service.recall(
        RecallRequest(
            query="数据库迁移决定",
            include_tier2=False,
            limit=5,
            max_context_chars=300,
        )
    )

    assert len(result["context"]) <= 300
    assert result["context_chars"] == len(result["context"])
    assert result["truncated"] is True
    assert result["results"][0]["summary"].endswith("...")


@pytest.mark.asyncio
async def test_recall_limits_one_session_and_filters_low_value_results(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    for index in range(3):
        _insert_turn(
            service,
            turn_id=f"same-{index}",
            session_id="same-session",
            text=f"数据库迁移证据条目 {index}，内容各不相同。",
            timestamp=now - timedelta(hours=index + 1),
        )
    _insert_turn(
        service,
        turn_id="other-session",
        session_id="other-session",
        text="数据库迁移还需要保留回滚方案。",
        timestamp=now - timedelta(days=1),
    )

    result = await service.recall(
        RecallRequest(
            query="数据库迁移",
            include_tier2=False,
            limit=5,
            min_score=0.2,
        )
    )
    strict = await service.recall(
        RecallRequest(
            query="数据库迁移",
            include_tier2=False,
            limit=5,
            min_score=1.0,
        )
    )

    assert sum(
        item.get("session_id") == "same-session" for item in result["results"]
    ) == 2
    assert "other-session" in {item["id"] for item in result["results"]}
    assert strict["results"] == []


@pytest.mark.asyncio
async def test_recall_rejects_empty_query_and_disabled_tiers(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(HTTPException, match="query is required"):
        await service.recall(RecallRequest(query="  "))
    with pytest.raises(HTTPException, match="at least one memory tier"):
        await service.recall(
            RecallRequest(query="memory", include_tier1=False, include_tier2=False)
        )
