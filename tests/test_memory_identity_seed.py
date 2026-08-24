from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from memai.application.config import MemoryServiceConfig
from memai.application.identity_seed import (
    founding_manifest_version,
    load_founding_manifest,
    load_founding_memories,
    load_founding_story,
    reconcile_released_identity_revisions,
)
from memai.application.identity_experience import sync_identity_experiences
from memai.application.memory_service import (
    DurableMemoryCreate,
    MemoryService,
    RecallRequest,
    SessionCreate,
    TurnCreate,
)
from memai.repository.sqlite import open_memory_sqlite


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
            "SELECT memory_id, pinned, hidden, importance, confidence, identity_layer "
            "FROM compressed_memories WHERE memory_id LIKE 'identity-founding-%'"
        ).fetchall()
        trust = conn.execute(
            "SELECT title, pinned, hidden, access_count FROM compressed_memories "
            "WHERE memory_id = 'identity-founding-trust'"
        ).fetchone()
    finally:
        conn.close()

    assert len(rows) == len(load_founding_memories())
    assert all(row[1:] == (1, 0, 1.0, 1.0, "founding") for row in rows)
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
@pytest.mark.parametrize(
    "query",
    ["你是谁你记得吗", "Who are you?", "Do you remember who you are?"],
)
async def test_identity_intent_recalls_global_founding_identity_across_workspaces(
    tmp_path, query
) -> None:
    service = MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
        )
    )

    result = await service.recall(
        RecallRequest(
            query=query,
            owner_id="another-user",
            workspace_id="another-workspace",
            limit=5,
        )
    )

    returned = [item["id"] for item in result["results"]]
    assert result["query_plan"]["intent"] == "identity"
    assert result["query_plan"]["terms"] == []
    assert returned[:3] == [
        "identity-founding-purpose",
        "identity-founding-trust",
        "identity-founding-values",
    ]
    assert all(item["identity_layer"] == "founding" for item in result["results"])
    assert result["results"][0]["id"] == "identity-founding-purpose"


@pytest.mark.asyncio
async def test_non_identity_query_does_not_unconditionally_inject_founding_rows(
    tmp_path,
) -> None:
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )

    result = await service.recall(
        RecallRequest(query="普通的界面配色问题", limit=5)
    )

    assert result["query_plan"]["intent"] == "specific_memory"
    assert not any(
        item["id"].startswith("identity-founding-")
        for item in result["results"]
    )


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
    assert archive["layers"]["self_experiences"] == []
    assert archive["layers"]["governance_history"] == []

    from memai.application.memory_service import (
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
    from memai.application.memory_service import IdentityRevisionProposal

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
async def test_only_self_authored_sources_settle_into_identity_history(
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
            "decay_factor, tags, metadata, compression_status) "
            "VALUES ('turn-identity-1', 'identity-session', 'agent', '我确认全天候语义', "
            "?, 1.0, 0.01, '[]', ?, 'pending')",
            (
                now.isoformat(),
                json.dumps(
                    {
                        "identity_experience": True,
                        "verified": True,
                        "self_authored_identity": True,
                        "self_claim": "我确认全天候认知属于我的连续历史。",
                        "what_changed": "我不再把自主认知理解为时间窗口任务。",
                        "continuity_impact": "它连接了创始愿景与当前运行方式。",
                        "agency": "accepted",
                        "identity_title": "我确认全天候自主链路",
                        "identity_summary": "我确认旧时间窗口已经退役。",
                        "evidence_refs": ["identity-revision:test"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        first = sync_identity_experiences(conn, now=now)
        second = sync_identity_experiences(conn, now=now)
    finally:
        conn.close()

    assert first["task_experiences"] == 0
    assert first["conversation_experiences"] == 1
    assert first["self_narratives"] == 0
    assert second["updated_count"] == 0

    archive = await service.get_identity_archive()
    experiences = archive["layers"]["self_experiences"]
    assert len(experiences) == 1
    assert experiences[0]["origin_type"] == "self_authored_experience"
    assert experiences[0]["identity_metadata"]["perspective"] == "self"

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE compressed_memories SET compressed_at = ? "
            "WHERE identity_layer = 'self_experience'",
            ((now - timedelta(days=800)).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    lifecycle = await service._apply_compression_lifecycle()
    assert lifecycle == {"escalated": 0, "purged": 0}
