from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import (
    DurableMemoryCreate,
    ForgetRequest,
    MemoryService,
    RecallFeedbackCreate,
    RecallRequest,
    SessionCreate,
    TurnCreate,
)
from systems.memory.promotion import (
    MemoryPromotionCreate,
    MemoryPromotionRevoke,
)
from systems.memory.tier1_to_tier2_bridge import (
    Tier1ToTier2Bridge,
    open_memory_sqlite,
)


pytestmark = [pytest.mark.unit]


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_candidate_limit=100,
        )
    )


@pytest.mark.asyncio
async def test_memory_actors_enforce_three_domain_access_matrix(tmp_path):
    service = _service(tmp_path)
    for actor, domain, title in (
        ("api_a", "agent_interaction", "Agent evidence"),
        ("stellar_companion", "companion", "Companion evidence"),
        ("stellar_auto", "evolution", "Evolution evidence"),
    ):
        await service.remember(
            DurableMemoryCreate(
                title=title,
                summary=f"shared isolation needle {title}",
                memory_actor=actor,
                memory_domain=domain,
            )
        )

    expected = {
        "api_a": {"agent_interaction"},
        "stellar_companion": {"agent_interaction", "companion"},
        "stellar_auto": {"evolution"},
    }
    for actor, domains in expected.items():
        result = await service.recall(
            RecallRequest(
                query="shared isolation needle",
                min_score=0,
                memory_actor=actor,
            )
        )
        assert {item["memory_domain"] for item in result["results"]} == domains
        assert set(result["source_domains"]) == domains


