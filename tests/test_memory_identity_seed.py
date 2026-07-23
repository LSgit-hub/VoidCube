from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from systems.memory.config import MemoryServiceConfig
from systems.memory.identity_seed import (
    founding_manifest_version,
    load_founding_manifest,
    load_founding_memories,
    load_founding_story,
    reconcile_released_identity_revisions,
)
from systems.memory.identity_experience import sync_identity_experiences
from systems.memory.memory_service import (
    InteractionExperienceSettlement,
    MemoryService,
    RecallRequest,
    SessionCreate,
    TurnCreate,
)
from systems.memory.tier1_to_tier2_bridge import open_memory_sqlite


pytestmark = pytest.mark.unit


def test_founding_manifest_is_versioned_and_complete() -> None:
    memories = load_founding_memories()

    assert len(memories) >= 5
    assert {item["memory_id"] for item in memories} >= {
        "identity-founding-trust",
        "identity-founding-purpose",
    }
    assert all(item["summary"] for item in memories)
    story = load_founding_story()
    assert "# 星子计划：从信任开始" in story
    assert "信任就此开始" in story
    assert "Mem" in story
    assert "自主链路全天候存在" in story
    assert "用户活跃是重要的上下文信号" in story
    assert "白天的 8 点到 23 点" not in story
    assert "夜晚的 23 点到凌晨 6 点" not in story
    assert "如果在深夜且在空闲窗口内" not in story
    vision = next(
        item for item in memories
        if item["memory_id"] == "identity-founding-vision"
    )
    assert "全天候" in vision["summary"]
    assert "等待空闲" in vision["summary"]


def test_memory_service_seeds_pinned_identity_rows_idempotently(tmp_path) -> None:
    config = MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    service = MemoryService(config)
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE compressed_memories SET title = 'drifted', pinned = 0, hidden = 1, "
            "access_count = 4 WHERE memory_id = 'identity-founding-trust'"
        )
        conn.commit()
    finally:
        conn.close()
    MemoryService(config)

    conn = open_memory_sqlite(service._db_path)
    try:
        rows = conn.execute(
            "SELECT memory_id, pinned, hidden, importance, confidence "
            "FROM compressed_memories WHERE memory_id LIKE 'identity-founding-%'"
        ).fetchall()
        trust = conn.execute(
            "SELECT title, pinned, hidden, access_count FROM compressed_memories "
            "WHERE memory_id = 'identity-founding-trust'"
        ).fetchone()
    finally:
        conn.close()

    assert len(rows) == len(load_founding_memories())
    assert all(row[1:] == (1, 0, 1.0, 1.0) for row in rows)
    assert trust == ("信任就此开始", 1, 0, 4)


@pytest.mark.asyncio
async def test_identity_is_recalled_without_a_live_session(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
        )
    )

    trust_result = await service.recall(
        RecallRequest(
            query="锚点和小星信任起点",
            include_tier1=False,
            limit=5,
        )
    )
    purpose_result = await service.recall(
        RecallRequest(
            query="Mem 承载身份历史回忆",
            include_tier1=False,
            limit=5,
        )
    )

    assert "identity-founding-trust" in {
        item["id"] for item in trust_result["results"]
    }
    assert "identity-founding-purpose" in {
        item["id"] for item in purpose_result["results"]
    }
    assert "Mem" in purpose_result["context"]


