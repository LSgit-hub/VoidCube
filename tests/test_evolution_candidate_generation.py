from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from systems.evolution_candidate_generation import (
    CandidateLearningReference,
    EvolutionCandidateGenerationRecordCorrupted,
    EvolutionCandidateGenerationRequest,
    EvolutionCandidateGenerationTransitionRejected,
    JsonEvolutionCandidateGenerationRepository,
)
from systems.evolution_evaluation import MetricTarget


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _request(**overrides: object) -> EvolutionCandidateGenerationRequest:
    values: dict[str, object] = {
        "mapping_key": "mapping-stream",
        "mapping_source": "learning_evidence_structure_projection_v1",
        "target_body_slot_id": "slot-B",
        "objective": "Improve stream rendering correctness.",
        "improvement_hypothesis": (
            "A focused stream handler change will preserve complete output."
        ),
        "baseline_commit": "a" * 40,
        "source_learning_refs": (
            CandidateLearningReference(
                learning_id="learn-stream",
                completed_at=NOW - timedelta(days=1),
                relevance=0.95,
                title="Stream rendering evidence",
                target_paths=("agent/stream_handler.py",),
            ),
        ),
        "knowledge_ids": ("knowledge-" + "b" * 64,),
        "allowed_paths": ("agent/stream_handler.py",),
        "forbidden_patterns": ("**/credential*",),
        "max_files_changed": 1,
        "test_commands": ("python -m pytest tests/test_stream_handler.py -q",),
        "command_timeout_seconds": 300,
        "target_metrics": (MetricTarget(metric="correctness", objective="increase"),),
    }
    values.update(overrides)
    return EvolutionCandidateGenerationRequest.create(**values)


def test_candidate_request_is_deterministic_and_rejects_tampering():
    first = _request()
    second = _request()

    assert first == second
    assert first.request_id.endswith(first.content_hash)

    payload = first.model_dump(mode="json")
    payload["objective"] = "A forged objective"
    with pytest.raises(ValidationError, match="content_hash does not match"):
        EvolutionCandidateGenerationRequest.model_validate(payload)


def test_candidate_request_rejects_learning_paths_outside_authoring_scope():
    with pytest.raises(
        ValidationError,
        match="learning target paths must be included in allowed_paths",
    ):
        _request(allowed_paths=("agent/display.py",))


def test_register_is_idempotent_and_persists_initial_state(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()

    first = repository.register(request, requested_at=NOW)
    replay = repository.register(request, requested_at=NOW + timedelta(hours=1))

    assert replay == first
    assert first.status == "pending"
    assert first.revision == 0
    assert repository.get_request(request.request_id) == request
    assert repository.get_current_state(request.request_id) == first
    assert repository.list_request_ids() == (request.request_id,)


def test_register_repairs_missing_current_pointer_after_interrupted_write(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    initial = repository.register(request, requested_at=NOW)
    repository.index_path.unlink()

    recovered = repository.register(
        request,
        requested_at=NOW + timedelta(hours=1),
    )

    assert recovered == initial
    assert repository.get_current_state(request.request_id) == initial


def test_authoring_claim_has_one_winner_and_unique_retry_identity(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)

    first = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    blocked = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-2",
        claimed_at=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=6),
    )
    retry = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-2",
        claimed_at=NOW + timedelta(minutes=5),
        lease_expires_at=NOW + timedelta(minutes=10),
    )

    assert first is not None
    assert blocked is None
    assert retry is not None
    assert first.attempt_number == 1
    assert retry.attempt_number == 2
    assert retry.attempt_id != first.attempt_id
    assert retry.authoring_task_id != first.authoring_task_id


def test_concurrent_repository_instances_only_publish_one_authoring_claim(tmp_path):
    root = tmp_path / "cycles"
    request = _request()
    JsonEvolutionCandidateGenerationRepository(root).register(
        request,
        requested_at=NOW,
    )

    def claim(worker: str):
        return JsonEvolutionCandidateGenerationRepository(root).claim_authoring(
            request.request_id,
            lease_owner=worker,
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(claim, ("worker-1", "worker-2")))

    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    assert winners[0].attempt_number == 1


def test_evaluation_lease_recovers_without_reauthoring_candidate(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    authoring = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert authoring is not None and authoring.attempt_id is not None

    evaluating = repository.begin_evaluation(
        request.request_id,
        attempt_id=authoring.attempt_id,
        authoring_result_id="evolution-authoring-result-" + "c" * 64,
        lease_owner="worker-1",
        started_at=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=6),
    )
    recovered = repository.claim_evaluation(
        request.request_id,
        lease_owner="worker-2",
        claimed_at=NOW + timedelta(minutes=6),
        lease_expires_at=NOW + timedelta(minutes=11),
    )

    assert evaluating.status == "evaluating"
    assert recovered is not None
    assert recovered.attempt_id == authoring.attempt_id
    assert recovered.authoring_task_id == authoring.authoring_task_id
    assert recovered.authoring_result_id == evaluating.authoring_result_id
    assert recovered.lease_owner == "worker-2"


