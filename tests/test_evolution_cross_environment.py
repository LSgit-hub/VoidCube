from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from systems.evolution_authoring import (
    EvolutionAuthoringExecutor,
    EvolutionAuthoringSpec,
)
from systems.evolution_evaluation import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    BenchmarkPackExecutor,
    HardGateResult,
    MetricTarget,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
    SubjectCheckoutEvidence,
)
from systems.self_cognition import SelfCognitionSnapshot
from systems.supervisor.evolution_candidate_evaluation_service import (
    EvolutionCandidateEvaluationService,
)
from tools.podman_sandbox import DEFAULT_IMAGE, image_exists
from tools.terminal_tool import terminal_tool
from tools.windows_host_executor import WindowsHostExecutor


pytestmark = [pytest.mark.integration, pytest.mark.slow]
PROJECT_ROOT = Path(__file__).parents[1].resolve()
PROJECT_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


def _git(*args: str, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )
    if check:
        return result.stdout.strip()
    return result.stdout.strip() or result.stderr.strip()


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repo"
    repository.mkdir()
    _git("init", "--initial-branch", "master", cwd=repository)
    _git("config", "user.name", "VoidCube 5G Test", cwd=repository)
    _git("config", "user.email", "stage-5g@example.invalid", cwd=repository)
    (repository / "agent").mkdir()
    (repository / "agent/demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", "agent/demo.py", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


def _snapshot(commit: str, body_id: str, now: datetime) -> SelfCognitionSnapshot:
    return SelfCognitionSnapshot.create(
        body_id=body_id,
        git_commit=commit,
        config_digest=("1" if body_id == "baseline" else "2") * 64,
        collector_version="stage-5g-integration",
        collected_at=now,
    )


@pytest.mark.asyncio
async def test_real_podman_authoring_to_windows_governed_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    if platform.system().lower() != "windows":
        pytest.skip("the real cross-environment handoff requires a Windows host")
    if not PROJECT_PYTHON.is_file():
        pytest.skip("project virtualenv is unavailable")
    if not image_exists(DEFAULT_IMAGE):
        pytest.skip(f"Podman image is unavailable: {DEFAULT_IMAGE}")

    monkeypatch.setenv("TERMINAL_ENV", "podman")
    monkeypatch.setenv("TERMINAL_PODMAN_IMAGE", DEFAULT_IMAGE)
    monkeypatch.setenv("TERMINAL_FALLBACK_TO_LOCAL", "false")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "120")

    repository, baseline = _repository(tmp_path)
    manifest_from_authoring = []

    class Agent:
        def author(self, context):
            manifest_from_authoring.append(context.environment_manifest)
            payload = json.loads(
                terminal_tool(
                    "printf 'VALUE = 2\\n' > /workspace/agent/demo.py",
                    task_id=context.task_id,
                    timeout=60,
                )
            )
            return {
                "completed": payload.get("exit_code") == 0,
                "summary": "Podman authoring command completed",
            }

    spec = EvolutionAuthoringSpec(
        task_id="stage-5g-real",
        objective="Verify the governed cross-environment handoff",
        improvement_hypothesis="The candidate value improves the measured quality",
        baseline_commit=baseline,
        allowed_paths=("agent/demo.py",),
        forbidden_patterns=("**/credential*",),
        max_files_changed=1,
        test_commands=(
            "python -c \"source=open('/workspace/agent/demo.py').read(); compile(source, 'agent/demo.py', 'exec')\"",
        ),
        command_timeout_seconds=120,
        commit_message="Create stage 5G candidate",
    )
    authoring = await EvolutionAuthoringExecutor(
        repository,
        worktree_root=tmp_path / "authoring-worktrees",
    ).execute(spec, agent=Agent())

    assert authoring.status == "candidate_created", (
        f"{authoring.status}: {authoring.error_code} {authoring.error_reason}; "
        f"changed_files={authoring.changed_files}; "
        f"commands={authoring.command_evidence}"
    )
    assert len(manifest_from_authoring) == 1
    authoring_manifest = manifest_from_authoring[0]
    assert authoring_manifest.backend == "podman"
    assert authoring_manifest.validation_scope == "container"
    assert authoring_manifest.validated_platforms == ("linux",)
    assert authoring_manifest.image_reference == DEFAULT_IMAGE
    assert authoring_manifest.image_digest

    evaluation_worktree = tmp_path / "evaluation-worktree"
    _git("worktree", "add", "--detach", str(evaluation_worktree), baseline, cwd=repository)
    now = datetime.now(timezone.utc)

    def runner(request):
        desired_commit = (
            baseline if request.subject == "baseline" else str(authoring.candidate_commit)
        )
        _git("switch", "--detach", "--force", desired_commit, cwd=evaluation_worktree)
        host = WindowsHostExecutor(
            evaluation_worktree,
            python_executable=PROJECT_PYTHON,
        )
        try:
            manifest = host.probe()
            command = f'"{PROJECT_PYTHON}" -m py_compile agent/demo.py'
            command_result = host.run(command)
        finally:
            host.cleanup()
        checkout = SubjectCheckoutEvidence.create(
            subject=request.subject,
            commit=desired_commit,
            worktree_path=str(evaluation_worktree),
            execution_environment_identity_id=(
                manifest.identity().execution_environment_identity_id
            ),
            checked_out_at=now,
        )
        return BenchmarkCaseResult(
            case_id=request.case.case_id,
            metrics=(
                MetricValue(
                    metric="correctness",
                    value=0.8 if request.subject == "baseline" else 0.95,
                    unit="ratio",
                ),
            ),
            execution_environment=manifest,
            hard_gate_results=(
                HardGateResult(
                    gate="tests",
                    passed=command_result.exit_code == 0
                    and not command_result.timed_out,
                ),
            ),
            command_evidence=(
                BenchmarkCommandEvidence(
                    command=command,
                    exit_code=command_result.exit_code,
                    output_summary=command_result.output.strip() or "completed",
                    timed_out=command_result.timed_out,
                ),
            ),
            subject_checkout=checkout,
        )

    pack = BenchmarkPack.create(
        name="stage-5g-real",
        pack_version="1",
        cases=(
            BenchmarkCase(
                case_id="pycompile",
                runner="pycompile",
                input_ref="agent/demo.py",
            ),
        ),
        created_at=now,
    )
    policy = ScoringPolicy.create(
        policy_version="stage-5g-real",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        required_validation_platforms=("windows",),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=now,
    )
    try:
        service = EvolutionCandidateEvaluationService.from_root(
            repository,
            tmp_path / "foundation",
            benchmark_executor=BenchmarkPackExecutor({"pycompile": runner}),
        )
        outcome = service.evaluate(
            authoring_result=authoring,
            baseline_snapshot=_snapshot(baseline, "baseline", now),
            candidate_snapshot=_snapshot(str(authoring.candidate_commit), "candidate", now),
            benchmark_pack=pack,
            scoring_policy=policy,
            target_metrics=(
                MetricTarget(metric="correctness", objective="increase"),
            ),
            hypothesis="The candidate improves correctness",
            created_at=now,
            completed_at=now,
        )
    finally:
        _git("worktree", "remove", "--force", str(evaluation_worktree), cwd=repository)

    evidence = outcome.experiment_result.benchmark_case_evidence
    assert outcome.experiment_result.verdict == "promote"
    assert outcome.governance_authorization["authorized"] is True
    assert outcome.governance_authorization["validated_platforms"] == ["windows"]
    assert evidence is not None and len(evidence) == 2
    assert {item.subject for item in evidence} == {"baseline", "candidate"}
    assert len({item.execution_environment_identity_id for item in evidence}) == 1
    assert (
        authoring.environment_identity_id
        == outcome.governance_authorization["authoring_environment_identity_id"]
    )
    assert authoring.environment_identity_id != evidence[0].execution_environment_identity_id
    print(
        "5G_REAL_EVIDENCE "
        + json.dumps(
            {
                "authoring_environment_manifest_id": authoring.environment_manifest_id,
                "authoring_environment_identity_id": authoring.environment_identity_id,
                "authoring_image_digest": authoring_manifest.image_digest,
                "evaluation_environment_ids": sorted(
                    {item.execution_environment_id for item in evidence}
                ),
                "evaluation_environment_identity_id": evidence[0].execution_environment_identity_id,
                "experiment_result_id": outcome.experiment_result.experiment_result_id,
            },
            sort_keys=True,
        )
    )
