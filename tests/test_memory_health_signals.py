from __future__ import annotations

import asyncio
import sys
import json
import sqlite3
import logging
import inspect
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memai.application import tier1_to_tier2_bridge as bridge_module
from memai.repository.sqlite import open_memory_sqlite
from memai.application.memory_service import (
    AgentOutboxHealthReport,
    MemoryService,
    MemoryServiceConfig,
    SessionCreate,
    Tier2CompressRequest,
    TurnCreate,
)
from memai.domain.lifecycle_policy import (
    evaluate_lifecycle_quality,
    lifecycle_age_thresholds,
)
from memai.application.maintenance_schedule import (
    claim_rule_execution,
    get_rule_state,
    record_rule_result,
)
from memai.application.maintenance import run_tier2_bridge_cycle
from memai.domain.domain import MemoryActor, MemoryDomain
from memai.application.tier1_to_tier2_bridge import (
    Tier1ToTier2Bridge,
    _write_compressed_memories_to_db,
)


def _make_service(tmp_path: Path) -> MemoryService:
    cfg = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"))
    return MemoryService(cfg)


def test_memory_service_does_not_own_a_second_tier2_bridge() -> None:
    source = inspect.getsource(MemoryService)

    assert "_bridge_to_tier2" not in source
    assert "_write_compressed_memories" not in source
    assert "_build_stable_cmem_ids" not in source


def test_memory_timing_defaults_match_weekly_consolidation_policy(tmp_path):
    config = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"))

    assert config.tier1_retention_days == 7
    assert config.tier2_batch_size == 25
    assert config.lifecycle_cadence_days == 7
    assert lifecycle_age_thresholds(config) == (
        (("event", 0), 14),
        (("scene", 1), 60),
        (("arc", 2), 180),
        (("epoch", 3), 365),
        (("epoch", 4), 90),
    )


def test_scheduled_rule_claim_uses_a_lease_to_prevent_duplicate_work(tmp_path):
    db_path = tmp_path / "mem.db"

    first = claim_rule_execution(
        db_path, rule_name="lifecycle_escalation", cadence_days=7
    )
    second = claim_rule_execution(
        db_path, rule_name="lifecycle_escalation", cadence_days=7
    )

    assert first.due is True
    assert second.due is False
    assert second.skip_reason == "in_progress"

    record_rule_result(
        db_path, rule_name="lifecycle_escalation", succeeded=True
    )
    after_success = claim_rule_execution(
        db_path, rule_name="lifecycle_escalation", cadence_days=7
    )
    assert after_success.due is False
    assert after_success.skip_reason == "cadence"


@pytest.mark.asyncio
async def test_scheduled_lifecycle_cadence_persists_across_restarts(
    tmp_path, monkeypatch
):
    first = _make_service(tmp_path)
    async def no_op():
        return 0

    async def lifecycle():
        pytest.fail("automatic lifecycle escalation must remain disabled")

    for service in (first,):
        monkeypatch.setattr(service, "_identity_experience_cycle", no_op)
        monkeypatch.setattr(service, "_tier1_decay_cycle", no_op)
        monkeypatch.setattr(service, "_tier2_bridge_cycle", no_op)
        monkeypatch.setattr(service, "_apply_compression_lifecycle", lifecycle)
        monkeypatch.setattr(service, "_purge_expired_memories", no_op)

    initial = await first._run_all_rules_internal(respect_cadence=True)
    assert initial["lifecycle_escalation"]["skipped"] == "disabled"

    restarted = _make_service(tmp_path)
    monkeypatch.setattr(restarted, "_identity_experience_cycle", no_op)
    monkeypatch.setattr(restarted, "_tier1_decay_cycle", no_op)
    monkeypatch.setattr(restarted, "_tier2_bridge_cycle", no_op)
    monkeypatch.setattr(restarted, "_apply_compression_lifecycle", lifecycle)
    monkeypatch.setattr(restarted, "_purge_expired_memories", no_op)

    throttled = await restarted._run_all_rules_internal(respect_cadence=True)
    assert throttled["lifecycle_escalation"]["skipped"] == "disabled"
    assert get_rule_state(
        restarted._db_path, "lifecycle_escalation"
    )["last_succeeded_at"] is None


@pytest.mark.asyncio
async def test_disabled_scheduled_lifecycle_does_not_call_legacy_escalation(
    tmp_path, monkeypatch
):
    service = _make_service(tmp_path)

    async def no_op():
        return 0

    async def failing_lifecycle():
        pytest.fail("automatic lifecycle escalation must remain disabled")

    monkeypatch.setattr(service, "_identity_experience_cycle", no_op)
    monkeypatch.setattr(service, "_tier1_decay_cycle", no_op)
    monkeypatch.setattr(service, "_tier2_bridge_cycle", no_op)
    monkeypatch.setattr(service, "_apply_compression_lifecycle", failing_lifecycle)
    monkeypatch.setattr(service, "_purge_expired_memories", no_op)

    first = await service._run_all_rules_internal(respect_cadence=True)
    second = await service._run_all_rules_internal(respect_cadence=True)

    assert first["lifecycle_escalation"]["skipped"] == "disabled"
    assert second["lifecycle_escalation"]["skipped"] == "disabled"
    state = get_rule_state(service._db_path, "lifecycle_escalation")
    assert state["last_succeeded_at"] is None
    assert state["last_error"] is None


@pytest.mark.asyncio
async def test_trigger_lifecycle_does_not_escalate_by_default(tmp_path, monkeypatch):
    service = _make_service(tmp_path)

    async def fail_escalation():
        pytest.fail("manual lifecycle escalation must be explicitly disabled")

    monkeypatch.setattr(service, "_apply_compression_lifecycle", fail_escalation)
    result = await service.trigger_lifecycle()

    assert result["status"] == "ok"
    assert "escalation" not in result
    assert result["purge"] == {"deleted": 0}


@pytest.mark.asyncio
async def test_public_maintenance_request_shares_background_rule_cadence(
    tmp_path, monkeypatch
):
    service = _make_service(tmp_path)

    async def no_op():
        return 0

    async def lifecycle():
        return {"escalated": 0, "purged": 0}

    monkeypatch.setattr(service, "_identity_experience_cycle", no_op)
    monkeypatch.setattr(service, "_tier1_decay_cycle", no_op)
    monkeypatch.setattr(service, "_tier2_bridge_cycle", no_op)
    monkeypatch.setattr(service, "_apply_compression_lifecycle", lifecycle)
    monkeypatch.setattr(service, "_purge_expired_memories", no_op)

    first = await service.run_all_rules()
    assert first["status"] == "accepted"
    assert first["run_id"]
    assert first["rules"] is None
    await service._maintenance_request_task

    completed = await service.rules_status()
    assert completed["status"] == "completed"
    assert completed["maintenance_run"]["rules"]["_effective_work"] == 0

    second = await service.run_all_rules()
    assert second["status"] == "accepted"
    await service._maintenance_request_task
    throttled = await service.rules_status()

    assert all(
        count == 1 for count in service._rule_run_counts.values()
    )
    rules = throttled["maintenance_run"]["rules"]
    assert rules["identity_experience"] == {"skipped": "cadence"}
    assert rules["tier1_decay"] == {"skipped": "cadence"}
    assert rules["tier2_bridge"] == {"skipped": "cadence"}
    assert rules["refresh_dormant_arcs"] == {"skipped": "cadence"}
    assert rules["lifecycle_escalation"]["skipped"] == "disabled"
    assert rules["purge_expired"] == {"skipped": "cadence"}
    assert rules["_effective_work"] == 0


