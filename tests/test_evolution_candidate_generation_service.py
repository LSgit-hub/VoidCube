from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from systems.evolution_authoring import (
    EvolutionAuthoringResult,
    JsonEvolutionAuthoringRepository,
    candidate_ref_for_task,
)
from systems.evolution_candidate_generation import (
    CandidateLearningReference,
    EvolutionCandidateGenerationRequest,
    JsonEvolutionCandidateGenerationRepository,
)
from systems.evolution_evaluation import (
    ExperimentSpec,
    JsonEvaluationRepository,
    MetricTarget,
    select_benchmark_platforms,
)
from systems.self_cognition import SelfCognitionSnapshot
from systems.supervisor.evolution_candidate_generation_service import (
    EvolutionCandidateGenerationService,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
NOW = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
RESULT_ID = "experiment-result-" + "e" * 64


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    target = repository / "agent" / "demo.py"
    target.parent.mkdir()
    target.write_text("VALUE = 'baseline'\n", encoding="utf-8")
    _git("add", "agent/demo.py", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    baseline = _git("rev-parse", "HEAD", cwd=repository)
    _git("switch", "--detach", baseline, cwd=repository)
    target.write_text("VALUE = 'candidate'\n", encoding="utf-8")
    _git("add", "agent/demo.py", cwd=repository)
    _git("commit", "-m", "candidate", cwd=repository)
    candidate = _git("rev-parse", "HEAD", cwd=repository)
    _git("switch", "master", cwd=repository)
    return repository, baseline, candidate


def _request(baseline: str) -> EvolutionCandidateGenerationRequest:
    return EvolutionCandidateGenerationRequest.create(
        mapping_key="mapping-demo",
        mapping_source="test-projection",
        target_body_slot_id="slot-B",
        objective="Improve demo behavior.",
        improvement_hypothesis="The candidate keeps native behavior compatible.",
        baseline_commit=baseline,
        source_learning_refs=(
            CandidateLearningReference(
                learning_id="learning-demo",
                completed_at=NOW - timedelta(days=1),
                relevance=0.9,
                title="Demo evidence",
                target_paths=("agent/demo.py",),
            ),
        ),
        allowed_paths=("agent/demo.py",),
        max_files_changed=1,
        test_commands=("python -m py_compile agent/demo.py",),
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
    )


def _snapshot(worktree: Path, subject: str, collected_at: datetime):
    commit = _git("rev-parse", "HEAD", cwd=worktree)
    return SelfCognitionSnapshot.create(
        body_id=f"test-{subject}",
        git_commit=commit,
        config_digest=("1" if subject == "baseline" else "2") * 64,
        collector_version="candidate-generation-test",
        collected_at=collected_at,
    )


def _authoring_result(task_id: str, baseline: str, candidate: str):
    return EvolutionAuthoringResult.create(
        task_id=task_id,
        status="candidate_created",
        baseline_commit=baseline,
        candidate_commit=candidate,
        candidate_ref=candidate_ref_for_task(task_id),
        changed_files=("agent/demo.py",),
        environment_manifest_id="authoring-environment",
        environment_identity_id="authoring-identity",
        environment_dependency_fingerprint="f" * 64,
        agent_summary="Updated agent/demo.py",
        started_at=NOW,
        finished_at=NOW,
    )


class _AuthoringExecutor:
    def __init__(self, repository: Path, candidate: str) -> None:
        self.repository = repository
        self.candidate = candidate
        self.calls = []

    async def execute(self, spec, *, agent):
        self.calls.append((spec, agent))
        _git("update-ref", candidate_ref_for_task(spec.task_id), self.candidate, cwd=self.repository)
        return _authoring_result(spec.task_id, spec.baseline_commit, self.candidate)


class _EvaluationService:
    def __init__(self, root: Path) -> None:
        self.evaluation_repository = JsonEvaluationRepository(root)
        self.evaluate_calls = []
        self.resume_calls = []

    def evaluate(self, **kwargs):
        self.evaluate_calls.append(kwargs)
        authoring = kwargs["authoring_result"]
        pack = kwargs["benchmark_pack"]
        policy = kwargs["scoring_policy"]
        selection = select_benchmark_platforms(
            authoring.changed_files,
            authoring.environment_dependency_fingerprint,
            created_at=kwargs["created_at"],
        )
        spec = ExperimentSpec.create(
            authoring_result_id=authoring.authoring_result_id,
            platform_selection=selection,
            baseline_snapshot_id=kwargs["baseline_snapshot"].snapshot_id,
            candidate_commit=authoring.candidate_commit,
            candidate_snapshot_id=kwargs["candidate_snapshot"].snapshot_id,
            hypothesis=kwargs["hypothesis"],
            knowledge_ids=kwargs["knowledge_ids"],
            target_metrics=kwargs["target_metrics"],
            benchmark_pack_id=pack.benchmark_pack_id,
            scoring_policy_id=policy.scoring_policy_id,
            created_at=kwargs["created_at"],
        )
        self.evaluation_repository.put_benchmark_pack(pack)
        self.evaluation_repository.put_scoring_policy(policy)
        self.evaluation_repository.put_experiment_spec(spec)
        return self._outcome(spec)

    def resume(self, experiment_spec_id: str, *, completed_at=None):
        self.resume_calls.append((experiment_spec_id, completed_at))
        spec = self.evaluation_repository.get_experiment_spec(experiment_spec_id)
        assert spec is not None
        return self._outcome(spec)

    @staticmethod
    def _outcome(spec):
        return SimpleNamespace(
            experiment_spec=spec,
            experiment_result=SimpleNamespace(
                experiment_result_id=RESULT_ID,
                verdict="promote",
            ),
            governance_authorization={"authorized": True},
        )


def _service(tmp_path: Path, clock: list[datetime]):
    repository, baseline, candidate = _repository(tmp_path)
    candidate_repository = JsonEvolutionCandidateGenerationRepository(
        tmp_path / "foundation" / "candidate-generation"
    )
    authoring_repository = JsonEvolutionAuthoringRepository(
        tmp_path / "foundation" / "authoring"
    )
    authoring_executor = _AuthoringExecutor(repository, candidate)
    evaluation_service = _EvaluationService(
        tmp_path / "foundation" / "evaluation"
    )
    service = EvolutionCandidateGenerationService(
        repository,
        candidate_repository=candidate_repository,
        authoring_repository=authoring_repository,
        authoring_executor=authoring_executor,
        authoring_agent=object(),
        evaluation_service=evaluation_service,
        snapshot_worktree_root=tmp_path / "foundation" / "snapshots",
        snapshot_collector=_snapshot,
        clock=lambda: clock[0],
        blocked_cooldown=timedelta(0),
        failed_cooldown=timedelta(0),
    )
    request = _request(baseline)
    candidate_repository.register(request, requested_at=clock[0])
    return (
        service,
        request,
        candidate,
        authoring_repository,
        authoring_executor,
        evaluation_service,
    )


@pytest.mark.asyncio
async def test_service_runs_authoring_snapshots_standard_evaluation_and_authorizes(
    tmp_path: Path,
):
    clock = [NOW]
    (
        service,
        request,
        candidate,
        authoring_repository,
        authoring_executor,
        evaluation_service,
    ) = _service(tmp_path, clock)

    outcome = await service.execute(request.request_id, lease_owner="worker-1")

    assert outcome.state.status == "authorized"
    assert outcome.state.experiment_result_id == RESULT_ID
    assert len(authoring_executor.calls) == 1
    assert authoring_repository.get(outcome.state.authoring_result_id).candidate_commit == candidate
    assert len(evaluation_service.evaluate_calls) == 1
    evaluation_call = evaluation_service.evaluate_calls[0]
    assert evaluation_call["benchmark_pack"].pack_version == "native-first-platform/1"
    assert evaluation_call["scoring_policy"].required_validation_platforms == ("windows",)
    assert {item.metric for item in evaluation_call["target_metrics"]} == {
        "correctness",
        "native_compatibility",
    }
    assert evaluation_call["baseline_snapshot"].git_commit == request.baseline_commit
    assert evaluation_call["candidate_snapshot"].git_commit == candidate
    assert _git("rev-parse", "HEAD", cwd=service.repository) == request.baseline_commit
    assert _git("status", "--porcelain", cwd=service.repository) == ""


@pytest.mark.asyncio
async def test_dirty_main_repository_is_persisted_as_blocked_authoring_result(
    tmp_path: Path,
):
    clock = [NOW]
    service, request, _candidate, authoring_repository, executor, evaluation = _service(
        tmp_path, clock
    )
    (service.repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    outcome = await service.execute(request.request_id, lease_owner="worker-1")

    assert outcome.state.status == "blocked"
    assert outcome.state.error_code == "main_repository_dirty"
    assert outcome.authoring_result is not None
    assert authoring_repository.get(outcome.authoring_result.authoring_result_id) is not None
    assert executor.calls == []
    assert evaluation.evaluate_calls == []


@pytest.mark.asyncio
async def test_expired_authoring_reuses_persisted_success_without_reauthoring(
    tmp_path: Path,
):
    clock = [NOW]
    service, request, candidate, authoring_repository, executor, evaluation = _service(
        tmp_path, clock
    )
    claimed = service.candidate_repository.claim_authoring(
        request.request_id,
        lease_owner="dead-worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    result = _authoring_result(
        str(claimed.authoring_task_id), request.baseline_commit, candidate
    )
    _git("update-ref", str(result.candidate_ref), candidate, cwd=service.repository)
    authoring_repository.put(result)
    clock[0] = NOW + timedelta(minutes=5)

    outcome = await service.execute(request.request_id, lease_owner="worker-2")

    assert outcome.state.status == "authorized"
    assert outcome.state.attempt_id == claimed.attempt_id
    assert executor.calls == []
    assert len(evaluation.evaluate_calls) == 1


@pytest.mark.asyncio
async def test_expired_authoring_with_orphan_ref_is_blocked_before_new_attempt(
    tmp_path: Path,
):
    clock = [NOW]
    service, request, candidate, authoring_repository, executor, evaluation = _service(
        tmp_path, clock
    )
    claimed = service.candidate_repository.claim_authoring(
        request.request_id,
        lease_owner="dead-worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    candidate_ref = candidate_ref_for_task(str(claimed.authoring_task_id))
    _git("update-ref", candidate_ref, candidate, cwd=service.repository)
    clock[0] = NOW + timedelta(minutes=5)

    blocked = await service.execute(request.request_id, lease_owner="worker-2")

    assert blocked.state.status == "blocked"
    assert blocked.state.attempt_id == claimed.attempt_id
    assert blocked.state.error_code == "candidate_ref_without_result"
    assert blocked.authoring_result is not None
    assert authoring_repository.get(
        blocked.authoring_result.authoring_result_id
    ) == blocked.authoring_result
    assert executor.calls == []
    assert evaluation.evaluate_calls == []
    assert _git("rev-parse", "--verify", f"{candidate_ref}^{{commit}}", cwd=service.repository) == candidate

    retried = await service.execute(request.request_id, lease_owner="worker-2")

    assert retried.state.status == "authorized"
    assert retried.state.attempt_number == claimed.attempt_number + 1
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_expired_evaluation_reuses_persisted_spec_and_authoring_result(
    tmp_path: Path,
):
    clock = [NOW]
    service, request, candidate, authoring_repository, executor, evaluation = _service(
        tmp_path, clock
    )
    claimed = service.candidate_repository.claim_authoring(
        request.request_id,
        lease_owner="dead-worker",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    authoring = _authoring_result(
        str(claimed.authoring_task_id), request.baseline_commit, candidate
    )
    _git("update-ref", str(authoring.candidate_ref), candidate, cwd=service.repository)
    authoring_repository.put(authoring)
    evaluating = service.candidate_repository.begin_evaluation(
        request.request_id,
        attempt_id=str(claimed.attempt_id),
        authoring_result_id=authoring.authoring_result_id,
        lease_owner="dead-worker",
        started_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    snapshot_id = "self-cognition-" + "1" * 64
    pack_id = "benchmark-pack-" + "2" * 64
    policy_id = "scoring-policy-" + "3" * 64
    selection = select_benchmark_platforms(
        authoring.changed_files,
        authoring.environment_dependency_fingerprint,
        created_at=NOW,
    )
    spec = ExperimentSpec.create(
        authoring_result_id=authoring.authoring_result_id,
        platform_selection=selection,
        baseline_snapshot_id=snapshot_id,
        candidate_commit=candidate,
        candidate_snapshot_id="self-cognition-" + "4" * 64,
        hypothesis=request.improvement_hypothesis,
        target_metrics=request.target_metrics,
        benchmark_pack_id=pack_id,
        scoring_policy_id=policy_id,
        created_at=NOW,
    )
    evaluation.evaluation_repository.put_experiment_spec(spec)
    clock[0] = NOW + timedelta(minutes=5)

    outcome = await service.execute(request.request_id, lease_owner="worker-2")

    assert evaluating.status == "evaluating"
    assert outcome.state.status == "authorized"
    assert outcome.state.attempt_id == claimed.attempt_id
    assert executor.calls == []
    assert evaluation.evaluate_calls == []
    assert evaluation.resume_calls[0][0] == spec.experiment_spec_id


@pytest.mark.asyncio
async def test_expired_evaluation_reclaims_lease_even_for_same_owner(tmp_path: Path):
    clock = [NOW]
    service, request, candidate, authoring_repository, executor, evaluation = _service(
        tmp_path, clock
    )
    claimed = service.candidate_repository.claim_authoring(
        request.request_id,
        lease_owner="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    authoring = _authoring_result(
        str(claimed.authoring_task_id), request.baseline_commit, candidate
    )
    _git("update-ref", str(authoring.candidate_ref), candidate, cwd=service.repository)
    authoring_repository.put(authoring)
    evaluating = service.candidate_repository.begin_evaluation(
        request.request_id,
        attempt_id=str(claimed.attempt_id),
        authoring_result_id=authoring.authoring_result_id,
        lease_owner="worker-1",
        started_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    clock[0] = NOW + timedelta(minutes=5)

    outcome = await service.execute(request.request_id, lease_owner="worker-1")

    assert outcome.state.status == "authorized"
    history = service.candidate_repository.state_history(request.request_id)
    reclaimed = next(item for item in history if item.revision == evaluating.revision + 1)
    assert reclaimed.status == "evaluating"
    assert reclaimed.lease_expires_at == clock[0] + service.lease_duration
    assert executor.calls == []
    assert len(evaluation.evaluate_calls) == 1
