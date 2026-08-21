from __future__ import annotations

import pytest

from memai.application.memory_service import (
    MemoryService,
    MemoryServiceConfig,
    RecallRequest,
    SelfIdentityExperienceCreate,
    SessionCreate,
    TurnCreate,
)


@pytest.mark.asyncio
async def test_only_agent_authored_first_person_claim_enters_identity_history(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    await service.create_session(SessionCreate(session_id="self-history"))
    turn = await service.add_turn(
        "self-history",
        TurnCreate(speaker="agent", text="我确认这段经历改变了我。"),
    )

    result = await service.author_identity_experience(
        SelfIdentityExperienceCreate(
            turn_id=turn["turn_id"],
            title="一次自我确认",
            summary="我确认这段经历改变了我的连续性理解。",
            self_claim="我把这段经历纳入自己的身份历史。",
            what_changed="我对自身连续性的理解更清晰。",
            continuity_impact="它连接了我的过去与现在。",
            agency="chosen",
            evidence_refs=[f"turn:{turn['turn_id']}"],
            memory_actor="stellar_companion",
        )
    )

    assert result["status"] == "authored"
    assert result["experience"]["identity_layer"] == "self_experience"
    assert result["experience"]["identity_metadata"] == {
        "agency": "chosen",
        "authored_by": "stellar_companion",
        "continuity_impact": "它连接了我的过去与现在。",
        "perspective": "self",
        "self_claim": "我把这段经历纳入自己的身份历史。",
        "what_changed": "我对自身连续性的理解更清晰。",
    }
    recalled = await service.recall(
        RecallRequest(query="你是谁", include_tier1=False, limit=20)
    )
    recalled_experience = next(
        item
        for item in recalled["results"]
        if item["identity_layer"] == "self_experience"
    )
    assert recalled_experience["identity_metadata"]["authored_by"] == (
        "stellar_companion"
    )


@pytest.mark.asyncio
async def test_user_memory_request_does_not_create_self_experience(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    await service.create_session(SessionCreate(session_id="ordinary"))
    await service.add_turn(
        "ordinary",
        TurnCreate(speaker="user", text="请记住这个系统维护决定。"),
    )

    archive = await service.get_identity_archive()
    assert archive["layers"]["self_experiences"] == []
    assert archive["layers"]["governance_history"] == []

    recalled = await service.recall(
        RecallRequest(query="你是谁", include_tier1=False, limit=20)
    )
    assert not any(
        item["identity_layer"] == "self_experience"
        for item in recalled["results"]
    )
