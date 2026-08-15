from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from systems.memory.config import MemoryServiceConfig
from systems.memory.database import open_memory_sqlite
from systems.memory.memory_service import (
    DurableMemoryCreate,
    ForgetRequest,
    IdentityExperienceVerification,
    MemoryService,
    RecallFeedbackCreate,
    RecallRequest,
    SessionCreate,
    TimelineQuery,
    TurnCreate,
    TurnPairCreate,
)
from systems.memory.recall import (
    _query_relevance_score,
    _temporal_fit_score,
    build_recall_plan,
    normalize_text,
)
from systems.memory.tier1_to_tier2_bridge import (
    _write_compressed_memories_to_db,
)


pytestmark = [pytest.mark.unit]


def test_query_relevance_requires_lexical_or_strong_semantic_support():
    weak_semantic_only = _query_relevance_score(0.0, 0.354)
    combined_support = _query_relevance_score(1.0 / 3.0, 0.559)

    assert 0.70 * weak_semantic_only + 0.20 < 0.5
    assert 0.70 * combined_support + 0.20 >= 0.5


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
            recall_max_context_chars=1200,
        )
    )


@pytest.mark.asyncio
async def test_compressed_reader_is_independent_of_migration_column_order(tmp_path):
    db_path = tmp_path / "migrated.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE compressed_memories ("
        "memory_id TEXT PRIMARY KEY, memory_type TEXT NOT NULL, title TEXT NOT NULL, "
        "summary TEXT NOT NULL, timespan_start TEXT NOT NULL, timespan_end TEXT NOT NULL, "
        "importance REAL, confidence REAL, topics TEXT, entities TEXT, source_turns TEXT, "
        "parent_id TEXT, compressed_at TEXT NOT NULL)"
    )
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO compressed_memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("migrated-row", "event", "Migrated", "Correct fields", stamp, stamp,
         0.8, 0.9, "[]", "[]", "[]", None, stamp),
    )
    conn.commit()
    conn.close()
    service = MemoryService(MemoryServiceConfig(db_path=str(db_path)))
    row = await service.get_compressed("migrated-row")
    assert row["event_kind"] is None
    assert row["pinned"] is False
    assert row["hidden"] is False
    assert row["memory_domain"] == "agent_interaction"
    assert row["created_at"] == stamp