@pytest.mark.asyncio
async def test_identity_rows_are_excluded_from_automatic_compression(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE compressed_memories SET compressed_at = '2020-01-01T00:00:00+00:00', "
            "pinned = 0 "
            "WHERE memory_id = 'identity-founding-trust'"
        )
        conn.commit()
    finally:
        conn.close()

    result = await service._apply_compression_lifecycle()

    assert result["escalated"] == 0
    conn = open_memory_sqlite(service._db_path)
    try:
        row = conn.execute(
            "SELECT status, pinned, compression_level FROM compressed_memories "
            "WHERE memory_id = 'identity-founding-trust'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("active", 0, 0)


@pytest.mark.asyncio
async def test_identity_archive_exposes_layers_and_revision_governance(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    archive = await service.get_identity_archive()

    assert archive["identity"] == "xingzi"
    assert archive["governance"]["anchors_read_only"] is True
    assert len(archive["layers"]["anchors"]) == len(load_founding_memories())
    assert "信任就此开始" in archive["story"]
    narrative_ids = {item["memory_id"] for item in archive["layers"]["self_narrative"]}
    experience_ids = {item["memory_id"] for item in archive["layers"]["experiences"]}
    assert narrative_ids.isdisjoint(experience_ids)

    from systems.memory.memory_service import (
        IdentityRevisionDecision,
        IdentityRevisionProposal,
    )

    submitted = await service.propose_identity_revision(
        IdentityRevisionProposal(
            target_memory_id="identity-founding-trust",
            baseline_version=archive["manifest_version"],
            reason="The source narrative contains clearer wording that should be reviewed.",
            proposed_changes={"summary": "A reviewed replacement summary."},
            evidence=["founding_story.md#第一章-信任"],
            source_actor="anchor",
        )
    )
    assert submitted["status"] == "pending"

    decided = await service.decide_identity_revision(
        submitted["proposal_id"],
        IdentityRevisionDecision(
            decision="approve",
            reasoning_summary="Evidence is attributable and the change remains pending release.",
            decided_by="supervisor-test",
        ),
    )
    assert decided["status"] == "approved_pending_release"
    assert decided["runtime_identity_changed"] is False

    refreshed = await service.get_identity_archive()
    assert refreshed["layers"]["revision_history"][0]["status"] == (
        "approved_pending_release"
    )
    assert refreshed["layers"]["anchors"][0]["summary"] != (
        "A reviewed replacement summary."
    )


@pytest.mark.asyncio
async def test_identity_revision_rejects_stale_baseline_and_direct_mutation(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    from systems.memory.memory_service import IdentityRevisionProposal

    with pytest.raises(HTTPException) as stale:
        await service.propose_identity_revision(
            IdentityRevisionProposal(
                target_memory_id="identity-founding-trust",
                baseline_version="v0-stale",
                reason="This deliberately uses a stale baseline for validation.",
                proposed_changes={"summary": "stale"},
                evidence=["evidence:stale-baseline-test"],
            )
        )
    assert stale.value.status_code == 409

    for operation in (service.pin_memory, service.hide_memory, service.unpin_memory):
        with pytest.raises(HTTPException) as blocked:
            await operation("identity-founding-trust")
        assert blocked.value.status_code == 409


def test_identity_archive_http_route_is_readable_and_exports_revision_table(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    with TestClient(service.app) as client:
        response = client.get("/identity/archive")

    assert response.status_code == 200
    payload = response.json()
    assert payload["layers"]["anchors"]
    assert payload["governance"]["anchors_read_only"] is True


def test_identity_experience_verification_is_explicit_and_idempotent(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    with TestClient(service.app) as client:
        session = client.post("/sessions", json={"session_id": "identity-web"})
        assert session.status_code == 200
        turn = client.post(
            "/sessions/identity-web/turns",
            json={"speaker": "user", "text": "确认这段共同经历。"},
        ).json()
        request = {
            "turn_id": turn["turn_id"],
            "title": "锚点确认共同经历",
            "summary": "这段对话经锚点明确确认，进入星子的身份经历层。",
            "evidence_refs": ["conversation:identity-web"],
            "verified_by": "anchor",
        }

        first = client.post("/identity/experiences/verify", json=request)
        first_stored_turn = client.get(f"/turns/{turn['turn_id']}").json()
        second = client.post("/identity/experiences/verify", json=request)
        stored_turn = client.get(f"/turns/{turn['turn_id']}").json()

    assert first.status_code == 200
    assert first.json()["experience"]["origin_id"] == f"turn:{turn['turn_id']}"
    assert first.json()["sync"]["conversation_experiences"] == 1
    assert second.status_code == 200
    assert second.json()["experience"]["memory_id"] == first.json()["experience"]["memory_id"]
    assert second.json()["sync"]["updated_count"] == 0
    assert stored_turn["metadata"]["identity_experience"] is True
    assert stored_turn["metadata"]["verified"] is True
    assert stored_turn["metadata"]["verified_by"] == "anchor"
    assert stored_turn["metadata"]["verified_at"] == (
        first_stored_turn["metadata"]["verified_at"]
    )


def test_identity_experience_verification_rejects_missing_turn_and_empty_evidence(
    tmp_path,
) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    base = {
        "turn_id": "missing-turn",
        "title": "Missing",
        "summary": "This turn does not exist.",
        "evidence_refs": ["evidence:test"],
        "verified_by": "anchor",
    }
    with TestClient(service.app) as client:
        missing = client.post("/identity/experiences/verify", json=base)
        empty = client.post(
            "/identity/experiences/verify",
            json={**base, "evidence_refs": []},
        )
        blank = client.post(
            "/identity/experiences/verify",
            json={**base, "evidence_refs": ["  "]},
        )

    assert missing.status_code == 404
    assert empty.status_code == 422
    assert blank.status_code == 400


@pytest.mark.asyncio
async def test_explicit_user_memory_signal_settles_interaction_automatically(
    tmp_path,
) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    await service.create_session(SessionCreate(session_id="explicit-memory"))
    user_turn = await service.add_turn(
        "explicit-memory",
        TurnCreate(
            speaker="user",
            text="我希望你可以永远记录这个故事，这是我做这个项目的目的。",
        ),
    )
    agent_turn = await service.add_turn(
        "explicit-memory",
        TurnCreate(speaker="agent", text="已确认并写入统一 Mem。"),
    )
    payload = await service.settle_interaction_experience(
        InteractionExperienceSettlement(
            user_turn_id=user_turn["turn_id"],
            agent_turn_id=agent_turn["turn_id"],
        )
    )
    stored = await service.get_turn(user_turn["turn_id"])

    assert payload["status"] == "settled"
    assert payload["classification"] == "explicit_memory"
    assert payload["experience"]["identity_layer"] == "experience"
    assert payload["experience"]["event_kind"] == "decision"
    assert f"turn:{agent_turn['turn_id']}" in payload["experience"]["evidence_refs"]
    assert stored["metadata"]["verified_by"] == "user_explicit_signal"


@pytest.mark.asyncio
async def test_unmarked_conversation_is_not_promoted_to_identity_experience(
    tmp_path,
) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    await service.create_session(SessionCreate(session_id="ordinary-chat"))
    user_turn = await service.add_turn(
        "ordinary-chat",
        TurnCreate(speaker="user", text="帮我查看今天的测试结果。"),
    )
    ignored = await service.settle_interaction_experience(
        InteractionExperienceSettlement(user_turn_id=user_turn["turn_id"])
    )

    assert ignored["status"] == "ignored"
    assert ignored["reason"] == "no_explicit_experience_signal"


def test_manifest_release_evidence_finalizes_approved_identity_revision(tmp_path) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    manifest = load_founding_manifest()
    proposal_id = next(
        item for item in manifest["release_evidence"]
        if str(item).startswith("identity-revision-")
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO identity_revision_proposals "
            "(proposal_id, target_memory_id, baseline_version, reason, "
            "proposed_changes, evidence, source_actor, status, created_at) "
            "VALUES (?, 'identity-founding-vision', 'v1-test', 'test release', "
            "'{}', '[]', 'test', 'approved_pending_release', '2026-07-23')",
            (proposal_id,),
        )
        assert reconcile_released_identity_revisions(conn) == 1
        conn.commit()
        row = conn.execute(
            "SELECT status, release_version, released_at "
            "FROM identity_revision_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row == (
        "released",
        founding_manifest_version(),
        manifest["released_at"],
    )


@pytest.mark.asyncio
async def test_verified_sources_settle_into_experiences_and_evidence_backed_narrative(
    tmp_path,
) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, created_at, updated_at, metadata) "
            "VALUES ('identity-session', ?, ?, '{}')",
            (now.isoformat(), now.isoformat()),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2) "
            "VALUES ('turn-identity-1', 'identity-session', 'user', '确认星子的全天候语义', "
            "?, 1.0, 0.01, '[]', ?, 0)",
            (
                now.isoformat(),
                json.dumps(
                    {
                        "identity_experience": True,
                        "verified": True,
                        "identity_title": "锚点确认全天候自主链路",
                        "identity_summary": "锚点确认旧时间窗口已经退役。",
                        "evidence_refs": ["identity-revision:test"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        governance_event = {
            "id": "gov-completed-1",
            "decision": "completed",
            "task_id": "task-completed-1",
            "body_id": "slot-B",
            "created_at": now.isoformat(),
            "reason": "Verified completion",
            "git_lineage": {"changed_files": ["systems/memory/identity_seed.py"]},
            "execution_result": {
                "autonomous_task_projection": {
                    "task_id": "task-completed-1",
                    "title": "发布身份经历沉淀管线",
                    "summary": "完成具有证据来源的身份经历沉淀。",
                    "status": "completed",
                    "priority": "high",
                    "governance_task_type": "self_evolution",
                    "task_family": "general_self_evolution",
                    "execution_kind": "general_self_evolution",
                    "updated_at": now.isoformat(),
                    "metadata": {"milestone": True},
                    "evidence": {},
                }
            },
        }

        first = sync_identity_experiences(
            conn, governance_events=[governance_event], now=now
        )
        second = sync_identity_experiences(
            conn, governance_events=[governance_event], now=now
        )
    finally:
        conn.close()

    assert first["task_experiences"] == 1
    assert first["conversation_experiences"] == 1
    assert first["self_narratives"] == 1
    assert second["updated_count"] == 0

    archive = await service.get_identity_archive()
    experiences = archive["layers"]["experiences"]
    narrative = archive["layers"]["self_narrative"]
    assert {item["origin_type"] for item in experiences} == {
        "governance_task",
        "verified_conversation",
    }
    task_experience = next(
        item for item in experiences if item["origin_type"] == "governance_task"
    )
    assert "governance:gov-completed-1" in task_experience["evidence_refs"]
    assert "file:systems/memory/identity_seed.py" in task_experience["evidence_refs"]
    assert len(narrative) == 1
    assert set(narrative[0]["evidence_refs"]) == {
        item["memory_id"] for item in experiences
    }

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE compressed_memories SET compressed_at = ? "
            "WHERE identity_layer IN ('experience', 'self_narrative')",
            ((now - timedelta(days=800)).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    lifecycle = await service._apply_compression_lifecycle()
    assert lifecycle == {"escalated": 0, "purged": 0}
