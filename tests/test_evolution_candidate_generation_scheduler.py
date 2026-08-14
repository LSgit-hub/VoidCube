from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from systems.evolution_candidate_generation import (
    CandidateLearningReference,
    EvolutionCandidateGenerationRequest,
    JsonEvolutionCandidateGenerationRepository,
)
from systems.evolution_evaluation import MetricTarget
from systems.supervisor.evolution_candidate_generation_scheduler import (
    EvolutionCandidateGenerationScheduler,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _request(*, objective: str = "Improve stream rendering correctness."):
    return EvolutionCandidateGenerationRequest.create(
        mapping_key=f"mapping-{objective}",
        mapping_source="learning_evidence_structure_projection_v1",
        target_body_slot_id="slot-B",
        objective=objective,
        improvement_hypothesis="A focused change will preserve complete output.",
        baseline_commit="a" * 40,
        source_learning_refs=(
            CandidateLearningReference(
                learning_id=f"learning-{objective}",
                completed_at=NOW - timedelta(days=1),
                relevance=0.95,
                title="Stream rendering evidence",
                target_paths=("agent/stream_handler.py",),
            ),
        ),
        knowledge_ids=("knowledge-" + "b" * 64,),
        allowed_paths=("agent/stream_handler.py",),
        forbidden_patterns=("**/credential*",),
        max_files_changed=1,
        test_commands=("python -m pytest tests/test_stream_handler.py -q",),
        command_timeout_seconds=300,
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
    )


def _quiet_observation():
    return {
        "observation_input": {
            "activity": {"active_sessions": 0},
            "user_chain_signal": {"is_quiet": True},
        }
    }


def _scheduler(
    repository,
    execute,
    *,
    enabled=False,
    observation=None,
    active_body=False,
    clock=None,
):
    async def load_observation():
        value = observation if observation is not None else _quiet_observation()
        return value

    return EvolutionCandidateGenerationScheduler(
        repository=repository,
        execute=execute,
        automatic_enabled=lambda: enabled,
        load_runtime_observation=load_observation,
        has_active_body_task=lambda: active_body,
        clock=clock or (lambda: NOW),
        lease_owner="test-scheduler",
    )


@pytest.mark.asyncio
async def test_shadow_previews_candidate_without_claiming_when_automatic_is_disabled(
    tmp_path,
):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    calls = []

    async def execute(request_id, *, lease_owner):
        calls.append((request_id, lease_owner))

    scheduler = _scheduler(repository, execute)

    automatic = await scheduler.trigger(mode="automatic")
    shadow = await scheduler.trigger(mode="shadow")

    assert automatic["reason"] == "automatic_disabled"
    assert shadow == {
        "status": "shadow_ready",
        "mode": "shadow",
        "would_start": True,
        "request_id": request.request_id,
        "automatic_enabled": False,
    }
    assert repository.get_current_state(request.request_id).status == "pending"
    assert calls == []


@pytest.mark.asyncio
async def test_manual_trigger_starts_one_background_cycle_without_waiting(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(request_id, *, lease_owner):
        state = repository.claim_authoring(
            request_id,
            lease_owner=lease_owner,
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=30),
        )
        started.set()
        await release.wait()
        return SimpleNamespace(state=state)

    scheduler = _scheduler(repository, execute)

    result = await asyncio.wait_for(
        scheduler.trigger(mode="manual"),
        timeout=0.2,
    )
    await started.wait()
    duplicate = await scheduler.trigger(mode="manual")

    assert result["status"] == "started"
    assert duplicate["reason"] == "active_candidate_cycle"
    assert scheduler.status()["background_task_running"] is True

    release.set()
    await scheduler._background_task
    assert scheduler.status()["latest_run"]["result_state"] == "authoring"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observation", "active_body", "reason"),
    [
        (
            {
                "observation_input": {
                    "activity": {"active_sessions": 1},
                    "user_chain_signal": {"is_quiet": False},
                }
            },
            False,
            "user_service_active",
        ),
        (_quiet_observation(), True, "active_body_task"),
    ],
)
async def test_runtime_gates_block_manual_candidate_generation(
    tmp_path,
    observation,
    active_body,
    reason,
):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)

    async def execute(request_id, *, lease_owner):
        raise AssertionError((request_id, lease_owner))

    scheduler = _scheduler(
        repository,
        execute,
        observation=observation,
        active_body=active_body,
    )

    result = await scheduler.trigger(mode="manual")

    assert result["status"] == "skipped"
    assert result["reason"] == reason
    assert repository.get_current_state(request.request_id).status == "pending"


