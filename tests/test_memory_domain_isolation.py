from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import (
    DurableMemoryCreate,
    MemoryService,
    RecallFeedbackCreate,
    RecallRequest,
    SessionCreate,
    TurnCreate,
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