def test_expired_authoring_with_persisted_result_recovers_into_evaluation(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    authoring = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert authoring is not None and authoring.attempt_id is not None

    recovered = repository.recover_evaluation(
        request.request_id,
        attempt_id=authoring.attempt_id,
        authoring_result_id="evolution-authoring-result-" + "c" * 64,
        lease_owner="worker-2",
        resumed_at=NOW + timedelta(minutes=5),
        lease_expires_at=NOW + timedelta(minutes=10),
    )

    assert recovered.status == "evaluating"
    assert recovered.attempt_id == authoring.attempt_id
    assert recovered.authoring_task_id == authoring.authoring_task_id
    assert recovered.lease_owner == "worker-2"


def test_active_authoring_lease_cannot_be_taken_over_with_persisted_result(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    authoring = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert authoring is not None and authoring.attempt_id is not None

    with pytest.raises(
        EvolutionCandidateGenerationTransitionRejected,
        match="has not expired",
    ):
        repository.recover_evaluation(
            request.request_id,
            attempt_id=authoring.attempt_id,
            authoring_result_id="evolution-authoring-result-" + "c" * 64,
            lease_owner="worker-2",
            resumed_at=NOW + timedelta(minutes=4),
            lease_expires_at=NOW + timedelta(minutes=9),
        )


def test_active_owner_can_renew_lease_without_changing_attempt(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    claimed = repository.claim_authoring(
        request.request_id,
        lease_owner="worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None and claimed.attempt_id is not None

    renewed = repository.renew_lease(
        request.request_id,
        attempt_id=claimed.attempt_id,
        lease_owner="worker",
        renewed_at=NOW + timedelta(minutes=4),
        lease_expires_at=NOW + timedelta(minutes=9),
    )

    assert renewed.status == "authoring"
    assert renewed.attempt_id == claimed.attempt_id
    assert renewed.authoring_task_id == claimed.authoring_task_id
    assert renewed.lease_expires_at == NOW + timedelta(minutes=9)


def test_authorized_cycle_retains_append_only_transition_history(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    initial = repository.register(request, requested_at=NOW)
    authoring = repository.claim_authoring(
        request.request_id,
        lease_owner="worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert authoring is not None and authoring.attempt_id is not None
    evaluating = repository.begin_evaluation(
        request.request_id,
        attempt_id=authoring.attempt_id,
        authoring_result_id="evolution-authoring-result-" + "c" * 64,
        lease_owner="worker",
        started_at=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=6),
    )
    authorized = repository.mark_authorized(
        request.request_id,
        attempt_id=authoring.attempt_id,
        experiment_result_id="experiment-result-" + "d" * 64,
        completed_at=NOW + timedelta(minutes=2),
    )

    assert authorized.status == "authorized"
    assert authorized.lease_owner is None
    assert [state.status for state in repository.state_history(request.request_id)] == [
        "pending",
        "authoring",
        "evaluating",
        "authorized",
    ]
    assert initial.state_id != authoring.state_id != evaluating.state_id


def test_failure_cooldown_prevents_retry_and_stale_attempt_cannot_advance(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    repository.register(request, requested_at=NOW)
    first = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert first is not None and first.attempt_id is not None
    repository.mark_failure(
        request.request_id,
        attempt_id=first.attempt_id,
        status="blocked",
        error_code="environment_unavailable",
        error_reason="The native environment probe failed.",
        completed_at=NOW + timedelta(minutes=1),
        cooldown_until=NOW + timedelta(hours=1),
    )

    assert (
        repository.claim_authoring(
            request.request_id,
            lease_owner="worker-2",
            claimed_at=NOW + timedelta(minutes=30),
            lease_expires_at=NOW + timedelta(minutes=35),
        )
        is None
    )
    retry = repository.claim_authoring(
        request.request_id,
        lease_owner="worker-2",
        claimed_at=NOW + timedelta(hours=1),
        lease_expires_at=NOW + timedelta(hours=1, minutes=5),
    )
    assert retry is not None and retry.attempt_id != first.attempt_id

    with pytest.raises(
        EvolutionCandidateGenerationTransitionRejected,
        match="does not own",
    ):
        repository.begin_evaluation(
            request.request_id,
            attempt_id=first.attempt_id,
            authoring_result_id="evolution-authoring-result-" + "e" * 64,
            lease_owner="worker-2",
            started_at=NOW + timedelta(hours=1, minutes=1),
            lease_expires_at=NOW + timedelta(hours=1, minutes=6),
        )


def test_corrupted_current_state_is_not_silently_replaced(tmp_path):
    repository = JsonEvolutionCandidateGenerationRepository(tmp_path / "cycles")
    request = _request()
    current = repository.register(request, requested_at=NOW)
    state_path = repository.states_root / f"{current.state_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["status"] = "authorized"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvolutionCandidateGenerationRecordCorrupted):
        repository.get_current_state(request.request_id)
