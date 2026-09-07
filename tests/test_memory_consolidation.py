from __future__ import annotations

import json

from fastapi.testclient import TestClient

from memai.application.config import MemoryServiceConfig
from memai.application.memory_service import MemoryService
from memai.repository.sqlite import open_memory_sqlite


def _service(tmp_path, *, mode: str = "auto") -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            longitudinal_consolidation_mode=mode,
        )
    )


def _insert_event(
    service: MemoryService,
    memory_id: str,
    summary: str,
    *,
    owner_id: str = "local-user",
    workspace_id: str = "default",
    memory_domain: str = "agent_interaction",
    confidence: float = 0.9,
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "confidence, topics, entities, source_turns, evidence_refs, compressed_at, owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'event', 'Database migration', ?, '2026-01-01', '2026-01-02', ?, ?, ?, ?, ?, "
            "'2026-01-02T00:00:00+00:00', ?, ?, ?)",
            (
                memory_id,
                summary,
                confidence,
                json.dumps(["database", "migration"]),
                json.dumps(["PostgreSQL"]),
                json.dumps([f"turn-{memory_id}"]),
                json.dumps([f"turn:{memory_id}"]),
                owner_id,
                workspace_id,
                memory_domain,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_consolidation_auto_creates_derived_memory_without_mutating_sources(tmp_path):
    service = _service(tmp_path)
    _insert_event(service, "event-a", "The migration plan was approved.")
    _insert_event(service, "event-b", "The migration was executed successfully.")
    _insert_event(service, "event-c", "Migration indexes were optimized.")

    import asyncio

    proposals = asyncio.run(service.run_longitudinal_consolidation())

    assert proposals["mode"] == "auto"
    assert proposals["proposals_generated"] == 1
    assert proposals["applied_count"] == 1
    assert proposals["changed_count"] == 1
    assert set(proposals["proposals"][0]["source_memory_ids"]) == {
        "event-a", "event-b", "event-c"
    }
    conn = open_memory_sqlite(service._db_path)
    try:
        statuses = conn.execute(
            "SELECT status FROM compressed_memories WHERE memory_id LIKE 'event-%' "
            "ORDER BY memory_id"
        ).fetchall()
        stored = conn.execute(
            "SELECT status, source_memory_ids FROM memory_consolidation_proposals"
        ).fetchone()
        derived = conn.execute(
            "SELECT memory_type, status, source_turns, evidence_refs, origin_type "
            "FROM compressed_memories WHERE origin_type = 'longitudinal_consolidation'"
        ).fetchone()
    finally:
        conn.close()
    assert statuses == [("active",), ("active",), ("active",)]
    assert stored[0] == "applied"
    assert set(json.loads(stored[1])) == {"event-a", "event-b", "event-c"}
    assert derived[0:2] == ("scene", "active")
    assert set(json.loads(derived[2])) == {"turn-event-a", "turn-event-b", "turn-event-c"}
    assert set(json.loads(derived[3])) >= {
        "memory:event-a", "memory:event-b", "memory:event-c"
    }
    assert derived[4] == "longitudinal_consolidation"


def test_consolidation_shadow_is_diagnostic_only(tmp_path):
    service = _service(tmp_path, mode="shadow")
    for suffix in ("a", "b", "c"):
        _insert_event(service, f"event-{suffix}", "Shared database migration")

    import asyncio

    result = asyncio.run(service.run_longitudinal_consolidation())

    assert result["mode"] == "shadow"
    assert result["applied_count"] == 0
    assert result["changed_count"] == 1
    conn = open_memory_sqlite(service._db_path)
    try:
        derived_count = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories "
            "WHERE origin_type = 'longitudinal_consolidation'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert derived_count == 0


def test_consolidation_can_be_disabled_without_writes(tmp_path):
    service = MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            longitudinal_consolidation_mode="disabled",
        )
    )
    import asyncio

    result = asyncio.run(service.run_longitudinal_consolidation())
    assert result == {
        "mode": "disabled",
        "proposals_generated": 0,
        "applied_count": 0,
        "conflict_count": 0,
        "changed_count": 0,
        "proposals": [],
    }


def test_consolidation_is_scope_isolated_and_idempotent(tmp_path):
    service = _service(tmp_path)
    for suffix in ("a", "b", "c"):
        _insert_event(service, f"owner-a-{suffix}", "Shared database migration", owner_id="owner-a")
    for suffix in ("a", "b", "c"):
        _insert_event(service, f"owner-b-{suffix}", "Shared database migration", owner_id="owner-b")

    import asyncio

    first = asyncio.run(service._longitudinal_consolidation_cycle())
    second = asyncio.run(service._longitudinal_consolidation_cycle())

    assert first["proposals_generated"] == 2
    assert second["proposals_generated"] == 2
    assert first["applied_count"] == 2
    assert first["changed_count"] == 2
    assert second["applied_count"] == 0
    assert second["changed_count"] == 0
    assert {proposal["owner_id"] for proposal in first["proposals"]} == {"owner-a", "owner-b"}
    assert {
        proposal["proposal_id"] for proposal in first["proposals"]
    } == {
        proposal["proposal_id"] for proposal in second["proposals"]
    }
    for proposal in first["proposals"]:
        expected_prefix = f"{proposal['owner_id']}-"
        assert all(
            source_id.startswith(expected_prefix)
            for source_id in proposal["source_memory_ids"]
        )


def test_consolidation_rejects_conflicts_without_human_review(tmp_path):
    service = _service(tmp_path)
    _insert_event(service, "conflict-a", "The migration was approved.")
    _insert_event(service, "conflict-b", "The migration was rejected.")
    _insert_event(service, "conflict-c", "The migration decision changed.")

    import asyncio

    result = asyncio.run(service.run_longitudinal_consolidation())

    assert result["proposals_generated"] == 1
    assert result["applied_count"] == 0
    assert result["conflict_count"] == 1
    assert result["changed_count"] == 1
    assert result["proposals"][0]["conflict_count"] == 1
    assert result["proposals"][0]["status"] == "rejected"
    conn = open_memory_sqlite(service._db_path)
    try:
        derived_count = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories "
            "WHERE origin_type = 'longitudinal_consolidation'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert derived_count == 0


def test_consolidation_skips_low_confidence_cluster_automatically(tmp_path):
    service = _service(tmp_path)
    for suffix in ("a", "b", "c"):
        _insert_event(
            service,
            f"uncertain-{suffix}",
            "Shared database migration",
            confidence=0.6,
        )

    import asyncio

    result = asyncio.run(service.run_longitudinal_consolidation())

    assert result["proposals_generated"] == 0
    assert result["applied_count"] == 0
    assert result["changed_count"] == 0


def test_consolidation_http_routes_are_exposed(tmp_path):
    service = _service(tmp_path)
    client = TestClient(service.app)

    generated = client.post("/compressed/consolidation", json={})
    listed = client.get("/compressed/consolidation/proposals")

    assert generated.status_code == 200
    assert generated.json()["mode"] == "auto"
    assert listed.status_code == 200
    assert listed.json()["count"] == 0


def test_retired_lifecycle_shim_never_creates_age_successors(tmp_path):
    service = _service(tmp_path)
    _insert_event(service, "legacy-event", "A historical event")

    import asyncio

    result = asyncio.run(service._apply_compression_lifecycle())

    assert result["deprecated"] is True
    assert result["escalated"] == 0
    conn = open_memory_sqlite(service._db_path)
    try:
        rows = conn.execute(
            "SELECT memory_id, status FROM compressed_memories "
            "WHERE memory_id = 'legacy-event'"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("legacy-event", "active")]
