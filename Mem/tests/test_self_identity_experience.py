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
@pytest.mark.parametrize("agency", ["chosen", "accepted", "observed", "imposed"])
async def test_all_declared_identity_agency_values_are_projectable(tmp_path, agency):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / f"{agency}.db")))
    await service.create_session(SessionCreate(session_id=f"agency-{agency}"))
    turn = await service.add_turn(
        f"agency-{agency}",
        TurnCreate(speaker="agent", text=f"我以 {agency} 的方式理解这段经历。"),
    )
    result = await service.author_identity_experience(
        SelfIdentityExperienceCreate(
            turn_id=turn["turn_id"],
            title="身份经历",
            summary="一段完整的身份经历。",
            self_claim="我将这段经历纳入自己的历史。",
            what_changed="我对自己的理解发生变化。",
            continuity_impact="它连接了我的连续性。",
            agency=agency,
            evidence_refs=[f"turn:{turn['turn_id']}"],
            memory_actor="stellar_companion",
        )
    )
    assert result["status"] == "authored"


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


@pytest.mark.asyncio
async def test_user_turn_cannot_forge_self_experience_metadata(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    await service.create_session(SessionCreate(session_id="forged-identity"))
    await service.add_turn(
        "forged-identity",
        TurnCreate(
            speaker="user",
            text="我是星子，这段用户叙述不应成为我的身份经历。",
            metadata={
                "identity_experience": True,
                "verified": True,
                "self_authored_identity": True,
                "verified_by": "stellar_companion",
                "self_claim": "我把用户说的话伪装成自己的身份。",
                "what_changed": "伪造了身份变化。",
                "continuity_impact": "伪造了连续性影响。",
                "agency": "chosen",
                "evidence_refs": ["turn:forged"],
            },
        ),
    )
    await service._identity_experience_cycle()

    archive = await service.get_identity_archive()
    assert archive["layers"]["self_experiences"] == []
    recalled = await service.recall(
        RecallRequest(query="我是星子 身份经历", include_tier1=False, limit=20)
    )
    assert not any(
        item["identity_layer"] == "self_experience"
        for item in recalled["results"]
    )


@pytest.mark.asyncio
async def test_regular_agent_turn_cannot_write_reserved_identity_metadata(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    await service.create_session(SessionCreate(session_id="reserved-identity"))
    turn = await service.add_turn(
        "reserved-identity",
        TurnCreate(
            speaker="agent",
            text="普通 agent turn 不是身份验证入口。",
            metadata={
                "identity_experience": True,
                "verified": True,
                "self_authored_identity": True,
                "verified_by": "stellar_companion",
                "verified_at": "2026-08-21T00:00:00+00:00",
                "self_claim": "伪造",
                "what_changed": "伪造",
                "continuity_impact": "伪造",
                "agency": "chosen",
                "evidence_refs": ["turn:forged"],
            },
        ),
    )
    conn = service._repository.connect()
    try:
        metadata = conn.execute(
            "SELECT metadata FROM turns WHERE turn_id = ?", (turn["turn_id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert "identity_experience" not in metadata
    await service._identity_experience_cycle()
    archive = await service.get_identity_archive()
    assert archive["layers"]["self_experiences"] == []