@pytest.mark.asyncio
async def test_public_maintenance_request_reports_in_progress_without_duplicate_run(
    tmp_path, monkeypatch
):
    service = _make_service(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_identity_cycle():
        started.set()
        await release.wait()
        return 0

    async def no_op():
        return 0

    monkeypatch.setattr(service, "_identity_experience_cycle", blocking_identity_cycle)
    monkeypatch.setattr(service, "_tier1_decay_cycle", no_op)
    monkeypatch.setattr(service, "_tier2_bridge_cycle", no_op)
    monkeypatch.setattr(service, "_apply_compression_lifecycle", no_op)
    monkeypatch.setattr(service, "_purge_expired_memories", no_op)

    accepted = await service.run_all_rules()
    await started.wait()
    duplicate = await service.run_all_rules()

    assert accepted["status"] == "accepted"
    assert duplicate["status"] == "in_progress"
    assert duplicate["run_id"] == accepted["run_id"]

    release.set()
    await service._maintenance_request_task
    assert (await service.rules_status())["status"] == "completed"


@pytest.mark.asyncio
async def test_requested_maintenance_records_rule_failure(tmp_path, monkeypatch):
    service = _make_service(tmp_path)

    async def no_op():
        return 0

    async def failing_bridge():
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(service, "_identity_experience_cycle", no_op)
    monkeypatch.setattr(service, "_tier1_decay_cycle", no_op)
    monkeypatch.setattr(service, "_tier2_bridge_cycle", failing_bridge)
    monkeypatch.setattr(service, "_apply_compression_lifecycle", no_op)
    monkeypatch.setattr(service, "_purge_expired_memories", no_op)

    accepted = await service.run_all_rules()
    await service._maintenance_request_task
    status = await service.rules_status()

    assert accepted["status"] == "accepted"
    assert status["status"] == "failed"
    assert status["maintenance_run"]["rules"]["tier2_bridge"] == {
        "error": "bridge unavailable"
    }
    assert "tier2_bridge: bridge unavailable" in status["error"]


@pytest.mark.asyncio
async def test_memory_lifespan_cancels_requested_maintenance(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    blocker = asyncio.Event()

    monkeypatch.setattr(service, "register_with_gateway", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_check_llm_health", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_identity_experience_cycle", AsyncMock(return_value={}))

    async with service.app.router.lifespan_context(service.app):
        service._maintenance_run_status.update(status="running")
        service._maintenance_request_task = asyncio.create_task(blocker.wait())

    assert service._maintenance_request_task.cancelled()
    assert service._maintenance_run_status["status"] == "cancelled"
    assert service._maintenance_run_status["completed_at"] is not None


@pytest.mark.asyncio
async def test_overlapping_maintenance_request_is_skipped(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_identity_cycle():
        started.set()
        await release.wait()
        return 0

    async def no_op():
        return 0

    monkeypatch.setattr(
        service,
        "_identity_experience_cycle",
        blocking_identity_cycle,
    )
    monkeypatch.setattr(service, "_tier1_decay_cycle", no_op)
    monkeypatch.setattr(service, "_tier2_bridge_cycle", no_op)
    monkeypatch.setattr(service, "_apply_compression_lifecycle", no_op)
    monkeypatch.setattr(service, "_purge_expired_memories", no_op)

    active = asyncio.create_task(service._run_all_rules_internal())
    await started.wait()
    overlapping = await service._run_all_rules_internal()
    release.set()
    await active

    assert overlapping["_effective_work"] == 0
    assert all(
        result == {"skipped": "in_progress"}
        for rule_name, result in overlapping.items()
        if not rule_name.startswith("_")
    )


def test_memory_sqlite_connections_use_busy_timeout_and_wal(tmp_path):
    db_path = tmp_path / "mem.db"
    conn = open_memory_sqlite(db_path)
    try:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert busy_timeout >= 30000
    assert str(journal_mode).lower() == "wal"


@pytest.mark.asyncio
async def test_memory_health_aggregates_agent_outbox_and_degrades_on_dead_letters(
    tmp_path,
):
    service = _make_service(tmp_path)
    service._gateway_registration_healthy = True
    failure_started_at = datetime.now(timezone.utc) - timedelta(seconds=30)

    await service.report_agent_outbox_health(
        AgentOutboxHealthReport(
            session_id="agent-session",
            outbox_id="shared-outbox",
            pending_count=2,
            inflight_count=1,
            dead_letter_count=1,
            oldest_pending_at=datetime.now(timezone.utc).isoformat(),
            oldest_failure_at=failure_started_at.isoformat(),
            last_error="schema rejected",
            max_attempts=12,
        )
    )
    health = await service.health_check()

    assert health["status"] == "degraded"
    assert health["agent_outbox"]["pending_count"] == 2
    assert health["agent_outbox"]["inflight_count"] == 1
    assert health["agent_outbox"]["dead_letter_count"] == 1
    assert health["agent_outbox"]["oldest_failure_age_seconds"] >= 29.0
    assert health["agent_outbox"]["issues"] == ["shared-outbox:dead_letter"]


@pytest.mark.asyncio
async def test_memory_health_stays_local_when_gateway_registration_is_unavailable(
    tmp_path,
):
    service = _make_service(tmp_path)
    service._gateway_registration_healthy = False

    health = await service.health_check()

    assert health["status"] == "healthy"
    assert health["gateway_registration"]["healthy"] is False


@pytest.mark.asyncio
async def test_memory_health_reports_commit_revision(tmp_path):
    service = _make_service(tmp_path)
    await service.create_session(SessionCreate(session_id="revision-session", metadata={}))

    health = await service.health_check()

    assert health["database"]["commit_revision"] >= 1
    assert health["database"]["repository"]["commit_revision"] == health["database"]["commit_revision"]
    assert health["commit_revision"] == health["database"]["commit_revision"]
    assert health["database"]["repository"]["write_queue_capacity"] >= 1
    assert health["database"]["repository"]["write_batch_commits"] >= 1


@pytest.mark.asyncio
async def test_memory_health_deduplicates_reports_for_the_same_outbox(tmp_path):
    service = _make_service(tmp_path)
    service._gateway_registration_healthy = True
    base = {
        "outbox_id": "shared-outbox",
        "dead_letter_count": 0,
        "oldest_pending_at": None,
        "last_error": None,
        "max_attempts": 12,
    }

    await service.report_agent_outbox_health(
        AgentOutboxHealthReport(
            session_id="agent-a",
            pending_count=3,
            **base,
        )
    )
    await service.report_agent_outbox_health(
        AgentOutboxHealthReport(
            session_id="agent-b",
            pending_count=0,
            **base,
        )
    )
    health = await service.health_check()

    assert health["status"] == "healthy"
    assert health["agent_outbox"]["reporter_count"] == 1
    assert health["agent_outbox"]["pending_count"] == 0
    assert health["agent_outbox"]["reporters"][0]["session_id"] == "agent-b"


@pytest.mark.asyncio
async def test_memory_health_groups_transport_outboxes_and_names_dead_letters(tmp_path):
    service = _make_service(tmp_path)
    service._gateway_registration_healthy = True
    for queue_name, actor, domain, dead_letters in (
        ("api_a", "api_a", "agent_interaction", 0),
        ("companion", "stellar_companion", "companion", 1),
        ("gateway", "api_a", "agent_interaction", 1),
    ):
        await service.report_agent_outbox_health(
            AgentOutboxHealthReport(
                session_id=f"{queue_name}-session",
                outbox_id=f"{queue_name}-outbox",
                queue_name=queue_name,
                memory_actor=actor,
                memory_domain=domain,
                pending_count=2 if dead_letters else 0,
                dead_letter_count=dead_letters,
                max_attempts=12,
            )
        )

    health = await service.health_check()
    transport = health["transport_outboxes"]

    assert health["status"] == "degraded"
    assert set(transport["outboxes"]) == {"api_a", "companion", "gateway"}
    assert transport["dead_letter_count"] == 2
    assert "companion:dead_letter" in transport["issues"]
    assert "gateway:dead_letter" in transport["issues"]
    assert transport["outboxes"]["api_a"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_memory_health_accepts_legacy_outbox_report_as_api_a(tmp_path):
    service = _make_service(tmp_path)
    service._gateway_registration_healthy = True
    await service.report_agent_outbox_health(
        AgentOutboxHealthReport(
            session_id="legacy-session",
            outbox_id="legacy-outbox",
            pending_count=1,
            dead_letter_count=0,
            max_attempts=12,
        )
    )

    transport = (await service.health_check())["transport_outboxes"]
    assert transport["outboxes"]["api_a"]["reporter_count"] == 1


# ── 4-6.1: memory_active must reflect real write work, not "a rule ran" ──

def test_rule_effective_count_across_return_shapes():
    f = MemoryService._rule_effective_count
    assert f(0) == 0
    assert f(7) == 7
    assert f(-3) == 0
    assert f({"escalated": 2, "purged": 3}) == 5
    assert f({"turns_processed": 4}) == 4
    assert f({"deleted": 6}) == 6
    assert f({"error": "boom"}) == 0
    assert f(None) == 0


@pytest.mark.asyncio
async def test_noop_cycle_does_not_stamp_effective_activity(tmp_path):
    # Empty DB → every rule is a no-op (0 rows). last_run advances, but the
    # effective-activity marker must stay None so the UI does not show active.
    svc = _make_service(tmp_path)
    result = await svc._run_all_rules_internal()
    assert result["_effective_work"] == 0
    assert svc._last_effective_activity_at is None
    # last_run still advances (the rule did execute)
    assert svc._last_rule_run.get("tier1_decay") is not None


@pytest.mark.asyncio
async def test_effective_activity_stamped_when_decay_writes_rows(tmp_path):
    # Seed a live turn so tier1_decay actually updates a row → effective work.
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="s1", metadata={}))
    await svc.add_turn("s1", TurnCreate(speaker="user", text="hello", metadata={}))

    result = await svc._run_all_rules_internal()
    assert result["_effective_work"] >= 1
    assert svc._last_effective_activity_at is not None


@pytest.mark.asyncio
async def test_tier1_decay_depends_on_elapsed_time_not_run_frequency(tmp_path):
    cfg = MemoryServiceConfig(
        db_path=str(tmp_path / "mem.db"),
        decay_interval_hours=24,
        tier1_decay_rate=0.81,
    )
    svc = MemoryService(cfg)
    await svc.create_session(SessionCreate(session_id="decay", metadata={}))
    first = await svc.add_turn(
        "decay", TurnCreate(speaker="user", text="half intervals", metadata={})
    )
    second = await svc.add_turn(
        "decay", TurnCreate(speaker="user", text="full interval", metadata={})
    )
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ?, last_decay_at = ?, relevance_score = 1.0",
            (start.isoformat(), start.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    assert await svc._tier1_decay_cycle(now=start + timedelta(hours=12)) == 2
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET relevance_score = 1.0, last_decay_at = ? WHERE turn_id = ?",
            (start.isoformat(), second["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    svc = MemoryService(cfg)
    full_time = start + timedelta(hours=24)
    assert await svc._tier1_decay_cycle(now=full_time) == 2
    assert await svc._tier1_decay_cycle(now=full_time) == 0
    conn = open_memory_sqlite(svc._db_path)
    try:
        scores = dict(
            conn.execute("SELECT turn_id, relevance_score FROM turns").fetchall()
        )
    finally:
        conn.close()

    assert scores[first["turn_id"]] == pytest.approx(0.81)
    assert scores[second["turn_id"]] == pytest.approx(0.81)


@pytest.mark.asyncio
async def test_tier1_decay_ignores_compressed_and_future_anchored_turns(tmp_path):
    cfg = MemoryServiceConfig(
        db_path=str(tmp_path / "mem.db"),
        decay_interval_hours=24,
        tier1_decay_rate=0.5,
    )
    svc = MemoryService(cfg)
    await svc.create_session(SessionCreate(session_id="decay", metadata={}))
    compressed = await svc.add_turn(
        "decay", TurnCreate(speaker="user", text="compressed", metadata={})
    )
    future = await svc.add_turn(
        "decay", TurnCreate(speaker="user", text="future", metadata={})
    )
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    future_anchor = now + timedelta(hours=12)
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET relevance_score = 0.7, timestamp = ?, "
            "last_decay_at = ?, compression_status = 'compressed' WHERE turn_id = ?",
            ((now - timedelta(days=1)).isoformat(), (now - timedelta(days=1)).isoformat(), compressed["turn_id"]),
        )
        conn.execute(
            "UPDATE turns SET relevance_score = 0.8, timestamp = ?, last_decay_at = ? "
            "WHERE turn_id = ?",
            (future_anchor.isoformat(), future_anchor.isoformat(), future["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    assert await svc._tier1_decay_cycle(now=now) == 0
    conn = open_memory_sqlite(svc._db_path)
    try:
        rows = dict(
            conn.execute(
                "SELECT turn_id, relevance_score FROM turns ORDER BY turn_id"
            ).fetchall()
        )
        future_last_decay = conn.execute(
            "SELECT last_decay_at FROM turns WHERE turn_id = ?", (future["turn_id"],)
        ).fetchone()[0]
    finally:
        conn.close()

    assert rows[compressed["turn_id"]] == pytest.approx(0.7)
    assert rows[future["turn_id"]] == pytest.approx(0.8)
    assert future_last_decay == future_anchor.isoformat()


@pytest.mark.asyncio
async def test_tier1_decay_migrates_legacy_null_anchor_from_turn_timestamp(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE turns ("
            "turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, speaker TEXT NOT NULL, "
            "text TEXT NOT NULL, timestamp TEXT NOT NULL, relevance_score REAL DEFAULT 1.0, "
            "decay_factor REAL DEFAULT 0.01, tags TEXT, metadata TEXT, dedup_key TEXT, "
            "compressed_to_tier2 INTEGER DEFAULT 0)"
        )
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-turn", "legacy", "user", "old", start.isoformat(), 1.0, 0.01, "[]", "{}", None, 0),
        )
        conn.commit()
    finally:
        conn.close()

    svc = MemoryService(
        MemoryServiceConfig(
            db_path=str(db_path), decay_interval_hours=24, tier1_decay_rate=0.5
        )
    )
    conn = open_memory_sqlite(db_path)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()
        }
    finally:
        conn.close()
    assert "last_decay_at" in columns

    reference = start + timedelta(days=1)
    assert await svc._tier1_decay_cycle(now=reference) == 1
    conn = open_memory_sqlite(db_path)
    try:
        score, last_decay_at = conn.execute(
            "SELECT relevance_score, last_decay_at FROM turns WHERE turn_id = 'legacy-turn'"
        ).fetchone()
    finally:
        conn.close()
    assert score == pytest.approx(0.5)
    assert last_decay_at == reference.isoformat()


@pytest.mark.asyncio
async def test_new_turn_initializes_decay_anchor_to_creation_timestamp(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="decay", metadata={}))
    created = await svc.add_turn(
        "decay", TurnCreate(speaker="user", text="new", metadata={})
    )

    conn = open_memory_sqlite(svc._db_path)
    try:
        timestamp, last_decay_at = conn.execute(
            "SELECT timestamp, last_decay_at FROM turns WHERE turn_id = ?",
            (created["turn_id"],),
        ).fetchone()
    finally:
        conn.close()

    assert last_decay_at == timestamp == created["timestamp"]


@pytest.mark.asyncio
async def test_rules_status_exposes_effective_activity_and_llm_check_marker(tmp_path):
    svc = _make_service(tmp_path)
    status = await svc.rules_status()
    assert "effective_activity_at" in status
    assert "llm_health_checked_at" in status
    assert "llm_healthy" in status


@pytest.mark.asyncio
async def test_rules_status_exposes_memory_maintenance_due_gate(tmp_path):
    svc = _make_service(tmp_path)

    assert (await svc.rules_status())["maintenance_due"] is True
    svc._last_rule_run_monotonic = {
        name: time.monotonic()
        for name in (
            "identity_experience",
            "tier1_decay",
            "tier2_bridge",
            "refresh_dormant_arcs",
            "lifecycle_escalation",
            "purge_expired",
        )
    }
    assert (await svc.rules_status())["maintenance_due"] is False

    await svc._maintenance_lock.acquire()
    try:
        assert (await svc.rules_status())["maintenance_due"] is False
    finally:
        svc._maintenance_lock.release()


def test_rules_status_route_is_not_shadowed_by_compressed_memory_id(tmp_path):
    svc = _make_service(tmp_path)
    client = TestClient(svc.app)

    response = client.get("/compressed/rules-status")

    assert response.status_code == 200
    assert "llm_healthy" in response.json()


@pytest.mark.asyncio
async def test_llm_health_records_configured_model_when_key_missing(tmp_path):
    svc = _make_service(tmp_path)
    svc._resolve_mem_llm_client = lambda: (None, "deepseek-v4-flash")  # type: ignore[method-assign]

    ok = await svc._check_llm_health()

    assert ok is False
    assert await svc.llm_health() == {
        "healthy": False,
        "model": "deepseek-v4-flash",
        "error": "llm_client_unavailable",
    }


@pytest.mark.asyncio
async def test_llm_health_exposes_resolution_failure_reason(tmp_path):
    svc = _make_service(tmp_path)
    svc._llm_resolution_status = "api_key_unavailable"
    svc._llm_resolution_detail = "no usable credential found via DEEPSEEK_API_KEY"
    svc._resolve_mem_llm_client = lambda: (None, "deepseek-v4-flash")  # type: ignore[method-assign]

    ok = await svc._check_llm_health()

    assert ok is False
    assert await svc.llm_health() == {
        "healthy": False,
        "model": "deepseek-v4-flash",
        "error": (
            "api_key_unavailable: no usable credential found via DEEPSEEK_API_KEY"
        ),
    }


@pytest.mark.asyncio
async def test_llm_health_preserves_model_when_probe_fails(tmp_path, caplog):
    svc = _make_service(tmp_path)

    class _FailingClient:
        def complete_json(self, **kwargs):
            raise RuntimeError("remote auth failed")

    svc._resolve_mem_llm_client = lambda: (_FailingClient(), "deepseek-v4-flash")  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="memory_service"):
        ok = await svc._check_llm_health()

    assert ok is False
    assert await svc.llm_health() == {
        "healthy": False,
        "model": "deepseek-v4-flash",
        "error": "RuntimeError: remote auth failed",
    }
    assert "LLM health check failed for model=deepseek-v4-flash" in caplog.text


@pytest.mark.asyncio
async def test_rule_failure_is_logged_as_warning(tmp_path, caplog):
    svc = _make_service(tmp_path)

    async def failing_decay():
        raise RuntimeError("decay boom")

    svc._tier1_decay_cycle = failing_decay  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="memory_service"):
        result = await svc._run_all_rules_internal()

    assert result["tier1_decay"]["error"] == "decay boom"
    assert "Memory maintenance rule tier1_decay failed" in caplog.text


@pytest.mark.asyncio
async def test_lifecycle_escalation_preserves_source_turns_and_derivation_link(tmp_path):
    svc = _make_service(tmp_path)
    old_compressed_at = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, timeline_parent_id, "
            "compressed_at, compression_level, status, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-child-1",
                "event",
                "Child event",
                "Original event summary",
                old_compressed_at,
                old_compressed_at,
                0.8,
                0.9,
                json.dumps(["memory"]),
                json.dumps(["VoidCube"]),
                json.dumps(["turn-1", "turn-2"]),
                None,
                old_compressed_at,
                0,
                "active",
                1.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    async def fake_escalate_summary(**kwargs):
        return "Parent scene", "Original event summary escalated into a scene."

    svc._llm_escalate_summary = fake_escalate_summary  # type: ignore[method-assign]

    result = await svc._apply_compression_lifecycle()

    conn = open_memory_sqlite(svc._db_path)
    try:
        parent = conn.execute(
            "SELECT memory_id, derived_from_id, source_turns, memory_type, compression_level "
            "FROM compressed_memories WHERE memory_type = 'scene'",
        ).fetchone()
        child = conn.execute(
            "SELECT status, superseded_by FROM compressed_memories WHERE memory_id = ?",
            ("event-child-1",),
        ).fetchone()
    finally:
        conn.close()

    assert result["escalated"] == 1
    assert parent[1] == "event-child-1"
    assert json.loads(parent[2]) == ["turn-1", "turn-2"]
    assert parent[3] == "scene"
    assert parent[4] == 1
    assert child[0] == "superseded"
    assert child[1] == parent[0]


@pytest.mark.asyncio
async def test_lifecycle_rejects_summary_with_unsupported_identifier(tmp_path):
    svc = _make_service(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "compressed_at, compression_level, status, source_turns) "
            "VALUES ('event-hallucination', 'event', 'Release', 'Release completed', "
            "?, ?, ?, 0, 'active', '[]')",
            (old, old, old),
        )
        conn.commit()
    finally:
        conn.close()

    async def hallucinate(**kwargs):
        return "Release", "Release completed with invented ticket ZX-9999."

    svc._llm_escalate_summary = hallucinate  # type: ignore[method-assign]
    result = await svc._apply_compression_lifecycle()
    conn = open_memory_sqlite(svc._db_path)
    try:
        status, retry_count, retry_after = conn.execute(
            "SELECT status, lifecycle_retry_count, lifecycle_retry_after "
            "FROM compressed_memories WHERE memory_id = 'event-hallucination'"
        ).fetchone()
    finally:
        conn.close()
    assert result["quality_rejected"] == 1
    assert status == "active"
    assert retry_count == 1
    assert retry_after is not None


@pytest.mark.asyncio
async def test_lifecycle_rolls_back_and_closes_connection_on_derived_index_failure(
    tmp_path, monkeypatch
):
    svc = _make_service(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "topics, entities, compressed_at, compression_level, status, source_turns) "
            "VALUES ('event-transactional', 'event', 'Release', "
            "'Release completed with durable evidence.', ?, ?, ?, ?, ?, 0, 'active', '[]')",
            (old, old, json.dumps(["release"]), json.dumps(["project"]), old),
        )
        conn.commit()
    finally:
        conn.close()

    async def escalate(**kwargs):
        return "Release scene", "Release completed with durable evidence."

    def fail_rebuild(*args, **kwargs):
        raise RuntimeError("entity graph unavailable")

    svc._llm_escalate_summary = escalate  # type: ignore[method-assign]
    monkeypatch.setattr("memai.indexes.entity_graph.rebuild_entity_graph", fail_rebuild)

    with pytest.raises(RuntimeError, match="entity graph unavailable"):
        result = await svc._apply_compression_lifecycle()

    conn = open_memory_sqlite(svc._db_path)
    try:
        original = conn.execute(
            "SELECT status, superseded_by FROM compressed_memories "
            "WHERE memory_id = 'event-transactional'"
        ).fetchone()
        successor_count = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories WHERE derived_from_id = 'event-transactional'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert original == ("active", None)
    assert successor_count == 0


def test_lifecycle_quality_uses_abstraction_specific_configurable_thresholds():
    quality = evaluate_lifecycle_quality(
        source_title="API migration 2026",
        source_summary="The database migration completed after staged validation.",
        proposed_title="Migration era",
        proposed_summary="A validated transition established the next operational era for API migration 2026.",
        min_source_support=0.15,
        min_identifier_fidelity=0.8,
    )
    assert quality.passed is True


@pytest.mark.asyncio
async def test_lifecycle_quality_rejection_stops_after_configured_attempts(tmp_path):
    svc = _make_service(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "compressed_at, compression_level, status, source_turns) "
            "VALUES ('event-retry-limit', 'event', 'Release', 'Release completed', "
            "?, ?, ?, 0, 'active', '[]')",
            (old, old, old),
        )
        conn.commit()
    finally:
        conn.close()
    calls = 0

    async def hallucinate(**kwargs):
        nonlocal calls
        calls += 1
        return "Release", "Release completed with invented ticket ZX-9999."

    svc._llm_escalate_summary = hallucinate  # type: ignore[method-assign]
    for attempt in range(svc.config.lifecycle_max_quality_retries):
        await svc._apply_compression_lifecycle()
        if attempt + 1 < svc.config.lifecycle_max_quality_retries:
            conn = open_memory_sqlite(svc._db_path)
            try:
                conn.execute(
                    "UPDATE compressed_memories SET lifecycle_retry_after = ? "
                    "WHERE memory_id = 'event-retry-limit'",
                    (old,),
                )
                conn.commit()
            finally:
                conn.close()
    await svc._apply_compression_lifecycle()
    conn = open_memory_sqlite(svc._db_path)
    try:
        retry_count, retry_after = conn.execute(
            "SELECT lifecycle_retry_count, lifecycle_retry_after FROM compressed_memories "
            "WHERE memory_id = 'event-retry-limit'"
        ).fetchone()
    finally:
        conn.close()
    assert calls == svc.config.lifecycle_max_quality_retries
    assert retry_count == svc.config.lifecycle_max_quality_retries
    assert retry_after is None


@pytest.mark.asyncio
async def test_tier1_add_turn_deduplicates_explicit_idempotency_key(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="dedup", metadata={}))
    first = await svc.add_turn(
        "dedup",
        TurnCreate(
            speaker="agent",
            text="same persisted finding",
            metadata={"turn_dedup_key": "finding-1"},
        ),
    )
    second = await svc.add_turn(
        "dedup",
        TurnCreate(
            speaker="agent",
            text="same persisted finding",
            metadata={"turn_dedup_key": "finding-1"},
        ),
    )

    turns = await svc.get_session_turns("dedup")

    assert first["status"] == "created"
    assert second["status"] == "deduplicated"
    assert second["turn_id"] == first["turn_id"]
    assert turns["total"] == 1


@pytest.mark.asyncio
async def test_tier1_add_turn_derives_dedup_key_from_task_metadata(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="task-dedup", metadata={}))
    payload = TurnCreate(
        speaker="agent",
        text="Autonomous-chain finding written through gateway",
        metadata={"task_id": "task-42", "source": "autonomous_task_finding"},
    )

    first = await svc.add_turn("task-dedup", payload)
    second = await svc.add_turn("task-dedup", payload)

    turns = await svc.get_session_turns("task-dedup")

    assert first["status"] == "created"
    assert second["status"] == "deduplicated"
    assert second["turn_id"] == first["turn_id"]
    assert first["dedup_key"].startswith("auto_")
    assert turns["total"] == 1


@pytest.mark.asyncio
async def test_tier1_add_turn_auto_creates_missing_session_atomically(tmp_path):
    svc = _make_service(tmp_path)

    created = await svc.add_turn(
        "auto-session",
        TurnCreate(speaker="user", text="hello from gateway", metadata={}),
    )
    session = await svc.get_session("auto-session")
    turns = await svc.get_session_turns("auto-session")

    assert created["status"] == "created"
    assert session["session_id"] == "auto-session"
    assert session["turn_count"] == 1
    assert turns["total"] == 1
    assert turns["turns"][0]["text"] == "hello from gateway"


@pytest.mark.operational
def test_obsolete_embedding_column_is_backed_up_then_removed(tmp_path):
    svc = _make_service(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute("ALTER TABLE compressed_memories ADD COLUMN embedding TEXT")
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "compressed_at, compression_level, status, weight, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cmem-keyword-fallback",
                "event",
                "Needle architecture decision",
                "The team chose a simpler memory bridge.",
                now,
                now,
                now,
                0,
                "active",
                1.0,
                json.dumps([1.0, 0.0]),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    svc = _make_service(tmp_path)
    conn = open_memory_sqlite(svc._db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(compressed_memories)"
            ).fetchall()
        }
    finally:
        conn.close()
    backups = svc._backup_manager.list_backups()
    assert len(backups) == 1
    backup_conn = open_memory_sqlite(backups[0]["path"])
    try:
        backup_columns = {
            row[1]
            for row in backup_conn.execute(
                "PRAGMA table_info(compressed_memories)"
            ).fetchall()
        }
        stored_embedding = backup_conn.execute(
            "SELECT embedding FROM compressed_memories "
            "WHERE memory_id = 'cmem-keyword-fallback'"
        ).fetchone()[0]
    finally:
        backup_conn.close()

    assert "embedding" not in columns
    assert "embedding" in backup_columns
    assert json.loads(stored_embedding) == [1.0, 0.0]


@pytest.mark.asyncio
async def test_tier2_compress_force_oldest_handles_recent_overflow(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="overflow", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="user", text="recent turn 1", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="agent", text="recent turn 2", metadata={}))

    age_only = await svc.tier2_compress(
        Tier2CompressRequest(retention_days=30, batch_size=10, min_relevance=0.0, dry_run=True)
    )
    forced = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.0,
            dry_run=True,
            force_oldest=True,
        )
    )

    assert age_only["status"] == "no_candidates"
    assert forced["status"] == "dry_run"
    assert forced["force_oldest"] is True
    assert forced["candidate_count"] == 2


