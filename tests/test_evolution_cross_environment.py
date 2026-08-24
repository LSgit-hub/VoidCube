from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from voidcube.systems.evolution_authoring import (
    EvolutionAuthoringExecutor,
    EvolutionAuthoringSpec,
)
from voidcube.systems.evolution_evaluation import (
    BenchmarkCase,
    BenchmarkCaseEvaluation,
    BenchmarkCaseResult,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    BenchmarkPackExecutor,
    HardGateResult,
    MetricTarget,
    MetricValue,
    NativeFirstBenchmarkExecutorFactory,
    ScoringDimension,
    ScoringPolicy,
    SubjectCheckoutEvidence,
)
from voidcube.systems.self_cognition import SelfCognitionSnapshot
from voidcube.systems.supervisor.evolution_candidate_evaluation_service import (
    EvolutionCandidateEvaluationService,
)
from voidcube.extensions.tools.files.file_tools import write_file_tool
from voidcube.infrastructure.execution.terminal_tool import terminal_tool
from voidcube.infrastructure.execution.windows_host_executor import WindowsHostExecutor


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


def _repository(root: Path, *, include_containerfile: bool = False) -> tuple[Path, str]:
    repository = root / "repo"
    repository.mkdir()
    _git("init", "--initial-branch", "master", cwd=repository)
    _git("config", "user.name", "VoidCube 5G Test", cwd=repository)
    _git("config", "user.email", "stage-5g@example.invalid", cwd=repository)
    target = repository / "src" / "voidcube" / "runtime" / "agent" / "demo.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    if include_containerfile:
        marker = (
            repository
            / "src"
            / "voidcube"
            / "extensions"
            / "tools"
            / "podman_stage5i_marker.py"
        )
        marker.parent.mkdir(parents=True)
        marker.write_text(
            "VALIDATION_MODE = 'baseline'\n",
            encoding="utf-8",
        )
    _git("add", ".", cwd=repository)
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
async def test_real_windows_authoring_to_windows_governed_evaluation(
    tmp_path: Path,
):
    if platform.system().lower() != "windows":
        pytest.skip("the real cross-environment handoff requires a Windows host")
    if not PROJECT_PYTHON.is_file():
        pytest.skip("project virtualenv is unavailable")
    repository, baseline = _repository(tmp_path)
    manifest_from_authoring = []

    class Agent:
        def author(self, context):
            manifest_from_authoring.append(context.environment_manifest)
            result = write_file_tool(
                "src/voidcube/runtime/agent/demo.py",
                "VALUE = 2\n",
                task_id=context.task_id,
            )
            return {
                "completed": not result.startswith("Error"),
                "summary": result,
            }

    spec = EvolutionAuthoringSpec(
        task_id="stage-5g-real",
        objective="Verify the governed cross-environment handoff",
        improvement_hypothesis="The candidate value improves the measured quality",
        baseline_commit=baseline,
        allowed_paths=("src/voidcube/runtime/agent/demo.py",),
        forbidden_patterns=("**/credential*",),
        max_files_changed=1,
        test_commands=(
            "python -c \"compile(open('src/voidcube/runtime/agent/demo.py').read(), 'src/voidcube/runtime/agent/demo.py', 'exec')\"",
        ),
        command_timeout_seconds=120,
        commit_message="Create stage 5G candidate",
    )
    authoring = await EvolutionAuthoringExecutor(
        repository,
        worktree_root=tmp_path / "authoring-worktrees",
        python_executable=PROJECT_PYTHON,
    ).execute(spec, agent=Agent())

    assert authoring.status == "candidate_created", (
        f"{authoring.status}: {authoring.error_code} {authoring.error_reason}; "
        f"changed_files={authoring.changed_files}; "
        f"commands={authoring.command_evidence}"
    )
    assert len(manifest_from_authoring) == 1
    authoring_manifest = manifest_from_authoring[0]
    assert authoring_manifest.backend == "local"
    assert authoring_manifest.validation_scope == "host"
    assert authoring_manifest.validated_platforms == ("windows",)
    assert authoring_manifest.image_reference is None
    assert authoring_manifest.image_digest is None

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
            command = f'"{PROJECT_PYTHON}" -m py_compile src/voidcube/runtime/agent/demo.py'
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
                    security_scanner_status="disabled",
                    container_disk_quota_status="not_applicable",
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
                input_ref="src/voidcube/runtime/agent/demo.py",
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