def _insert_turn(
    service: MemoryService,
    *,
    turn_id: str,
    session_id: str,
    text: str,
    timestamp: datetime,
    speaker: str = "user",
    owner_id: str = "local-user",
    workspace_id: str = "default",
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = timestamp.isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, owner_id, workspace_id, stamp, stamp, "{}"),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
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
                owner_id,
                workspace_id,
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
    memory_type: str = "event",
    owner_id: str = "local-user",
    workspace_id: str = "default",
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = timestamp.isoformat()
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, compressed_at, "
            "status, weight, event_kind, owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0.9, ?, '[]', '[]', ?, "
            "'active', 0.8, 'decision', ?, ?)",
            (
                memory_id,
                memory_type,
                title,
                summary,
                stamp,
                stamp,
                importance,
                json.dumps(topics or [], ensure_ascii=False),
                stamp,
                owner_id,
                workspace_id,
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


@pytest.mark.asyncio
async def test_timeline_queries_all_sessions_and_includes_fractional_day_end(tmp_path):
    service = _service(tmp_path)
    for turn_id, session_id, stamp in (
        ("first", "session-a", datetime.fromisoformat("2026-08-05T09:00:00+08:00")),
        ("last", "session-b", datetime.fromisoformat("2026-08-05T23:59:59.999999+08:00")),
        ("next", "session-c", datetime.fromisoformat("2026-08-06T00:00:00+08:00")),
    ):
        _insert_turn(
            service,
            turn_id=turn_id,
            session_id=session_id,
            text=turn_id,
            timestamp=stamp,
        )

    result = await service.timeline_view(TimelineQuery(date="2026-08-05"))
    filtered = await service.timeline_view(
        TimelineQuery(date="2026-08-05", session_id="session-b")
    )

    assert [turn["turn_id"] for turn in result["turns"]] == ["first", "last"]
    assert [turn["turn_id"] for turn in filtered["turns"]] == ["last"]


def test_recall_deduplicates_text_that_mostly_contains_another_result(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="short",
        session_id="session-a",
        text="请你再次检查一下自己的记忆系统看看它是否存在问题？",
        timestamp=now,
    )
    _insert_turn(
        service,
        turn_id="long",
        session_id="session-b",
        text="我在修理记忆系统请你再次检查一下自己的记忆系统看看它是否存在问题给我提供参考",
        timestamp=now - timedelta(minutes=10),
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        from systems.memory.recall import recall_memories

        result = recall_memories(
            conn,
            build_recall_plan("检查记忆系统是否存在问题"),
            min_score=0.0,
        )
    finally:
        conn.close()

    assert result["count"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "你是谁你记得吗",
        "你叫什么？",
        "你还记得自己吗",
        "我们是谁？",
        "你记得锚点吗",
        "你的身份是什么？",
        "Who are you?",
        "What is the VoidCube identity?",
        "Do you remember who you are?",
    ],
)
def test_identity_queries_have_a_first_class_plan_without_noise_terms(query):
    plan = build_recall_plan(query)

    assert plan.intent == "identity"
    assert plan.terms == ()
    assert {"身份", "星子", "小星", "voidcube", "锚点"} <= set(
        plan.concept_terms
    )


def test_identity_name_in_operational_query_is_not_identity_intent():
    assert build_recall_plan("帮我查看星子的配置").intent != "identity"
    assert build_recall_plan("星子昨天做了什么").intent != "identity"


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
    assert all(
        {"raw_score", "normalized_score", "score"} <= set(item)
        for item in result["results"]
    )
    assert all(
        0.0 <= float(item[field]) <= 1.0
        for item in result["results"]
        for field in ("raw_score", "normalized_score", "score")
    )
    assert result["trace_id"]
    health = await service.health_check()
    assert health["recall"]["requests"] == 1
    assert health["recall"]["hits"] == 1
    assert health["recall"]["last_result_count"] == 2
    assert health["recall"]["last_latency_ms"] >= 0
    assert health["service_reachable"] is True
    assert health["database"]["readable"] is True
    assert health["database"]["integrity"] == "ok"
    assert health["database"]["counts"]["turns"] >= 2
    assert "pending_count" in health["semantic_index"]


@pytest.mark.asyncio
async def test_mixed_language_query_scores_latin_and_numeric_matches(tmp_path):
    service = _service(tmp_path)
    _insert_compressed(
        service,
        memory_id="event-api-retry",
        title="API 429 retry 策略",
        summary="API 429 retry 采用指数退避，最多三次。",
        timestamp=datetime.now(timezone.utc),
        topics=["API", "429", "retry"],
    )

    result = await service.recall(RecallRequest(query="API 429 retry几次"))

    assert result["results"][0]["id"] == "event-api-retry"
    assert result["results"][0]["signals"]["lexical"] > 0.8
    assert set(result["results"][0]["matched_terms"]) >= {"api", "429", "retry"}


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
async def test_recent_user_plan_recall_does_not_require_lexical_overlap(tmp_path):
    service = _service(tmp_path)
    _insert_turn(
        service,
        turn_id="upcoming-move",
        session_id="plan-session",
        text="我下周就要搬去杭州了。",
        timestamp=datetime.now(timezone.utc),
    )

    result = await service.recall(
        RecallRequest(query="用户最近要做什么？", include_tier2=False)
    )

    assert result["query_plan"]["intent"] == "recent_conversation"
    assert result["results"][0]["id"] == "upcoming-move"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "text"),
    (
        ("用户当前遇到了什么问题？", "现在卡在一个报错上，查了一下午。"),
        ("用户希望被怎么称呼？", "以后叫我 River 就好。"),
    ),
)
async def test_concept_aliases_recall_user_state_without_exact_terms(
    tmp_path, query, text
):
    service = _service(tmp_path)
    _insert_turn(
        service,
        turn_id="concept-match",
        session_id="concept-session",
        text=text,
        timestamp=datetime.now(timezone.utc),
    )

    result = await service.recall(RecallRequest(query=query, include_tier2=False))

    assert result["results"][0]["id"] == "concept-match"
    assert result["results"][0]["matched_terms"]