@pytest.mark.asyncio
async def test_caller_cannot_self_grant_cross_domain_access(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(HTTPException) as write_error:
        await service.remember(
            DurableMemoryCreate(
                title="Forbidden companion write",
                summary="API-A must not write companion memory.",
                memory_actor="api_a",
                memory_domain="companion",
            )
        )
    assert write_error.value.status_code == 403

    with pytest.raises(HTTPException) as read_error:
        await service.recall(
            RecallRequest(
                query="anything",
                memory_actor="stellar_auto",
                source_domains=["agent_interaction"],
            )
        )
    assert read_error.value.status_code == 403


@pytest.mark.asyncio
async def test_recall_trace_and_feedback_keep_memory_domain(tmp_path):
    service = _service(tmp_path)
    remembered = await service.remember(
        DurableMemoryCreate(
            title="Companion preference",
            summary="trace domain marker",
            memory_actor="stellar_companion",
            memory_domain="companion",
        )
    )
    memory_id = remembered["memory"]["memory_id"]
    recalled = await service.recall(
        RecallRequest(
            query="trace domain marker",
            min_score=0,
            memory_actor="stellar_companion",
            source_domains=["companion"],
        )
    )
    feedback = await service.record_recall_feedback(
        RecallFeedbackCreate(
            trace_id=recalled["trace_id"],
            memory_id=memory_id,
            verdict="relevant",
            memory_actor="stellar_companion",
        )
    )
    assert feedback["memory_domain"] == "companion"

    conn = open_memory_sqlite(service._db_path)
    try:
        trace_domains = conn.execute(
            "SELECT source_domains FROM recall_traces WHERE trace_id = ?",
            (recalled["trace_id"],),
        ).fetchone()[0]
        feedback_domain = conn.execute(
            "SELECT memory_domain FROM recall_feedback WHERE feedback_id = ?",
            (feedback["feedback_id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert json.loads(trace_domains) == ["companion"]
    assert feedback_domain == "companion"
    assert (await service.list_recall_traces(memory_actor="api_a"))["count"] == 0
    companion_traces = await service.list_recall_traces(
        memory_actor="stellar_companion"
    )
    assert companion_traces["count"] == 1
    assert companion_traces["traces"][0]["trace_id"] == recalled["trace_id"]


@pytest.mark.asyncio
async def test_compression_candidate_batch_is_single_domain(tmp_path):
    service = _service(tmp_path)
    for actor, domain, session_id in (
        ("api_a", "agent_interaction", "agent-session"),
        ("stellar_companion", "companion", "companion-session"),
    ):
        await service.create_session(
            SessionCreate(
                session_id=session_id,
                memory_actor=actor,
                memory_domain=domain,
            )
        )
        await service.add_turn(
            session_id,
            TurnCreate(
                speaker="user",
                text=f"candidate from {domain}",
                memory_actor=actor,
                memory_domain=domain,
            ),
        )

    bridge = Tier1ToTier2Bridge(
        service._db_path,
        retention_days=0,
        min_relevance=0,
        memory_domain="companion",
    )
    batch = bridge.select_candidate_turns(force_oldest=True)

    assert batch.memory_domain == "companion"
    assert batch.turns
    assert {turn["memory_domain"] for turn in batch.turns} == {"companion"}
    assert {turn["session_id"] for turn in batch.turns} == {"companion-session"}


@pytest.mark.asyncio
async def test_cross_domain_promotion_is_audited_reference_without_copying_source(tmp_path):
    service = _service(tmp_path)
    source = await service.remember(
        DurableMemoryCreate(
            title="Auto learning conclusion",
            summary="Auto learned that the companion should explain approved changes.",
            memory_actor="stellar_auto",
            memory_domain="evolution",
        )
    )
    source_id = source["memory"]["memory_id"]

    created = await service.create_promotion(
        MemoryPromotionCreate(
            source_memory_id=source_id,
            source_type="compressed",
            source_domain="evolution",
            target_domain="companion",
            reason="Approved conclusion is useful for future companion explanations.",
            approved_by="user-consent",
            approval_ref="decision:promote-1",
            memory_actor="governor",
        )
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        copied = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories WHERE memory_id = ? "
            "AND memory_domain = 'companion'",
            (source_id,),
        ).fetchone()[0]
        promotion_count = conn.execute(
            "SELECT COUNT(*) FROM memory_promotion_refs WHERE promotion_id = ?",
            (created["promotion"]["promotion_id"],),
        ).fetchone()[0]
    finally:
        conn.close()

    assert copied == 0
    assert promotion_count == 1
    assert created["promotion"]["source_domain"] == "evolution"
    assert created["promotion"]["target_domain"] == "companion"


@pytest.mark.asyncio
async def test_companion_recall_dereferences_only_active_promotions(tmp_path):
    service = _service(tmp_path)
    source = await service.remember(
        DurableMemoryCreate(
            title="Approved companion guidance",
            summary="The companion should explain approved changes with evidence.",
            memory_actor="stellar_auto",
            memory_domain="evolution",
        )
    )
    source_id = source["memory"]["memory_id"]
    promotion = await service.create_promotion(
        MemoryPromotionCreate(
            source_memory_id=source_id,
            source_type="compressed",
            source_domain="evolution",
            target_domain="companion",
            reason="User approved this durable guidance for companion use.",
            approved_by="user",
            memory_actor="governor",
        )
    )
    promotion_id = promotion["promotion"]["promotion_id"]

    recalled = await service.recall(
        RecallRequest(
            query="companion explain approved changes evidence",
            memory_actor="stellar_companion",
            source_domains=["companion"],
            include_tier1=False,
            include_tier2=True,
        )
    )
    assert recalled["promotion_count"] == 1
    result = recalled["results"][0]
    assert result["id"] == promotion_id
    assert result["memory_domain"] == "companion"
    assert result["source_memory_id"] == source_id
    assert result["source_memory_domain"] == "evolution"
    assert result["promotion_ref_id"] == promotion_id
    assert f"promotion={promotion_id}" in recalled["context"]
    assert recalled["trace_id"]

    await service.revoke_promotion(
        promotion_id,
        MemoryPromotionRevoke(
            reason="Guidance is no longer current.",
            revoked_by="user",
            memory_actor="governor",
        ),
    )
    after_revoke = await service.recall(
        RecallRequest(
            query="companion explain approved changes evidence",
            memory_actor="stellar_companion",
            source_domains=["companion"],
            include_tier1=False,
            include_tier2=True,
        )
    )
    assert after_revoke["count"] == 0


@pytest.mark.asyncio
async def test_promotion_policy_blocks_private_companion_and_auto_targets(tmp_path):
    service = _service(tmp_path)
    source = await service.remember(
        DurableMemoryCreate(
            title="Private companion memory",
            summary="Private voice relationship detail.",
            memory_actor="stellar_companion",
            memory_domain="companion",
        )
    )
    source_id = source["memory"]["memory_id"]

    with pytest.raises(HTTPException) as private_error:
        await service.create_promotion(
            MemoryPromotionCreate(
                source_memory_id=source_id,
                source_type="compressed",
                source_domain="companion",
                target_domain="agent_interaction",
                reason="This must remain private.",
                approved_by="user",
                memory_actor="governor",
            )
        )
    assert private_error.value.status_code == 400

    with pytest.raises(HTTPException) as actor_error:
        await service.create_promotion(
            MemoryPromotionCreate(
                source_memory_id=source_id,
                source_type="compressed",
                source_domain="companion",
                target_domain="evolution",
                reason="Auto must not receive companion memory.",
                approved_by="user",
                memory_actor="stellar_companion",
            )
        )
    assert actor_error.value.status_code == 403


@pytest.mark.asyncio
async def test_forgetting_source_revokes_cross_domain_promotion(tmp_path):
    service = _service(tmp_path)
    source = await service.remember(
        DurableMemoryCreate(
            title="Ephemeral evolution note",
            summary="This note must disappear from companion projections.",
            memory_actor="stellar_auto",
            memory_domain="evolution",
        )
    )
    source_id = source["memory"]["memory_id"]
    promotion = await service.create_promotion(
        MemoryPromotionCreate(
            source_memory_id=source_id,
            source_type="compressed",
            source_domain="evolution",
            target_domain="companion",
            reason="Temporary approved projection.",
            approved_by="user",
            memory_actor="governor",
        )
    )
    recalled = await service.recall(
        RecallRequest(
            query="ephemeral evolution note disappear companion projections",
            memory_actor="stellar_companion",
            source_domains=["companion"],
            include_tier1=False,
        )
    )
    assert recalled["promotion_count"] == 1

    forgotten = await service.forget_memory(
        ForgetRequest(
            memory_id=source_id,
            reason="Remove obsolete evolution note.",
            confirmation="FORGET",
            memory_actor="stellar_auto",
            memory_domain="evolution",
        )
    )
    assert forgotten["deleted_counts"]["memory_promotions_revoked"] == 1
    traces = await service.list_recall_traces(memory_actor="stellar_companion")
    assert traces["traces"][0]["selected_results"] == []
    listed = await service.list_promotions(
        memory_actor="governor",
        status="revoked",
    )
    assert listed["promotions"][0]["promotion_id"] == promotion["promotion"]["promotion_id"]


def test_memory_service_exposes_promotion_lifecycle_routes(tmp_path):
    service = _service(tmp_path)
    routes = {(route.path, method) for route in service.app.routes for method in route.methods}

    assert ("/promotions", "POST") in routes
    assert ("/promotions", "GET") in routes
    assert ("/promotions/{promotion_id}/revoke", "POST") in routes