@pytest.mark.asyncio
async def test_cooldown_blocks_selected_request_but_not_another_pending_request(
    tmp_path,
):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    cooling_request = _request(objective="Improve first stream path.")
    ready_request = _request(objective="Improve second stream path.")
    repository.register(cooling_request, requested_at=NOW)
    repository.register(ready_request, requested_at=NOW + timedelta(seconds=1))
    claimed = repository.claim_authoring(
        cooling_request.request_id,
        lease_owner="failed-worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    repository.mark_failure(
        cooling_request.request_id,
        attempt_id=claimed.attempt_id,
        status="blocked",
        error_code="environment_unavailable",
        error_reason="The required environment is unavailable.",
        completed_at=NOW,
        cooldown_until=NOW + timedelta(hours=1),
    )
    release = asyncio.Event()

    async def execute(request_id, *, lease_owner):
        state = repository.claim_authoring(
            request_id,
            lease_owner=lease_owner,
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=30),
        )
        await release.wait()
        return SimpleNamespace(state=state)

    scheduler = _scheduler(repository, execute)

    cooling = await scheduler.trigger(
        mode="shadow",
        request_id=cooling_request.request_id,
    )
    ready = await scheduler.trigger(mode="manual")

    assert cooling["reason"] == "candidate_cooldown"
    assert ready["request_id"] == ready_request.request_id
    release.set()
    await scheduler._background_task


@pytest.mark.asyncio
async def test_runtime_observation_failure_is_fail_closed_and_sanitized(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)

    async def load_observation():
        raise RuntimeError("sensitive gateway detail")

    scheduler = EvolutionCandidateGenerationScheduler(
        repository=repository,
        execute=lambda *args, **kwargs: None,
        automatic_enabled=lambda: True,
        load_runtime_observation=load_observation,
        has_active_body_task=lambda: False,
        clock=lambda: NOW,
        lease_owner="test-scheduler",
    )

    result = await scheduler.trigger(mode="automatic")

    assert result["reason"] == "runtime_observation_unavailable"
    assert result["error_code"] == "RuntimeError"
    assert "sensitive" not in str(result)


@pytest.mark.asyncio
async def test_restarted_scheduler_respects_lease_then_retries_after_expiry(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    first = repository.claim_authoring(
        request.request_id,
        lease_owner="stopped-supervisor",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    clock = [NOW]

    async def execute(request_id, *, lease_owner):
        state = repository.claim_authoring(
            request_id,
            lease_owner=lease_owner,
            claimed_at=clock[0],
            lease_expires_at=clock[0] + timedelta(minutes=30),
        )
        return SimpleNamespace(state=state)

    before_expiry = _scheduler(
        repository,
        execute,
        clock=lambda: clock[0],
    )

    blocked = await before_expiry.trigger(mode="shadow")

    assert blocked["reason"] == "active_candidate_cycle"
    assert repository.get_current_state(request.request_id) == first

    clock[0] = NOW + timedelta(minutes=5)
    restarted = _scheduler(
        repository,
        execute,
        clock=lambda: clock[0],
    )
    started = await restarted.trigger(mode="manual")
    await restarted._background_task
    retried = repository.get_current_state(request.request_id)

    assert started["status"] == "started"
    assert retried.status == "authoring"
    assert retried.attempt_number == first.attempt_number + 1
    assert retried.attempt_id != first.attempt_id


@pytest.mark.asyncio
async def test_same_candidate_becomes_retryable_only_after_cooldown(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    claimed = repository.claim_authoring(
        request.request_id,
        lease_owner="failed-worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    repository.mark_failure(
        request.request_id,
        attempt_id=claimed.attempt_id,
        status="blocked",
        error_code="environment_unavailable",
        error_reason="The required environment is unavailable.",
        completed_at=NOW,
        cooldown_until=NOW + timedelta(hours=1),
    )
    clock = [NOW]

    async def execute(request_id, *, lease_owner):
        state = repository.claim_authoring(
            request_id,
            lease_owner=lease_owner,
            claimed_at=clock[0],
            lease_expires_at=clock[0] + timedelta(minutes=30),
        )
        return SimpleNamespace(state=state)

    scheduler = _scheduler(
        repository,
        execute,
        clock=lambda: clock[0],
    )

    cooling = await scheduler.trigger(mode="shadow")
    clock[0] = NOW + timedelta(hours=1)
    started = await scheduler.trigger(mode="manual")
    await scheduler._background_task
    retried = repository.get_current_state(request.request_id)

    assert cooling["reason"] == "candidate_cooldown"
    assert started["status"] == "started"
    assert retried.attempt_number == claimed.attempt_number + 1