@pytest.mark.asyncio
async def test_current_state_recall_prioritizes_elliptical_update(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="framework-old",
        session_id="framework-old-session",
        text="项目框架我们用的是 Vue。",
        timestamp=now - timedelta(days=20),
    )
    _insert_turn(
        service,
        turn_id="framework-current",
        session_id="framework-current-session",
        text="我们改用 React 了。",
        timestamp=now - timedelta(days=1),
    )
    for index in range(25):
        _insert_turn(
            service,
            turn_id=f"unrelated-chat-{index}",
            session_id=f"unrelated-session-{index}",
            text=f"今天完成了第 {index} 项普通会议记录。",
            timestamp=now - timedelta(minutes=index),
        )
    _insert_turn(
        service,
        turn_id="unrelated-database-update",
        session_id="database-session",
        text="数据库改用 PostgreSQL 了。",
        timestamp=now - timedelta(minutes=30),
    )
    _insert_turn(
        service,
        turn_id="unrelated-deployment-update",
        session_id="deployment-session",
        text="部署方案切回 Compose 了。",
        timestamp=now - timedelta(minutes=31),
    )

    result = await service.recall(
        RecallRequest(query="项目现在用什么框架？", include_tier2=False)
    )

    assert result["results"][0]["id"] == "framework-current"
    assert result["results"][0]["signals"]["state_update"] == 1.0
    assert "unrelated-database-update" not in {
        item["id"] for item in result["results"]
    }
    assert "unrelated-deployment-update" not in {
        item["id"] for item in result["results"]
    }