@pytest.mark.asyncio
async def test_real_native_authoring_dispatches_linux_and_windows_runner_matrix(
    tmp_path: Path,
):
    if platform.system().lower() != "windows":
        pytest.skip("the native-first runner matrix requires a Windows host")
    if not PROJECT_PYTHON.is_file():
        pytest.skip("project virtualenv is unavailable")
    if shutil.which("tirith") is None:
        pytest.skip("the native runner matrix requires the Tirith security scanner")
    repository, baseline = _repository(tmp_path, include_containerfile=True)

    class Agent:
        def author(self, context):
            result = write_file_tool(
                "src/voidcube/extensions/tools/podman_stage5i_marker.py",
                "VALIDATION_MODE = 'native-first'\n",
                task_id=context.task_id,
            )
            return {"completed": not result.startswith("Error"), "summary": result}

    authoring_spec = EvolutionAuthoringSpec(
        task_id="stage-5i-real-matrix",
        objective="Verify native authoring and the selected runner matrix",
        improvement_hypothesis="The container metadata is valid on both project platforms",
        baseline_commit=baseline,
        allowed_paths=("src/voidcube/extensions/tools/podman_stage5i_marker.py",),
        forbidden_patterns=("**/credential*",),
        max_files_changed=1,
        test_commands=(
            "python -c \"from pathlib import Path; assert 'native-first' in Path('src/voidcube/extensions/tools/podman_stage5i_marker.py').read_text()\"",
        ),
        command_timeout_seconds=120,
        commit_message="Create stage 5I runner matrix candidate",
    )
    authoring = await EvolutionAuthoringExecutor(
        repository,
        worktree_root=tmp_path / "authoring-worktrees",
        python_executable=PROJECT_PYTHON,
    ).execute(authoring_spec, agent=Agent())
    assert authoring.status == "candidate_created", (
        f"{authoring.status}: {authoring.error_code} {authoring.error_reason}"
    )

    def evaluator(request, task_id, _environment):
        command = (
            "python -c \"from pathlib import Path; "
            "assert 'VALIDATION_MODE' in Path('src/voidcube/extensions/tools/podman_stage5i_marker.py').read_text()\""
        )
        payload = json.loads(
            terminal_tool(command, task_id=task_id, timeout=request.timeout_seconds)
        )
        return BenchmarkCaseEvaluation(
            case_id=request.case.case_id,
            metrics=(
                MetricValue(
                    metric="correctness",
                    value=0.8 if request.subject == "baseline" else 0.95,
                    unit="ratio",
                ),
            ),
            hard_gate_results=(
                HardGateResult(
                    gate="tests",
                    passed=payload["exit_code"] == 0 and not payload.get("timed_out"),
                ),
            ),
            command_evidence=(
                BenchmarkCommandEvidence(
                    command=command,
                    exit_code=int(payload["exit_code"]),
                    output_summary=str(payload.get("output") or "completed").strip(),
                    timed_out=bool(payload.get("timed_out")),
                    security_scanner_status=payload.get("security_scanner_status"),
                    container_disk_quota_status=payload.get(
                        "container_disk_quota_status"
                    ),
                ),
            ),
        )

    now = datetime.now(timezone.utc)
    pack = BenchmarkPack.create(
        name="stage-5i-real-matrix",
        pack_version="1",
        cases=(
            BenchmarkCase(
                case_id="containerfile",
                runner="containerfile",
                input_ref="src/voidcube/extensions/tools/podman_stage5i_marker.py",
            ),
        ),
        created_at=now,
    )
    policy = ScoringPolicy.create(
        policy_version="stage-5i-real-matrix",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        required_validation_platforms=("linux", "windows"),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=now,
    )
    executor_factory = NativeFirstBenchmarkExecutorFactory(
        repository,
        worktree_root=tmp_path / "validation-worktrees",
        evaluators={"containerfile": evaluator},
        python_executable=PROJECT_PYTHON,
        case_timeout_seconds=120,
    )
    service = EvolutionCandidateEvaluationService.from_root(
        repository,
        tmp_path / "foundation",
        benchmark_executor_factory=executor_factory,
    )
    outcome = service.evaluate(
        authoring_result=authoring,
        baseline_snapshot=_snapshot(baseline, "baseline", now),
        candidate_snapshot=_snapshot(str(authoring.candidate_commit), "candidate", now),
        benchmark_pack=pack,
        scoring_policy=policy,
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
        hypothesis="The container metadata remains valid on Linux and Windows",
        created_at=now,
        completed_at=now,
    )

    result = outcome.experiment_result
    authorization = outcome.governance_authorization
    assert result.verdict == "promote"
    assert result.execution_environments is not None
    assert result.execution_environment_identities is not None
    assert len(result.execution_environments) == 4
    assert len(result.execution_environment_identities) == 2
    assert authorization["authorized"] is True
    assert authorization["selected_validation_platforms"] == ["linux", "windows"]
    assert len(authorization["execution_environment_ids"]) == 4
    assert len(authorization["execution_environment_identity_ids"]) == 2
    assert not (tmp_path / "validation-worktrees" / "linux").exists()
    assert not (tmp_path / "validation-worktrees" / "windows").exists()
