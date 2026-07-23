from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, RecallRequest
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
    assert result["query_plan"]["method"] == "multilingual_hybrid"
    assert "Relevant recalled memory:" in result["context"]
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