@pytest.mark.asyncio
async def test_tier2_compress_includes_expired_low_relevance_turns(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="low-rel", metadata={}))
    turn = await svc.add_turn(
        "low-rel",
        TurnCreate(speaker="user", text="old low relevance memory", metadata={}),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ?, relevance_score = ? WHERE turn_id = ?",
            (old_timestamp, 0.01, turn["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.5,
            dry_run=True,
        )
    )

    assert result["status"] == "dry_run"
    assert result["candidate_count"] == 1
    assert result["low_relevance_fallback"] is True
    assert result["sample_turn_ids"] == [turn["turn_id"]]


@pytest.mark.asyncio
async def test_tier2_bridge_cycle_uses_force_oldest_when_max_turns_exceeded(tmp_path):
    cfg = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"), tier1_max_turns=2)
    svc = MemoryService(cfg)
    await svc.create_session(SessionCreate(session_id="overflow", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="user", text="recent turn 1", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="agent", text="recent turn 2", metadata={}))
    captured = {}

    async def fake_tier2_compress(request):
        captured["force_oldest"] = request.force_oldest
        captured["batch_size"] = request.batch_size
        return {"status": "compressed", "turns_processed": 2, "events_generated": 0}

    svc.tier2_compress = fake_tier2_compress  # type: ignore[method-assign]

    processed = await svc._tier2_bridge_cycle()

    assert processed["turns_processed"] == 2
    assert processed["scope_count"] == 1
    assert processed["failed_scope_count"] == 0
    assert captured["force_oldest"] is True


@pytest.mark.asyncio
async def test_tier2_bridge_cycle_surfaces_scope_timeout_error(tmp_path):
    cfg = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"), tier1_max_turns=1)
    svc = MemoryService(cfg)
    await svc.create_session(SessionCreate(session_id="timeout", metadata={}))
    await svc.add_turn("timeout", TurnCreate(speaker="user", text="durable turn", metadata={}))

    async def fake_tier2_compress(request):
        return {
            "status": "failed",
            "turns_processed": 0,
            "events_generated": 0,
            "errors": ["The read operation timed out"],
        }

    svc.tier2_compress = fake_tier2_compress  # type: ignore[method-assign]
    result = await svc._tier2_bridge_cycle()
    status = await svc.rules_status()

    assert result["failed_scope_count"] == 1
    assert result["scopes"][0]["status"] == "failed"
    assert result["scopes"][0]["errors"] == ["The read operation timed out"]
    assert status["tier2_bridge_last_result"] == result


@pytest.mark.asyncio
async def test_tier2_bridge_timeout_cancels_scope_and_continues_with_next_scope(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(
        SessionCreate(
            session_id="slow-scope",
            memory_domain=MemoryDomain.AGENT_INTERACTION,
            metadata={},
        )
    )
    await svc.add_turn(
        "slow-scope",
        TurnCreate(
            speaker="user",
            text="slow scope turn",
            memory_domain=MemoryDomain.AGENT_INTERACTION,
            metadata={},
        ),
    )
    await svc.create_session(
        SessionCreate(
            session_id="fast-scope",
            memory_actor=MemoryActor.STELLAR_AUTO,
            memory_domain=MemoryDomain.EVOLUTION,
            metadata={},
        )
    )
    await svc.add_turn(
        "fast-scope",
        TurnCreate(
            speaker="user",
            text="fast scope turn",
            memory_actor=MemoryActor.STELLAR_AUTO,
            memory_domain=MemoryDomain.EVOLUTION,
            metadata={},
        ),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ? WHERE session_id IN (?, ?)",
            (old_timestamp, "slow-scope", "fast-scope"),
        )
        conn.commit()
    finally:
        conn.close()

    calls: list[str] = []

    async def compress(request):
        calls.append(str(request.memory_domain))
        if request.memory_domain == MemoryDomain.AGENT_INTERACTION.value:
            await asyncio.sleep(0.05)
        return {
            "status": "compressed",
            "turns_processed": 1,
            "events_generated": 1,
        }

    config = SimpleNamespace(
        tier1_retention_days=7,
        tier1_max_turns=100,
        tier1_min_relevance=0.1,
        tier2_batch_size=25,
        tier2_scope_timeout_seconds=0.01,
    )
    result = await run_tier2_bridge_cycle(
        svc._db_path,
        config,
        request_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        compress=compress,
        maintenance_actor="memory-maintenance",
        logger=logging.getLogger(__name__),
    )

    assert calls == [
        MemoryDomain.AGENT_INTERACTION.value,
        MemoryDomain.EVOLUTION.value,
    ]
    assert result["failed_scope_count"] == 1
    assert result["turns_processed"] == 1
    assert result["scopes"][0]["status"] == "failed"
    assert result["scopes"][0]["deadline_exceeded"] is True
    assert "timed out after 0.01 seconds" in result["scopes"][0]["errors"][0]
    assert result["scopes"][1]["status"] == "compressed"


@pytest.mark.asyncio
async def test_standalone_tier2_bridge_finds_recent_overflow_candidates(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="overflow", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="user", text="recent turn 1", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="agent", text="recent turn 2", metadata={}))

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=2,
    )

    candidates = bridge.find_candidate_turns()

    assert [candidate["text"] for candidate in candidates] == ["recent turn 1", "recent turn 2"]