@pytest.mark.asyncio
async def test_current_issue_query_excludes_unrelated_elliptical_update(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="current-blocker",
        session_id="blocker-session",
        text="现在卡在一个报错上。",
        timestamp=now - timedelta(hours=1),
    )
    _insert_turn(
        service,
        turn_id="unrelated-framework-update",
        session_id="framework-session",
        text="项目改用 React 了。",
        timestamp=now,
    )

    result = await service.recall(
        RecallRequest(query="用户当前遇到了什么问题？", include_tier2=False)
    )

    assert [item["id"] for item in result["results"]] == ["current-blocker"]


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
async def test_immediate_specific_recall_prefers_recent_synonym_over_old_exact_word(
    tmp_path,
):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="old-exact-fault",
        session_id="old-session",
        text="旧审查曾记录一个无关故障。",
        timestamp=now - timedelta(days=14),
    )
    _insert_turn(
        service,
        turn_id="recent-synonym",
        session_id="recent-session",
        text="记忆系统失效的根因是 memai 包版本冲突。",
        timestamp=now - timedelta(hours=8),
    )
    _insert_compressed(
        service,
        memory_id="tier2-exact-fault",
        title="长期故障",
        summary="这是一个长期故障，不是刚才的对话。",
        timestamp=now,
    )

    result = await service.recall(
        RecallRequest(query="我们刚才谈到的故障是什么", limit=3)
    )

    assert result["results"][0]["id"] == "recent-synonym"
    assert result["query_plan"]["immediate_recency"] is True
    assert {item["tier"] for item in result["results"]} == {"tier1"}


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
async def test_explicit_durable_memory_supersedes_old_conclusion(tmp_path):
    service = _service(tmp_path)
    old = await service.remember(
        DurableMemoryCreate(
            title="Memory diagnosis",
            summary="The service is still failing.",
            evidence_refs=["turn:old"],
            event_kind="blocker",
        )
    )
    old_id = old["memory"]["memory_id"]

    new = await service.remember(
        DurableMemoryCreate(
            title="Memory diagnosis repaired",
            summary="The service repair is verified.",
            evidence_refs=["turn:new"],
            event_kind="completion",
            supersedes_memory_ids=[old_id],
        )
    )
    retried = await service.remember(
        DurableMemoryCreate(
            title="Memory diagnosis repaired",
            summary="The service repair is verified.",
            evidence_refs=["turn:new"],
            event_kind="completion",
            supersedes_memory_ids=[old_id],
        )
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        old_status = conn.execute(
            "SELECT status, superseded_by FROM compressed_memories WHERE memory_id = ?",
            (old_id,),
        ).fetchone()
    finally:
        conn.close()
    recalled = await service.recall(
        RecallRequest(query="memory diagnosis service", include_tier1=False)
    )

    assert old_status == ("superseded", new["memory"]["memory_id"])
    assert retried["memory"]["memory_id"] == new["memory"]["memory_id"]
    assert [item["id"] for item in recalled["results"]] == [
        new["memory"]["memory_id"]
    ]


@pytest.mark.asyncio
async def test_explicit_supersession_rejects_missing_or_out_of_scope_memory(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(HTTPException, match="must be active in the same scope"):
        await service.remember(
            DurableMemoryCreate(
                title="Unsupported replacement",
                summary="This must not create a broken version chain.",
                supersedes_memory_ids=["missing-memory"],
            )
        )

    conn = open_memory_sqlite(service._db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories "
            "WHERE title = 'Unsupported replacement'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


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
    assert strict["recall_status"] == "weak_match"


@pytest.mark.asyncio
async def test_recall_rejects_empty_query_and_disabled_tiers(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(HTTPException, match="query is required"):
        await service.recall(RecallRequest(query="  "))
    with pytest.raises(HTTPException, match="at least one memory tier"):
        await service.recall(
            RecallRequest(query="memory", include_tier1=False, include_tier2=False)
        )


@pytest.mark.asyncio
async def test_recall_enforces_owner_and_workspace_scope(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="owner-a-turn",
        session_id="owner-a-session",
        text="数据库迁移必须保留私有备份。",
        timestamp=now,
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    _insert_turn(
        service,
        turn_id="owner-b-turn",
        session_id="owner-b-session",
        text="数据库迁移使用另一个私有方案。",
        timestamp=now,
        owner_id="owner-b",
        workspace_id="workspace-b",
    )

    result = await service.recall(
        RecallRequest(
            query="数据库迁移私有方案",
            include_tier2=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert {item["id"] for item in result["results"]} == {"owner-a-turn"}


@pytest.mark.asyncio
async def test_recall_uses_archived_original_as_evidence_fallback(tmp_path):
    service = _service(tmp_path)
    stamp = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO turns_archive "
            "(turn_id, session_id, speaker, text_summary, original_text, timestamp, "
            "compressed_at, event_ids, scene_ids, owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?)",
            (
                "archived-detail",
                "archive-session",
                "user",
                "迁移细节",
                "数据库迁移使用校验码 backup-4821 作为恢复证据。",
                stamp,
                stamp,
                "owner-a",
                "workspace-a",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = await service.recall(
        RecallRequest(
            query="数据库迁移校验码 backup-4821",
            owner_id="owner-a",
            workspace_id="workspace-a",
            include_tier2=False,
        )
    )

    assert result["results"][0]["id"] == "archived-detail"
    assert result["results"][0]["tier"] == "archive"
    assert result["results"][0]["signals"]["archive_fallback"] is True


@pytest.mark.asyncio
async def test_turn_pair_write_is_atomic_scoped_and_idempotent(tmp_path):
    service = _service(tmp_path)
    request = TurnPairCreate(
        session_id="pair-session",
        user_content="请记住部署前备份。",
        assistant_content="已确认。",
        write_id="write-pair-1",
        owner_id="owner-a",
        workspace_id="workspace-a",
    )

    first = await service.add_turn_pair(request)
    repeated = await service.add_turn_pair(request)
    conn = open_memory_sqlite(service._db_path)
    try:
        rows = conn.execute(
            "SELECT speaker, owner_id, workspace_id FROM turns "
            "WHERE session_id = ? ORDER BY speaker",
            ("pair-session",),
        ).fetchall()
    finally:
        conn.close()

    assert first["turn_ids"] == repeated["turn_ids"]
    assert rows == [
        ("agent", "owner-a", "workspace-a"),
        ("user", "owner-a", "workspace-a"),
    ]


@pytest.mark.asyncio
async def test_profile_memory_is_persisted_recalled_and_superseded(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="profile-source",
        session_id="profile-session",
        text="我偏好使用 Podman。",
        timestamp=now,
        owner_id="owner-a",
        workspace_id="workspace-a",
    )

    def profile(memory_id: str, value: str, summary: str):
        return SimpleNamespace(
            id=memory_id,
            memory_kind="preference",
            subject="user",
            predicate="container_runtime",
            value=value,
            summary=summary,
            confidence=0.95,
            certainty_state="confirmed",
            status="active",
            valid_from=now,
            valid_to=None,
            evidence_refs=["turn:profile-source"],
            source_turns=["profile-source"],
            supersedes=[],
            conflict_refs=[],
            created_at=now,
        )

    conn = open_memory_sqlite(service._db_path)
    try:
        _write_compressed_memories_to_db(
            conn,
            SimpleNamespace(
                events=[], scenes=[], arcs=[], epochs=[],
                profile_memories=[profile("profile-1", "docker", "用户偏好 Docker。")],
            ),
            now.isoformat(),
        )
        _write_compressed_memories_to_db(
            conn,
            SimpleNamespace(
                events=[], scenes=[], arcs=[], epochs=[],
                profile_memories=[profile("profile-2", "podman", "用户改为偏好 Podman。")],
            ),
            now.isoformat(),
        )
        conn.commit()
        statuses = conn.execute(
            "SELECT memory_id, status FROM profile_memories ORDER BY memory_id"
        ).fetchall()
    finally:
        conn.close()

    result = await service.recall(
        RecallRequest(
            query="用户偏好什么容器运行时 Podman",
            memory_type="profile",
            include_tier1=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert statuses == [("profile-1", "superseded"), ("profile-2", "active")]
    assert [item["id"] for item in result["results"]] == ["profile-2"]


@pytest.mark.asyncio
async def test_explicit_recall_feedback_changes_future_ranking(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="feedback-target",
        session_id="feedback-session",
        text="数据库迁移备份方案使用旧脚本。",
        timestamp=now,
    )
    first = await service.recall(
        RecallRequest(query="数据库迁移备份旧脚本", include_tier2=False)
    )
    await service.record_recall_feedback(
        RecallFeedbackCreate(
            trace_id=first["trace_id"],
            memory_id="feedback-target",
            verdict="incorrect",
            reason="用户确认这条方案已经错误。",
        )
    )

    second = await service.recall(
        RecallRequest(
            query="数据库迁移备份旧脚本",
            include_tier2=False,
            min_score=0.0,
        )
    )

    assert second["results"][0]["signals"]["feedback_delta"] == -0.5
    assert second["results"][0]["score"] < first["results"][0]["score"]
    assert all(
        0.0 <= float(second["results"][0][field]) <= 1.0
        for field in ("raw_score", "normalized_score", "score")
    )


@pytest.mark.asyncio
async def test_forget_session_hard_deletes_only_requested_scope(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="forget-owner-a",
        session_id="forget-session-a",
        text="私有部署口令提示 alpha。",
        timestamp=now,
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    _insert_turn(
        service,
        turn_id="keep-owner-b",
        session_id="forget-session-b",
        text="私有部署口令提示 beta。",
        timestamp=now,
        owner_id="owner-b",
        workspace_id="workspace-b",
    )
    trace = await service.recall(
        RecallRequest(
            query="私有部署口令提示 alpha",
            current_session_id="forget-session-a",
            include_tier2=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO memory_embeddings "
            "(source_type, memory_id, owner_id, workspace_id, content_hash, model, "
            "provider, dimensions, vector, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "turn",
                "forget-owner-a",
                "owner-a",
                "workspace-a",
                "hash-a",
                "test-model",
                "test-provider",
                2,
                "[1.0,0.0]",
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    forgotten = await service.forget_memory(
        ForgetRequest(
            session_id="forget-session-a",
            reason="用户明确要求删除该会话",
            confirmation="FORGET",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )
    owner_a = await service.recall(
        RecallRequest(
            query="私有部署口令提示",
            include_tier2=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )
    owner_b = await service.recall(
        RecallRequest(
            query="私有部署口令提示",
            include_tier2=False,
            owner_id="owner-b",
            workspace_id="workspace-b",
        )
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        derived_counts = {
            "fts": conn.execute(
                "SELECT COUNT(*) FROM memory_fts WHERE memory_id = 'forget-owner-a'"
            ).fetchone()[0],
            "embeddings": conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings "
                "WHERE memory_id = 'forget-owner-a'"
            ).fetchone()[0],
            "traces": conn.execute(
                "SELECT COUNT(*) FROM recall_traces WHERE trace_id = ?",
                (trace["trace_id"],),
            ).fetchone()[0],
        }
    finally:
        conn.close()

    assert forgotten["deleted_counts"]["turns"] == 1
    assert forgotten["deleted_counts"]["memory_embeddings"] == 1
    assert forgotten["deleted_counts"]["recall_traces"] == 1
    assert derived_counts == {"fts": 0, "embeddings": 0, "traces": 0}
    assert owner_a["results"] == []
    assert [item["id"] for item in owner_b["results"]] == ["keep-owner-b"]


@pytest.mark.asyncio
async def test_memory_service_force_redacts_all_durable_write_paths(tmp_path):
    service = _service(tmp_path)
    secret = "sk-1234567890abcdefghijklmnop"
    await service.create_session(
        SessionCreate(
            session_id="redaction-session",
            metadata={"token": secret},
        )
    )
    await service.add_turn(
        "redaction-session",
        TurnCreate(
            speaker="user",
            text=f"credential={secret}",
            metadata={"api_key": secret},
        ),
    )
    await service.remember(
        DurableMemoryCreate(
            title=f"Credential {secret}",
            summary=f"Never persist {secret} in full.",
            topics=[secret],
        )
    )
    await service.recall(
        RecallRequest(
            query=f"find leaked credential {secret}",
            current_session_id="redaction-session",
        )
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        stored_values = [
            conn.execute(
                "SELECT metadata FROM sessions WHERE session_id = 'redaction-session'"
            ).fetchone()[0],
            *conn.execute(
                "SELECT text, metadata FROM turns WHERE session_id = 'redaction-session'"
            ).fetchone(),
            *conn.execute(
                "SELECT title, summary, topics FROM compressed_memories "
                "WHERE origin_type = 'agent_explicit_memory'"
            ).fetchone(),
            *conn.execute(
                "SELECT query, query_plan FROM recall_traces "
                "WHERE session_id = 'redaction-session'"
            ).fetchone(),
        ]
    finally:
        conn.close()

    assert all(secret not in str(value) for value in stored_values)
    assert any("..." in str(value) for value in stored_values)


@pytest.mark.asyncio
async def test_all_direct_memory_reads_and_mutations_enforce_scope(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_turn(
        service,
        turn_id="direct-owner-a",
        session_id="direct-session-a",
        text="owner A private turn",
        timestamp=now,
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    _insert_turn(
        service,
        turn_id="direct-owner-b",
        session_id="direct-session-b",
        text="owner B private turn",
        timestamp=now,
        owner_id="owner-b",
        workspace_id="workspace-b",
    )
    _insert_compressed(
        service,
        memory_id="compressed-owner-a",
        title="Owner A memory",
        summary="owner A private compressed memory",
        timestamp=now,
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    _insert_compressed(
        service,
        memory_id="compressed-owner-b",
        title="Owner B memory",
        summary="owner B private compressed memory",
        timestamp=now,
        owner_id="owner-b",
        workspace_id="workspace-b",
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE compressed_memories SET source_turns = ? WHERE memory_id = ?",
            (json.dumps(["direct-owner-a"]), "compressed-owner-a"),
        )
        conn.execute(
            "UPDATE compressed_memories SET source_turns = ? WHERE memory_id = ?",
            (json.dumps(["direct-owner-b"]), "compressed-owner-b"),
        )
        conn.commit()
    finally:
        conn.close()

    sessions = await service.list_sessions(
        owner_id="owner-a", workspace_id="workspace-a"
    )
    turns = await service.query_turns(
        owner_id="owner-a", workspace_id="workspace-a"
    )
    compressed = await service.search_compressed(
        {"owner_id": "owner-a", "workspace_id": "workspace-a"}
    )
    trace = await service.trace_compressed_by_turn(
        "direct-owner-b",
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    stats = await service.tier1_stats(
        owner_id="owner-a", workspace_id="workspace-a"
    )

    assert [item["session_id"] for item in sessions["sessions"]] == [
        "direct-session-a"
    ]
    assert [item["turn_id"] for item in turns["turns"]] == ["direct-owner-a"]
    assert "compressed-owner-a" in {
        item["memory_id"] for item in compressed["results"]
    }
    assert "compressed-owner-b" not in {
        item["memory_id"] for item in compressed["results"]
    }
    assert trace["compressed_memories"] == []
    assert stats["tier1"]["total_turns"] == 1

    with pytest.raises(HTTPException, match="Session not found"):
        await service.get_session(
            "direct-session-b",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    with pytest.raises(HTTPException, match="Turn not found"):
        await service.get_turn(
            "direct-owner-b",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    with pytest.raises(HTTPException, match="Compressed memory not found"):
        await service.get_compressed(
            "compressed-owner-b",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    with pytest.raises(HTTPException, match="Memory not found"):
        await service.pin_memory(
            "compressed-owner-b",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    with pytest.raises(HTTPException, match="Turn not found"):
        await service.verify_identity_experience(
            IdentityExperienceVerification(
                turn_id="direct-owner-b",
                title="Cross-scope attempt",
                summary="This must not be accepted.",
                evidence_refs=["turn:direct-owner-b"],
                owner_id="owner-a",
                workspace_id="workspace-a",
            )
        )


# ── Temporal-aware recall (time-first runtime port) ─────────────────────


def test_build_recall_plan_resolves_this_week_and_this_month():
    anchor = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

    plan = build_recall_plan("这个月我们做了什么", now=anchor)
    assert plan.temporal_intent == "explicit"
    assert plan.timespan_start is not None
    assert plan.timespan_end is not None
    assert plan.timespan_start.startswith("2026-08-01")

    plan_week = build_recall_plan("本周进展如何", now=anchor)
    assert plan_week.temporal_intent == "explicit"
    # 2026-08-04 is a Tuesday; the week starts Monday 2026-08-03.
    assert plan_week.timespan_start.startswith("2026-08-03")

    plan_recent = build_recall_plan("最近聊了什么", now=anchor)
    assert plan_recent.temporal_intent == "implicit"
    assert plan_recent.timespan_start is not None
    assert plan_recent.timespan_end is not None

    plan_plain = build_recall_plan("记忆系统方案", now=anchor)
    assert plan_plain.temporal_intent == "none"
    assert plan_plain.timespan_start is None
    assert plan_plain.timespan_end is None


def test_build_recall_plan_detects_current_state_intent():
    anchor = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

    current = build_recall_plan("用户现在用什么容器运行时", now=anchor)
    assert current.current_state_intent is True

    past = build_recall_plan("之前提到过的那个新项目叫什么", now=anchor)
    assert past.current_state_intent is False
    assert build_recall_plan("记忆系统最新修复结果", now=anchor).current_state_intent
    assert build_recall_plan("记忆系统最近一次诊断", now=anchor).current_state_intent


@pytest.mark.parametrize(
    "query",
    ["unknown provider", "knowledge update", "nowadays settings"],
)
def test_build_recall_plan_does_not_match_current_state_substrings(query):
    plan = build_recall_plan(query)
    assert plan.current_state_intent is (query == "nowadays settings")


@pytest.mark.asyncio
async def test_explicit_month_window_filters_to_in_window_memory(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_compressed(
        service,
        memory_id="in-month",
        title="本月告警阈值",
        summary="本月把监控告警阈值改为 90 秒。",
        timestamp=now,
        topics=["监控"],
    )
    _insert_compressed(
        service,
        memory_id="out-of-month",
        title="旧版告警阈值",
        summary="很久以前把监控告警阈值改为 120 秒。",
        timestamp=now - timedelta(days=70),
        topics=["监控"],
    )

    result = await service.recall(RecallRequest(query="这个月监控告警阈值", limit=5))

    returned = [item["id"] for item in result["results"]]
    assert result["query_plan"]["temporal_intent"] == "explicit"
    assert "in-month" in returned
    assert "out-of-month" not in returned


@pytest.mark.asyncio
async def test_explicit_window_records_temporal_fit_signal(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_compressed(
        service,
        memory_id="window-memory",
        title="本月告警阈值",
        summary="本月把监控告警阈值改为 90 秒。",
        timestamp=now,
        topics=["监控"],
    )

    result = await service.recall(RecallRequest(query="这个月监控告警阈值", limit=3))

    assert result["query_plan"]["temporal_intent"] == "explicit"
    assert result["results"]
    assert result["results"][0]["id"] == "window-memory"
    assert result["results"][0]["signals"]["temporal_fit"] == pytest.approx(1.0)


def test_temporal_fit_ranks_inside_window_above_edge():
    window_start = "2026-08-01T00:00:00Z"
    window_end = "2026-08-31T23:59:59Z"
    full = _temporal_fit_score(
        "2026-08-01T00:00:00Z",
        "2026-08-31T23:59:59Z",
        window_start,
        window_end,
    )
    edge = _temporal_fit_score(
        "2026-08-30T00:00:00Z",
        "2026-08-30T23:59:59Z",
        window_start,
        window_end,
    )
    outside = _temporal_fit_score(
        "2026-07-01T00:00:00Z",
        "2026-07-01T23:59:59Z",
        window_start,
        window_end,
    )

    assert full == pytest.approx(1.0)
    assert full > edge > 0
    assert outside == 0.0


def test_temporal_fit_outside_point_does_not_outrank_inside_span():
    start = "2026-08-01T00:00:00Z"
    end = "2026-08-31T00:00:00Z"
    outside_point = _temporal_fit_score(
        "2026-07-12T00:00:00Z", "2026-07-12T00:00:00Z", start, end
    )
    inside_span = _temporal_fit_score(
        "2026-08-01T00:00:00Z", "2026-08-21T00:00:00Z", start, end
    )
    assert outside_point == 0.0
    assert inside_span > outside_point


@pytest.mark.asyncio
async def test_structural_tie_breaker_prefers_arc_over_event(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_compressed(
        service,
        memory_id="arc-memory",
        title="记忆压缩主弧线",
        summary="记忆压缩主线跨越多轮迭代形成完整脉络。",
        timestamp=now,
        topics=["记忆压缩"],
        memory_type="arc",
    )
    _insert_compressed(
        service,
        memory_id="event-memory",
        title="记忆压缩事件",
        summary="记忆压缩事件记录了本次压缩决策。",
        timestamp=now,
        topics=["记忆压缩"],
        memory_type="event",
    )

    result = await service.recall(RecallRequest(query="记忆压缩", limit=5))

    returned = [item["id"] for item in result["results"]]
    assert returned[0] == "arc-memory"
    assert "event-memory" in returned
    assert returned.index("arc-memory") < returned.index("event-memory")
