from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from memai.application.config import MemoryServiceConfig
from memai.application.memory_service import MemoryService, RecallRequest
from memai.repository.sqlite import open_memory_sqlite


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


def _insert_compressed_version(
    service: MemoryService,
    *,
    memory_id: str,
    summary: str,
    created_at: datetime,
    status: str = "active",
    superseded_by: str | None = None,
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = created_at.isoformat()
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, compressed_at, "
            "compression_level, status, superseded_by, weight, event_kind, "
            "owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, 'event', ?, ?, ?, ?, 0.8, 0.9, '[\"数据库\"]', '[]', '[]', ?, "
            "0, ?, ?, 0.8, 'decision', 'local-user', 'default', 'agent_interaction', ?)",
            (
                memory_id,
                f"数据库迁移决策 {memory_id}",
                summary,
                stamp,
                stamp,
                stamp,
                status,
                superseded_by,
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_as_of_returns_version_current_at_snapshot_time(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    t1 = now - timedelta(days=20)
    t2 = now - timedelta(days=10)

    _insert_compressed_version(
        service,
        memory_id="mem-v1",
        summary="旧版：数据库迁移前保留完整备份。",
        created_at=t1,
        status="superseded",
        superseded_by="mem-v2",
    )
    _insert_compressed_version(
        service,
        memory_id="mem-v2",
        summary="新版：数据库迁移前执行完整性检查并保留回滚备份。",
        created_at=t2,
    )

    # Before v2 existed → v1 was current.
    before = await service.recall(
        RecallRequest(query="数据库迁移决策", as_of=(t1 + timedelta(days=1)).isoformat(), limit=5)
    )
    before_ids = [item["id"] for item in before["results"]]
    assert "mem-v1" in before_ids
    assert "mem-v2" not in before_ids

    # After v2 was created → v2 is current, v1 is superseded.
    after = await service.recall(
        RecallRequest(query="数据库迁移决策", as_of=(t2 + timedelta(days=1)).isoformat(), limit=5)
    )
    after_ids = [item["id"] for item in after["results"]]
    assert "mem-v2" in after_ids
    assert "mem-v1" not in after_ids

    # Before v1 existed → neither.
    none = await service.recall(
        RecallRequest(query="数据库迁移决策", as_of=(t1 - timedelta(days=1)).isoformat(), limit=5)
    )
    assert "mem-v1" not in [item["id"] for item in none["results"]]
    assert "mem-v2" not in [item["id"] for item in none["results"]]

    # Default (no as_of) → only the current active version.
    current = await service.recall(RecallRequest(query="数据库迁移决策", limit=5))
    current_ids = [item["id"] for item in current["results"]]
    assert "mem-v2" in current_ids
    assert "mem-v1" not in current_ids


@pytest.mark.asyncio
async def test_as_of_plumbed_into_query_plan_for_audit(tmp_path):
    service = _service(tmp_path)
    result = await service.recall(
        RecallRequest(query="数据库迁移", as_of="2026-01-15T00:00:00Z", limit=3)
    )
    assert result["query_plan"]["as_of"] == "2026-01-15T00:00:00Z"


@pytest.mark.asyncio
async def test_compression_quality_dashboard_returns_audits(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compression_quality_audit "
            "(audit_id, evaluated_at, status, candidate_count, event_count, "
            "covered_turn_count, event_coverage, backlinked_event_count, "
            "backlink_completeness, source_chars, event_summary_chars, "
            "compression_ratio, degraded_event_count, degraded_fraction, "
            "source_supported_event_count, source_support, identifier_fidelity, "
            "polarity_consistency, unsupported_identifiers, thresholds, "
            "failed_checks, sample_turn_ids) "
            "VALUES (?, ?, 'accepted', 10, 5, 8, 0.8, 5, 1.0, 4000, 800, "
            "0.2, 0, 0.0, 5, 1.0, 1.0, 1.0, '[]', '{}', '[]', '[]')",
            ("audit-1", now),
        )
        conn.commit()
    finally:
        conn.close()

    result = await service.compression_quality()

    assert result["count"] >= 1
    assert result["accepted"] >= 1
    assert result["audits"][0]["status"] == "accepted"
    assert result["audits"][0]["compression_ratio"] == 0.2