@pytest.mark.asyncio
async def test_tier2_bridge_counts_only_retry_eligible_candidates(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="retry-eligibility", metadata={}))
    created = {}
    for name, tags in (
        ("eligible", []),
        ("backoff", []),
        ("exhausted", []),
        ("evaluation", ["evaluation"]),
    ):
        created[name] = await svc.add_turn(
            "retry-eligibility",
            TurnCreate(speaker="user", text=name, tags=tags, metadata={}),
        )

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET compression_retry_after = ? WHERE turn_id = ?",
            (future, created["backoff"]["turn_id"]),
        )
        conn.execute(
            "UPDATE turns SET compression_retry_count = 3 WHERE turn_id = ?",
            (created["exhausted"]["turn_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=2,
    )

    assert bridge._active_turn_count() == 3
    snapshot = bridge.candidate_health_snapshot()
    assert snapshot["eligible_count"] == 1
    assert snapshot["force_oldest"] is True
    assert snapshot["oldest_candidate_at"] is not None
    assert snapshot["oldest_candidate_age_seconds"] >= 0
    assert bridge.count_candidates() == 1
    assert bridge.needs_compression() is True
    assert [turn["text"] for turn in bridge.find_candidate_turns()] == ["eligible"]


@pytest.mark.asyncio
async def test_tier2_candidate_health_snapshot_reuses_commit_revision_cache(
    tmp_path, monkeypatch
):
    service = _make_service(tmp_path)
    await service.create_session(SessionCreate(session_id="cached-health", metadata={}))
    await service.add_turn(
        "cached-health",
        TurnCreate(speaker="user", text="cached turn", metadata={}),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ? WHERE session_id = ?",
            (old_timestamp, "cached-health"),
        )
        conn.commit()
    finally:
        conn.close()

    calls = 0

    def fake_candidate_health_snapshot(self):
        nonlocal calls
        calls += 1
        return {
            "eligible_count": 1,
            "oldest_candidate_at": old_timestamp,
            "oldest_candidate_age_seconds": 8 * 24 * 3600,
        }

    monkeypatch.setattr(
        bridge_module.Tier1ToTier2Bridge,
        "candidate_health_snapshot",
        fake_candidate_health_snapshot,
    )

    first = service._tier2_candidate_health_snapshot()
    second = service._tier2_candidate_health_snapshot()

    assert first["eligible_candidate_count"] == 1
    assert second["eligible_candidate_count"] == 1
    assert calls == 1


def test_tier2_pressure_trigger_uses_count_then_oldest_age(tmp_path):
    config = MemoryServiceConfig(
        db_path=str(tmp_path / "mem.db"),
        tier2_trigger_candidate_count=2,
        tier2_trigger_oldest_age_seconds=60,
    )
    service = MemoryService(config)

    assert service._tier2_pressure_trigger_reason({
        "eligible_candidate_count": 2,
        "oldest_candidate_age_seconds": 0,
    }) == "candidate_count"
    assert service._tier2_pressure_trigger_reason({
        "eligible_candidate_count": 1,
        "oldest_candidate_age_seconds": 60,
    }) == "oldest_candidate_age"
    assert service._tier2_pressure_trigger_reason({
        "eligible_candidate_count": 1,
        "oldest_candidate_age_seconds": 59,
    }) is None


@pytest.mark.asyncio
async def test_tier2_bridge_repeated_failures_degrade_health(tmp_path):
    config = MemoryServiceConfig(
        db_path=str(tmp_path / "mem.db"),
        tier1_max_turns=1,
        tier2_bridge_failure_degraded_after=2,
    )
    service = MemoryService(config)
    service._gateway_registration_healthy = True
    await service.create_session(SessionCreate(session_id="degraded", metadata={}))
    await service.add_turn(
        "degraded", TurnCreate(speaker="user", text="durable turn", metadata={})
    )

    async def fail_compression(request):
        return {
            "status": "failed",
            "turns_processed": 0,
            "events_generated": 0,
            "errors": ["bridge unavailable"],
        }

    service.tier2_compress = fail_compression  # type: ignore[method-assign]
    service._tier2_bridge_last_trigger_reason = "candidate_count"
    await service._tier2_bridge_cycle()
    assert service._tier2_bridge_state == "failed"
    await service._tier2_bridge_cycle()

    health = await service.health_check()
    bridge_health = health["maintenance"]["tier2_bridge"]
    assert health["status"] == "degraded"
    assert bridge_health["state"] == "degraded"
    assert bridge_health["consecutive_failures"] == 2
    assert bridge_health["last_failure_reason"] == "bridge unavailable"
    assert bridge_health["last_trigger_reason"] == "candidate_count"
    assert bridge_health["eligible_candidate_count"] == 1


@pytest.mark.asyncio
async def test_tier2_candidate_health_normalizes_mixed_iso_offsets(tmp_path):
    service = _make_service(tmp_path)
    await service.create_session(SessionCreate(session_id="utc", metadata={}))
    await service.create_session(
        SessionCreate(
            session_id="offset",
            memory_actor=MemoryActor.STELLAR_AUTO,
            memory_domain=MemoryDomain.EVOLUTION,
            metadata={},
        )
    )
    first = await service.add_turn(
        "utc", TurnCreate(speaker="user", text="UTC candidate", metadata={})
    )
    second = await service.add_turn(
        "offset",
        TurnCreate(
            speaker="user",
            text="offset candidate",
            memory_actor=MemoryActor.STELLAR_AUTO,
            memory_domain=MemoryDomain.EVOLUTION,
            metadata={},
        ),
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ? WHERE turn_id = ?",
            ("2026-01-01T00:30:00Z", first["turn_id"]),
        )
        conn.execute(
            "UPDATE turns SET timestamp = ? WHERE turn_id = ?",
            ("2026-01-01T01:00:00+02:00", second["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    snapshot = service._tier2_candidate_health_snapshot()

    assert snapshot["eligible_candidate_count"] == 2
    assert snapshot["oldest_candidate_at"] == "2026-01-01T01:00:00+02:00"
    assert snapshot["oldest_candidate_age_seconds"] > 0


@pytest.mark.asyncio
async def test_tier2_bridge_does_not_schedule_when_all_turns_are_in_backoff(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="all-backoff", metadata={}))
    turn = await svc.add_turn(
        "all-backoff",
        TurnCreate(speaker="user", text="temporarily blocked", metadata={}),
    )
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET compression_retry_after = ? WHERE turn_id = ?",
            (future, turn["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        max_turns=1,
    )

    assert bridge._active_turn_count() == 1
    assert bridge.count_candidates() == 0
    assert bridge.needs_compression() is False
    assert bridge.find_candidate_turns() == []


@pytest.mark.asyncio
async def test_standalone_tier2_bridge_finds_expired_low_relevance_candidates(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="low-rel", metadata={}))
    turn = await svc.add_turn(
        "low-rel",
        TurnCreate(speaker="user", text="old low relevance bridge memory", metadata={}),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ?, relevance_score = ? WHERE turn_id = ?",
            (old_timestamp, 0.01, turn["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.5,
        max_turns=100,
    )

    candidates = bridge.find_candidate_turns()

    assert [candidate["turn_id"] for candidate in candidates] == [turn["turn_id"]]


@pytest.mark.asyncio
async def test_tier2_compress_keeps_turns_uncompressed_when_no_events_generated(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="empty-events", metadata={}))
    await svc.add_turn("empty-events", TurnCreate(speaker="user", text="plain chatter", metadata={}))

    class _EmptyPipeline:
        def ingest(self, turns):
            return SimpleNamespace(events=[], scenes=[], arcs=[], epochs=[], profile_memories=[])

    svc._build_compression_pipeline = lambda: _EmptyPipeline()  # type: ignore[method-assign]

    result = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.0,
            force_oldest=True,
        )
    )

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compression_status FROM turns WHERE session_id = ?",
            ("empty-events",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result["status"] == "no_events_generated"
    assert result["turns_processed"] == 0
    assert result["compression_degraded"] is True
    assert result["compression_method"] == "heuristic"
    assert compressed == "pending"
    assert archive_count == 0


@pytest.mark.asyncio
async def test_standalone_bridge_keeps_turns_uncompressed_when_no_events_generated(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="empty-events", metadata={}))
    await svc.add_turn("empty-events", TurnCreate(speaker="user", text="plain chatter", metadata={}))

    class _EmptyPipeline:
        def ingest(self, turns):
            return SimpleNamespace(events=[], scenes=[], arcs=[], epochs=[], profile_memories=[])

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=1,
    )
    bridge._build_pipeline = lambda: _EmptyPipeline()  # type: ignore[method-assign]

    result = bridge.run_cycle()

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compression_status FROM turns WHERE session_id = ?",
            ("empty-events",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result.turns_processed == 0
    assert result.candidate_count == 1
    assert compressed == "pending"
    assert archive_count == 0


@pytest.mark.asyncio
@pytest.mark.operational
async def test_compression_quality_gate_audits_incomplete_turn_coverage_without_rejecting(tmp_path):
    svc = _make_service(tmp_path)
    svc._llm_healthy = True
    await svc.create_session(SessionCreate(session_id="quality-reject", metadata={}))
    first = await svc.add_turn(
        "quality-reject",
        TurnCreate(speaker="user", text="A durable architecture decision was made.", metadata={}),
    )
    await svc.add_turn(
        "quality-reject",
        TurnCreate(speaker="agent", text="The implementation plan was also confirmed.", metadata={}),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id="event-partial",
        parent_ids=[],
        event_kind="decision",
        title="Architecture decision",
        summary="Architecture decision.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[first["turn_id"]],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _PartialPipeline:
        def ingest(self, turns):
            return SimpleNamespace(
                events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[]
            )

    svc._build_compression_pipeline = lambda: _PartialPipeline()  # type: ignore[method-assign]
    result = await svc.tier2_compress(
        Tier2CompressRequest(min_relevance=0.0, force_oldest=True)
    )

    conn = open_memory_sqlite(svc._db_path)
    try:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compression_status != 'compressed'"
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
        audit_status, event_coverage, failed_checks = conn.execute(
            "SELECT status, event_coverage, failed_checks "
            "FROM compression_quality_audit"
        ).fetchone()
    finally:
        conn.close()

    assert result["status"] == "compressed"
    assert result["turns_processed"] == 2
    assert result["quality_evidence"]["event_coverage"] == pytest.approx(0.5)
    assert result["quality_evidence"]["failed_checks"] == []
    assert active_count == 0
    assert archive_count == 2
    assert audit_status == "passed"
    assert event_coverage == pytest.approx(0.5)
    assert json.loads(failed_checks) == []


def test_quality_rejection_stops_retrying_after_three_attempts(tmp_path):
    svc = _make_service(tmp_path)
    conn = open_memory_sqlite(svc._db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, created_at, updated_at, metadata) "
            "VALUES ('retry-session', ?, ?, '{}')",
            (now, now),
        )
        conn.execute(
            "INSERT INTO turns (turn_id, session_id, speaker, text, timestamp) "
            "VALUES ('retry-turn', 'retry-session', 'user', 'source', ?)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()
    bridge = Tier1ToTier2Bridge(db_path=svc._db_path)
    turn = {
        "turn_id": "retry-turn", "owner_id": "local-user",
        "workspace_id": "default", "memory_domain": "agent_interaction",
    }
    for _ in range(3):
        bridge._record_quality_rejection([turn])
    conn = open_memory_sqlite(svc._db_path)
    try:
        state = conn.execute(
            "SELECT compression_retry_count, compression_retry_after, compression_status "
            "FROM turns WHERE turn_id = 'retry-turn'"
        ).fetchone()
    finally:
        conn.close()
    assert state == (3, None, "quality_quarantined")


@pytest.mark.asyncio
@pytest.mark.operational
async def test_compression_quality_gate_passes_with_complete_reciprocal_backlinks(tmp_path):
    svc = _make_service(tmp_path)
    svc._llm_healthy = True
    await svc.create_session(SessionCreate(session_id="quality-pass", metadata={}))
    first = await svc.add_turn(
        "quality-pass",
        TurnCreate(speaker="user", text="A durable architecture decision was made.", metadata={}),
    )
    second = await svc.add_turn(
        "quality-pass",
        TurnCreate(speaker="agent", text="The implementation plan was confirmed.", metadata={}),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id="event-complete",
        parent_ids=[],
        event_kind="decision",
        title="Architecture decision",
        summary="Architecture decision and implementation plan confirmed.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[first["turn_id"], second["turn_id"]],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _CompletePipeline:
        def ingest(self, turns):
            return SimpleNamespace(
                events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[]
            )

    svc._build_compression_pipeline = lambda: _CompletePipeline()  # type: ignore[method-assign]
    result = await svc.tier2_compress(
        Tier2CompressRequest(min_relevance=0.0, force_oldest=True)
    )
    repeated = await svc.tier2_compress(
        Tier2CompressRequest(min_relevance=0.0, force_oldest=True)
    )

    conn = open_memory_sqlite(svc._db_path)
    try:
        archive_rows = conn.execute(
            "SELECT turn_id, event_ids FROM turns_archive ORDER BY turn_id"
        ).fetchall()
        audit_status = conn.execute(
            "SELECT status FROM compression_quality_audit"
        ).fetchone()[0]
    finally:
        conn.close()

    assert result["status"] == "compressed"
    assert result["turns_processed"] == 2
    assert result["quality_evidence"]["passed"] is True
    assert result["quality_evidence"]["event_coverage"] == pytest.approx(1.0)
    assert result["quality_evidence"]["backlink_completeness"] == pytest.approx(1.0)
    assert repeated["status"] == "no_candidates"
    assert len(archive_rows) == 2
    assert all(json.loads(event_ids) for _, event_ids in archive_rows)
    assert audit_status == "passed"


@pytest.mark.asyncio
@pytest.mark.operational
async def test_compression_quality_gate_rejects_cross_session_event(tmp_path):
    svc = _make_service(tmp_path)
    svc._llm_healthy = True
    for session_id, text in (
        ("session-a", "Project Alpha decision."),
        ("session-b", "Project Beta decision."),
    ):
        await svc.create_session(SessionCreate(session_id=session_id, metadata={}))
        await svc.add_turn(
            session_id,
            TurnCreate(speaker="user", text=text, metadata={}),
        )

    turns = []
    conn = open_memory_sqlite(svc._db_path)
    try:
        turns = [
            {
                "turn_id": row[0],
                "session_id": row[1],
                "speaker": row[2],
                "text": row[3],
                "timestamp": row[4],
                "relevance_score": row[5],
                "owner_id": row[6],
                "workspace_id": row[7],
                "memory_domain": row[8],
            }
            for row in conn.execute(
                "SELECT turn_id, session_id, speaker, text, timestamp, "
                "relevance_score, owner_id, workspace_id, memory_domain "
                "FROM turns ORDER BY session_id"
            ).fetchall()
        ]
    finally:
        conn.close()

    event = SimpleNamespace(
        id="event-cross-session",
        title="Combined projects",
        summary="Project Alpha and Project Beta decisions.",
        event_kind="decision",
        timespan_start=datetime.now(timezone.utc),
        timespan_end=datetime.now(timezone.utc),
        importance=0.8,
        confidence=0.9,
        topics=["projects"],
        entities=["Alpha", "Beta"],
        source_turns=[turn["turn_id"] for turn in turns],
    )
    event.to_dict = lambda: {"id": event.id, "source_turns": event.source_turns}

    class _CrossSessionPipeline:
        def ingest(self, _turns):
            return SimpleNamespace(
                events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[]
            )

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=-1,
        batch_size=25,
        min_relevance=0.0,
        pipeline_factory=_CrossSessionPipeline,
    )
    output = bridge._build_tier2_output(turns)
    evidence = bridge._evaluate_quality(turns, output)

    assert evidence["passed"] is False
    assert "session_boundary" in evidence["failed_checks"]
    assert evidence["session_boundary_violations"]


@pytest.mark.asyncio
@pytest.mark.operational
async def test_compression_quality_gate_rejects_semantically_unsupported_summary(tmp_path):
    svc = MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "semantic-quality.db"),
            tier2_max_compression_ratio=10.0,
        )
    )
    svc._llm_healthy = True
    await svc.create_session(SessionCreate(session_id="quality-semantic", metadata={}))
    turn = await svc.add_turn(
        "quality-semantic",
        TurnCreate(
            speaker="user",
            text="Deployment must not use SQLite build 2024.",
            metadata={},
        ),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id="event-fabricated",
        parent_ids=[],
        event_kind="decision",
        title="Lunar orchard outcome",
        summary="The lunar orchard approved PostgreSQL release 9999.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["fabricated"],
        entities=["PostgreSQL"],
        source_turns=[turn["turn_id"]],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _FabricatedPipeline:
        def ingest(self, turns):
            return SimpleNamespace(
                events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[]
            )

    svc._build_compression_pipeline = lambda: _FabricatedPipeline()  # type: ignore[method-assign]
    result = await svc.tier2_compress(
        Tier2CompressRequest(min_relevance=0.0, force_oldest=True)
    )

    conn = open_memory_sqlite(svc._db_path)
    try:
        audit = conn.execute(
            "SELECT status, source_support, identifier_fidelity, "
            "polarity_consistency, unsupported_identifiers "
            "FROM compression_quality_audit"
        ).fetchone()
    finally:
        conn.close()

    assert result["status"] == "quality_rejected"
    assert result["quality_evidence"]["failed_checks"] == ["no_valid_events"]
    assert audit[0] == "rejected"
    assert audit[1] < svc.config.tier2_min_source_support
    assert audit[2:4] == (0.0, 0.0)
    assert json.loads(audit[4]) == ["9999"]


@pytest.mark.asyncio
@pytest.mark.operational
async def test_quality_gate_filters_one_bad_event_and_commits_valid_event(tmp_path):
    svc = _make_service(tmp_path)
    svc.config.tier2_max_compression_ratio = 10.0
    svc._llm_healthy = True
    await svc.create_session(SessionCreate(session_id="quality-filter", metadata={}))
    turn = await svc.add_turn(
        "quality-filter",
        TurnCreate(
            speaker="user",
            text="Deployment decision: use PostgreSQL for the memory database.",
            metadata={},
        ),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    def make_event(event_id, title, summary):
        event = SimpleNamespace(
            id=event_id,
            parent_ids=[],
            event_kind="decision",
            title=title,
            summary=summary,
            timespan_start=now_dt,
            timespan_end=now_dt,
            importance=0.8,
            confidence=0.9,
            topics=["memory"],
            entities=["PostgreSQL"],
            source_turns=[turn["turn_id"]],
        )
        event.to_dict = lambda: {
            "id": event.id,
            "title": event.title,
            "summary": event.summary,
            "source_turns": list(event.source_turns),
        }
        return event

    valid = make_event(
        "event-valid",
        "Deployment decision",
        "Use PostgreSQL for the memory database.",
    )
    invalid = make_event(
        "event-invalid",
        "Fabricated release",
        "The lunar orchard approved release 9999.",
    )
    scene = SimpleNamespace(
        id="scene-mixed",
        child_ids=[valid.id, invalid.id],
        key_events=[valid.id, invalid.id],
        evidence_refs=[valid.id, invalid.id],
    )
    scene.to_dict = lambda: {"id": scene.id, "child_ids": list(scene.child_ids)}
    arc = SimpleNamespace(
        id="arc-mixed",
        child_ids=[scene.id],
        evidence_refs=[scene.id],
    )
    arc.to_dict = lambda: {"id": arc.id, "child_ids": list(arc.child_ids)}
    epoch = SimpleNamespace(
        id="epoch-mixed",
        child_ids=[arc.id],
        evidence_refs=[arc.id],
    )
    epoch.to_dict = lambda: {"id": epoch.id, "child_ids": list(epoch.child_ids)}

    class _MixedPipeline:
        def ingest(self, turns):
            return SimpleNamespace(
                events=[valid, invalid],
                scenes=[scene],
                arcs=[arc],
                epochs=[epoch],
                profile_memories=[],
            )

    svc._build_compression_pipeline = lambda: _MixedPipeline()  # type: ignore[method-assign]
    result = await svc.tier2_compress(
        Tier2CompressRequest(min_relevance=0.0, force_oldest=True)
    )

    conn = open_memory_sqlite(svc._db_path)
    try:
        memories = conn.execute(
            "SELECT summary FROM compressed_memories WHERE memory_domain = 'agent_interaction'"
        ).fetchall()
    finally:
        conn.close()

    assert result["status"] == "compressed"
    assert result["events_generated"] == 1
    assert result["scenes_generated"] == 0
    assert result["arcs_generated"] == 0
    assert result["epochs_generated"] == 0
    assert result["quality_evidence"]["valid_event_fraction"] == pytest.approx(0.5)
    assert result["quality_evidence"]["rejected_event_reasons"]["event-invalid"]
    summaries = [summary for (summary,) in memories]
    assert "Use PostgreSQL for the memory database." in summaries
    assert all("lunar orchard" not in summary for summary in summaries)


@pytest.mark.asyncio
async def test_compression_quality_gate_can_reject_degraded_pipeline(tmp_path):
    cfg = MemoryServiceConfig(
        db_path=str(tmp_path / "mem.db"),
        tier2_max_degraded_fraction=0.0,
    )
    svc = MemoryService(cfg)
    await svc.create_session(SessionCreate(session_id="quality-degraded", metadata={}))
    turn = await svc.add_turn(
        "quality-degraded",
        TurnCreate(speaker="user", text="A durable architecture decision was made.", metadata={}),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id="event-degraded",
        parent_ids=[],
        event_kind="decision",
        title="Architecture decision",
        summary="Architecture decision.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[turn["turn_id"]],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _DegradedPipeline:
        def ingest(self, turns):
            return SimpleNamespace(
                events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[]
            )

    svc._build_compression_pipeline = lambda: _DegradedPipeline()  # type: ignore[method-assign]
    result = await svc.tier2_compress(
        Tier2CompressRequest(min_relevance=0.0, force_oldest=True)
    )

    assert result["status"] == "quality_rejected"
    assert result["quality_evidence"]["degraded_fraction"] == pytest.approx(1.0)
    assert result["quality_evidence"]["failed_checks"] == ["degraded_fraction"]


@pytest.mark.asyncio
async def test_tier2_compress_rolls_back_archive_when_compressed_write_fails(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    svc._llm_healthy = True
    await svc.create_session(SessionCreate(session_id="rollback", metadata={}))
    await svc.add_turn(
        "rollback",
        TurnCreate(
            speaker="user",
            text="decision memory with enough source detail for a concise durable summary",
            metadata={},
        ),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    event = SimpleNamespace(
        id="event-random",
        parent_ids=[],
        event_kind="decision",
        title="Decision",
        summary="A durable decision.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _Pipeline:
        def ingest(self, turns):
            event.source_turns = [turns[0].turn_id]
            return SimpleNamespace(events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[])

    def fail_write(conn, pipeline_result, now, **kwargs):
        raise RuntimeError("compressed write failed")

    svc._build_compression_pipeline = lambda: _Pipeline()  # type: ignore[method-assign]
    monkeypatch.setattr(bridge_module, "_write_compressed_memories_to_db", fail_write)

    result = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.0,
            force_oldest=True,
        )
    )

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compression_status FROM turns WHERE session_id = ?",
            ("rollback",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result["status"] == "failed"
    assert result["turns_processed"] == 0
    assert result["errors"] == ["compressed write failed"]
    assert compressed == "pending"
    assert archive_count == 0
    conn = open_memory_sqlite(svc._db_path)
    try:
        audit_status = conn.execute(
            "SELECT status FROM compression_quality_audit"
        ).fetchone()[0]
    finally:
        conn.close()
    assert audit_status == "commit_failed"


@pytest.mark.asyncio
async def test_standalone_bridge_rolls_back_archive_when_compressed_write_fails(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="rollback", metadata={}))
    await svc.add_turn(
        "rollback",
        TurnCreate(
            speaker="user",
            text="decision memory with enough source detail for a concise durable summary",
            metadata={},
        ),
    )
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    event = SimpleNamespace(
        id="event-random",
        parent_ids=[],
        event_kind="decision",
        title="Decision",
        summary="A durable decision.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _Pipeline:
        def ingest(self, turns):
            event.source_turns = [turns[0].turn_id]
            return SimpleNamespace(events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[])

    def fail_write(conn, pipeline_result, now, **kwargs):
        raise RuntimeError("compressed write failed")

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=1,
    )
    bridge._build_pipeline = lambda: _Pipeline()  # type: ignore[method-assign]
    monkeypatch.setattr(bridge_module, "_write_compressed_memories_to_db", fail_write)

    result = bridge.run_cycle()

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compression_status FROM turns WHERE session_id = ?",
            ("rollback",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result.status == "failed"
    assert result.turns_processed == 0
    assert result.errors == ["compressed write failed"]
    assert compressed == "pending"
    assert archive_count == 0


def test_compressed_memory_write_uses_stable_ids_for_duplicate_events(tmp_path):
    svc = _make_service(tmp_path)
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    def result_with_event(event_id: str):
        event = SimpleNamespace(
            id=event_id,
            parent_ids=[],
            event_kind="decision",
            title="Stable decision",
            summary="The same source turn produced the same event.",
            timespan_start=now_dt,
            timespan_end=now_dt,
            importance=0.8,
            confidence=0.9,
            topics=["memory"],
            entities=["VoidCube"],
            source_turns=["turn-1"],
        )
        scene = SimpleNamespace(
            id=f"scene_for_{event_id}",
            parent_ids=[],
            child_ids=[event_id],
            evidence_refs=[event_id],
            title="Stable scene",
            summary="The same event group produced the same scene.",
            timespan_start=now_dt,
            timespan_end=now_dt,
            importance=0.7,
            confidence=0.8,
            topics=["memory"],
            entities=["VoidCube"],
        )
        return SimpleNamespace(events=[event], scenes=[scene], arcs=[], epochs=[])

    conn = sqlite3.connect(str(svc._db_path))
    try:
        _write_compressed_memories_to_db(
            conn,
            result_with_event("event_random_a"),
            now_dt.isoformat(),
        )
        _write_compressed_memories_to_db(
            conn,
            result_with_event("event_random_b"),
            now_dt.isoformat(),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT memory_id, memory_type, event_kind FROM compressed_memories "
            "WHERE memory_id LIKE 'event\\_%' ESCAPE '\\' "
            "OR memory_id LIKE 'scene\\_%' ESCAPE '\\' ORDER BY memory_type"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert [row[1] for row in rows] == ["event", "scene"]
    assert rows[0][0].startswith("event_")
    assert rows[1][0].startswith("scene_")
    assert [row[2] for row in rows] == ["decision", "decision"]


def test_bridge_sets_created_at_and_propagates_event_kind_to_all_levels(tmp_path):
    svc = _make_service(tmp_path)
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    def item(item_id, **values):
        defaults = {
            "id": item_id, "parent_ids": [], "child_ids": [], "evidence_refs": [],
            "title": item_id, "summary": f"summary {item_id}",
            "timespan_start": now_dt, "timespan_end": now_dt,
            "importance": 0.8, "confidence": 0.9,
            "topics": ["memory"], "entities": ["VoidCube"],
        }
        defaults.update(values)
        return SimpleNamespace(**defaults)

    event = item(
        "event-1", event_kind="decision", source_turns=["turn-1"]
    )
    scene = item("scene-1", child_ids=[event.id], evidence_refs=[event.id])
    arc = item("arc-1", child_ids=[scene.id], evidence_refs=[scene.id])
    epoch = item("epoch-1", child_ids=[arc.id], evidence_refs=[arc.id])
    profile = SimpleNamespace(
        id="profile-1",
        memory_kind="fact",
        subject="project",
        predicate="uses",
        value="stable references",
        summary="The project uses stable references.",
        confidence=0.9,
        certainty_state="observed",
        status="active",
        valid_from=now_dt,
        valid_to=None,
        evidence_refs=["turn-1", scene.id],
        source_turns=["turn-1"],
        parent_timeline_refs=[scene.id],
        supersedes=[],
        conflict_refs=[],
        created_at=now_dt,
    )
    result = SimpleNamespace(
        events=[event],
        scenes=[scene],
        arcs=[arc],
        epochs=[epoch],
        profile_memories=[profile],
    )
    conn = open_memory_sqlite(svc._db_path)
    try:
        _write_compressed_memories_to_db(conn, result, now_dt.isoformat())
        conn.commit()
        rows = conn.execute(
            "SELECT memory_type, event_kind, created_at FROM compressed_memories "
            "WHERE memory_id NOT LIKE 'identity-founding-%' ORDER BY compression_level"
        ).fetchall()
        profile_count = conn.execute(
            "SELECT COUNT(*) FROM profile_memories WHERE memory_id = 'profile-1'"
        ).fetchone()[0]
        stable_scene_id = conn.execute(
            "SELECT memory_id FROM compressed_memories WHERE memory_type = 'scene'"
        ).fetchone()[0]
        event_parent_id = conn.execute(
            "SELECT timeline_parent_id FROM compressed_memories "
            "WHERE memory_type = 'event' AND memory_id NOT LIKE 'identity-founding-%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert [(row[0], row[1]) for row in rows] == [
        ("event", "decision"), ("scene", "decision"),
        ("arc", "decision"), ("epoch", "decision"),
    ]
    assert profile_count == 0
    assert event_parent_id == stable_scene_id
    assert all(row[2] == now_dt.isoformat() for row in rows)
