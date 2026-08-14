from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from systems.evolution_authoring import (
    AuthoringCommandEvidence,
    EvolutionAuthoringResult,
)
from systems.evolution_evaluation import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    BenchmarkPackExecutor,
    ExecutionEnvironmentManifest,
    HardGateResult,
    MetricTarget,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
    capture_host_environment_manifest,
)
from systems.self_cognition import SelfCognitionSnapshot
from systems.supervisor.endogenous_candidate_factories import (
    body_improvement_constraints,
)
from systems.supervisor.endogenous_foundation_bridge import (
    EndogenousFoundationReadOnlyProjection,
)
from systems.supervisor.evolution_candidate_evaluation_service import (
    EvolutionCandidateEvaluationBlocked,
    EvolutionCandidateEvaluationService,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
NOW = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)


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


def _repository(
    tmp_path: Path,
    relative_path: str = "agent/demo.py",
) -> tuple[Path, str, str, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.com", cwd=repository)
    target = repository / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", relative_path, cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    baseline = _git("rev-parse", "HEAD", cwd=repository)
    branch = _git("branch", "--show-current", cwd=repository)
    _git("switch", "--detach", baseline, cwd=repository)
    target.write_text("VALUE = 2\n", encoding="utf-8")
    _git("add", relative_path, cwd=repository)
    _git("commit", "-m", "candidate", cwd=repository)
    candidate = _git("rev-parse", "HEAD", cwd=repository)
    candidate_ref = "refs/voidcube/candidates/stage-5f-test"
    _git("update-ref", candidate_ref, candidate, cwd=repository)
    _git("switch", branch, cwd=repository)
    return repository, baseline, candidate, candidate_ref


def _authoring(
    baseline: str,
    candidate: str,
    candidate_ref: str,
    candidate_environment: ExecutionEnvironmentManifest,
    *,
    changed_files: tuple[str, ...] = ("agent/demo.py",),
) -> EvolutionAuthoringResult:
    return EvolutionAuthoringResult.create(
        task_id="stage-5f-test",
        status="candidate_created",
        baseline_commit=baseline,
        candidate_commit=candidate,
        candidate_ref=candidate_ref,
        changed_files=changed_files,
        environment_manifest_id=candidate_environment.execution_environment_id,
        environment_identity_id=(
            candidate_environment.identity().execution_environment_identity_id
        ),
        environment_dependency_fingerprint=candidate_environment.dependency_fingerprint,
        command_evidence=(
            AuthoringCommandEvidence(
                command="python -m py_compile agent/demo.py",
                exit_code=0,
                output="no output",
                security_scanner_status="available",
                container_disk_quota_status="unsupported",
            ),
        ),
        agent_summary="Changed the demo value",
        started_at=NOW,
        finished_at=NOW,
    )


def _snapshot(commit: str, body_id: str) -> SelfCognitionSnapshot:
    return SelfCognitionSnapshot.create(
        body_id=body_id,
        git_commit=commit,
        config_digest=("1" if body_id == "baseline" else "2") * 64,
        collector_version="stage-5f-test",
        collected_at=NOW,
    )


def _contracts() -> tuple[BenchmarkPack, ScoringPolicy]:
    pack = BenchmarkPack.create(
        name="stage-5f",
        pack_version="1",
        cases=(BenchmarkCase(case_id="quality", runner="quality", input_ref="demo"),),
        created_at=NOW,
    )
    policy = ScoringPolicy.create(
        policy_version="stage-5f",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        required_validation_platforms=("windows",),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=NOW,
    )
    return pack, policy


def _environment(
    repository: Path,
    commit: str,
    *,
    platform_name: str = "windows",
) -> ExecutionEnvironmentManifest:
    manifest = capture_host_environment_manifest(repository, repository_head=commit)
    if platform_name == "windows":
        return manifest
    payload = manifest.content_payload()
    payload.update(
        backend="podman",
        validation_scope="container",
        execution_os="Linux 6.8",
        architecture="x86_64",
        execution_workspace_path="/workspace",
        path_mappings=(
            {
                "host_path": manifest.host_workspace_path,
                "execution_path": "/workspace",
            },
        ),
        validated_platforms=("linux",),
    )
    return ExecutionEnvironmentManifest.create(**payload)


def _executor(
    baseline_environment: ExecutionEnvironmentManifest,
    candidate_environment: ExecutionEnvironmentManifest,
) -> BenchmarkPackExecutor:
    def runner(request):
        candidate = request.subject == "candidate"
        return BenchmarkCaseResult(
            case_id=request.case.case_id,
            metrics=(
                MetricValue(
                    metric="correctness",
                    value=0.9 if candidate else 0.8,
                    unit="ratio",
                ),
            ),
            execution_environment=(
                candidate_environment if candidate else baseline_environment
            ),
            hard_gate_results=(HardGateResult(gate="tests", passed=True),),
            command_evidence=(
                BenchmarkCommandEvidence(
                    command="pytest tests/test_demo.py -q",
                    exit_code=0,
                    output_summary="1 passed",
                ),
            ),
            evidence_refs=(f"log:{request.subject}:{request.case.case_id}",),
        )

    return BenchmarkPackExecutor({"quality": runner})


def _service(
    repository: Path,
    foundation_root: Path,
    baseline: str,
    candidate: str,
    *,
    platform_name: str = "windows",
) -> tuple[
    EvolutionCandidateEvaluationService,
    ExecutionEnvironmentManifest,
    ExecutionEnvironmentManifest,
]:
    baseline_environment = _environment(
        repository, baseline, platform_name=platform_name
    )
    candidate_environment = _environment(
        repository, candidate, platform_name=platform_name
    )
    service = EvolutionCandidateEvaluationService.from_root(
        repository,
        foundation_root,
        benchmark_executor=_executor(baseline_environment, candidate_environment),
    )
    return service, baseline_environment, candidate_environment


def _evaluate(
    service: EvolutionCandidateEvaluationService,
    authoring: EvolutionAuthoringResult,
    baseline: str,
    candidate: str,
):
    pack, policy = _contracts()
    return service.evaluate(
        authoring_result=authoring,
        baseline_snapshot=_snapshot(baseline, "baseline"),
        candidate_snapshot=_snapshot(candidate, "candidate"),
        benchmark_pack=pack,
        scoring_policy=policy,
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
        hypothesis="The candidate improves correctness",
        completed_at=NOW,
        created_at=NOW,
    )


def test_handoff_persists_replayable_evidence_and_exposes_existing_authorization(
    tmp_path: Path,
):
    repository, baseline, candidate, candidate_ref = _repository(tmp_path)
    foundation_root = tmp_path / "foundation"
    service, baseline_environment, candidate_environment = _service(
        repository, foundation_root, baseline, candidate
    )
    authoring = _authoring(baseline, candidate, candidate_ref, candidate_environment)

    outcome = _evaluate(service, authoring, baseline, candidate)

    assert outcome.experiment_spec.authoring_result_id == authoring.authoring_result_id
    assert outcome.experiment_result.verdict == "promote"
    assert outcome.governance_authorization["authorized"] is True
    assert outcome.governance_authorization["candidate_ref"] == candidate_ref
    assert outcome.governance_authorization["changed_files"] == ["agent/demo.py"]
    evidence = outcome.experiment_result.benchmark_case_evidence
    assert evidence is not None and len(evidence) == 2
    assert {item.execution_environment_id for item in evidence} == {
        baseline_environment.execution_environment_id,
        candidate_environment.execution_environment_id,
    }
    assert all(item.commands[0].output_summary == "1 passed" for item in evidence)

    projection = EndogenousFoundationReadOnlyProjection.from_root(
        foundation_root
    ).load()
    authorization = projection["evaluation"]["body_improvement_authorization"]
    assert authorization["authorized"] is True
    assert authorization["authoring_result_id"] == authoring.authoring_result_id

    constraints = body_improvement_constraints(
        {
            "target_slot_id": "slot-B",
            "worktree_path": str(repository),
            "target_paths": ["agent/demo.py"],
            "mapping_source": "stage-5f-test",
            "evaluation_authorization": authorization,
        }
    )
    assert constraints["requires_governor_review"] is True
    assert constraints["requires_user_consent"] is True
    assert constraints["must_match_evaluated_commit"] is True
    assert constraints["authoring_result_id"] == authoring.authoring_result_id


def test_linux_evaluation_cannot_cross_windows_platform_gate(tmp_path: Path):
    repository, baseline, candidate, candidate_ref = _repository(tmp_path)
    service, _baseline_environment, candidate_environment = _service(
        repository,
        tmp_path / "foundation",
        baseline,
        candidate,
        platform_name="linux",
    )
    authoring = _authoring(baseline, candidate, candidate_ref, candidate_environment)

    outcome = _evaluate(service, authoring, baseline, candidate)

    assert outcome.experiment_result.verdict == "reject"
    assert outcome.governance_authorization["authorized"] is False
    assert (
        outcome.governance_authorization["reason"] == "experiment_verdict_not_promote"
    )


def test_handoff_rejects_candidate_ref_mismatch_before_persistence(tmp_path: Path):
    repository, baseline, candidate, candidate_ref = _repository(tmp_path)
    foundation_root = tmp_path / "foundation"
    service, _baseline_environment, candidate_environment = _service(
        repository, foundation_root, baseline, candidate
    )
    authoring = _authoring(baseline, candidate, candidate_ref, candidate_environment)
    _git("update-ref", candidate_ref, baseline, cwd=repository)

    with pytest.raises(EvolutionCandidateEvaluationBlocked) as captured:
        _evaluate(service, authoring, baseline, candidate)

    assert captured.value.code == "candidate_ref_commit_mismatch"
    assert service.authoring_repository.list_ids() == ()


def test_handoff_rejects_changed_file_evidence_mismatch(tmp_path: Path):
    repository, baseline, candidate, candidate_ref = _repository(tmp_path)
    service, _baseline_environment, candidate_environment = _service(
        repository, tmp_path / "foundation", baseline, candidate
    )
    authoring = _authoring(
        baseline,
        candidate,
        candidate_ref,
        candidate_environment,
        changed_files=("agent/other.py",),
    )

    with pytest.raises(EvolutionCandidateEvaluationBlocked) as captured:
        _evaluate(service, authoring, baseline, candidate)

    assert captured.value.code == "candidate_changed_files_mismatch"


def test_handoff_rejects_authoring_without_capability_evidence(tmp_path: Path):
    repository, baseline, candidate, candidate_ref = _repository(tmp_path)
    service, _baseline_environment, candidate_environment = _service(
        repository, tmp_path / "foundation", baseline, candidate
    )
    authoring = _authoring(baseline, candidate, candidate_ref, candidate_environment)
    payload = authoring.content_payload()
    payload["command_evidence"][0]["security_scanner_status"] = None
    incomplete = EvolutionAuthoringResult.create(**payload)

    with pytest.raises(EvolutionCandidateEvaluationBlocked) as captured:
        _evaluate(service, incomplete, baseline, candidate)

    assert captured.value.code == "authoring_environment_capability_evidence_missing"


def test_governance_rejects_tampered_persisted_authoring_result(tmp_path: Path):
    repository, baseline, candidate, candidate_ref = _repository(tmp_path)
    foundation_root = tmp_path / "foundation"
    service, _baseline_environment, candidate_environment = _service(
        repository, foundation_root, baseline, candidate
    )
    authoring = _authoring(baseline, candidate, candidate_ref, candidate_environment)
    outcome = _evaluate(service, authoring, baseline, candidate)
    result_path = (
        foundation_root
        / "authoring"
        / "results"
        / f"{authoring.authoring_result_id}.json"
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["changed_files"] = ["agent/forged.py"]
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    authorization = service.governance_verifier.verify(
        outcome.experiment_result.experiment_result_id
    )

    assert authorization["authorized"] is False
    assert authorization["reason"] == "authoring_result_unreadable"


def test_handoff_blocks_when_policy_does_not_cover_selected_platforms(tmp_path: Path):
    changed_file = "tools/podman_probe.py"
    repository, baseline, candidate, candidate_ref = _repository(
        tmp_path,
        relative_path=changed_file,
    )
    foundation_root = tmp_path / "foundation"
    service, _baseline_environment, candidate_environment = _service(
        repository, foundation_root, baseline, candidate
    )
    authoring = _authoring(
        baseline,
        candidate,
        candidate_ref,
        candidate_environment,
        changed_files=(changed_file,),
    )

    with pytest.raises(EvolutionCandidateEvaluationBlocked) as captured:
        _evaluate(service, authoring, baseline, candidate)

    assert captured.value.code == "platform_selection_not_covered"
    assert service.evaluation_repository.list_ids("experiment_specs") == ()
