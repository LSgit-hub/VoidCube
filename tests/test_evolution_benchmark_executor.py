from __future__ import annotations

import ast
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from systems.evolution_evaluation import (
    BENCHMARK_CONSISTENCY_GATE,
    EXECUTION_ENVIRONMENT_GATE,
    BenchmarkCase,
    BenchmarkCaseFailed,
    BenchmarkCaseResult,
    BenchmarkCaseTimedOut,
    BenchmarkCommandEvidence,
    BenchmarkConfigurationError,
    BenchmarkPack,
    BenchmarkPackExecutor,
    ExperimentSpec,
    ExecutionEnvironmentManifest,
    HardGateResult,
    JsonEvaluationRepository,
    MetricValue,
    MetricTarget,
    ScoringDimension,
    ScoringPolicy,
    AllowedRegression,
    capture_host_environment_manifest,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
BASELINE_SNAPSHOT = "self-cognition-" + "a" * 64
CANDIDATE_SNAPSHOT = "self-cognition-" + "b" * 64
ENVIRONMENT = capture_host_environment_manifest(
    Path(__file__).parents[1],
    repository_head="c" * 40,
)


def _contracts(
    *,
    objective: str = "increase",
    target_value: float | None = 0.85,
    allowed_regressions: tuple[AllowedRegression, ...] = (),
):
    pack = BenchmarkPack.create(
        name="quality",
        pack_version="1",
        cases=(
            BenchmarkCase(case_id="case-1", runner="quality", input_ref="input-1"),
            BenchmarkCase(case_id="case-2", runner="quality", input_ref="input-2"),
        ),
        created_at=NOW,
    )
    policy = ScoringPolicy.create(
        policy_version="1",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        required_validation_platforms=("windows",),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=NOW,
    )
    spec = ExperimentSpec.create(
        baseline_snapshot_id=BASELINE_SNAPSHOT,
        candidate_commit="candidate-commit",
        candidate_snapshot_id=CANDIDATE_SNAPSHOT,
        hypothesis="The candidate improves correctness.",
        knowledge_ids=(),
        target_metrics=(
            MetricTarget(
                metric="correctness",
                objective=objective,
                target_value=target_value,
            ),
        ),
        allowed_regressions=allowed_regressions,
        benchmark_pack_id=pack.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=NOW,
    )
    return pack, policy, spec


def _runner(request):
    value = 0.8 if request.subject == "baseline" else 0.9
    return BenchmarkCaseResult(
        case_id=request.case.case_id,
        execution_environment=ENVIRONMENT,
        metrics=(MetricValue(metric="correctness", value=value, unit="ratio"),),
        hard_gate_results=(
            HardGateResult(
                gate="tests",
                passed=True,
                evidence_refs=(f"{request.subject}:{request.case.case_id}",),
            ),
        ),
        command_evidence=(_command_evidence(request),),
        evidence_refs=(f"evidence:{request.subject}:{request.case.case_id}",),
    )


def _command_evidence(request):
    return BenchmarkCommandEvidence(
        command=f"pytest {request.case.input_ref}",
        exit_code=0,
        output_summary=f"{request.subject}:{request.case.case_id}:passed",
    )


def test_executor_runs_same_pack_for_both_subjects_and_promotes():
    pack, policy, spec = _contracts()
    calls: list[tuple[str, str]] = []

    def recording_runner(request):
        calls.append((request.subject, request.case.case_id))
        return _runner(request)

    result = BenchmarkPackExecutor({"quality": recording_runner}).execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    assert calls == [
        ("baseline", "case-1"),
        ("baseline", "case-2"),
        ("candidate", "case-1"),
        ("candidate", "case-2"),
    ]
    assert result.baseline_metrics == (MetricValue(metric="correctness", value=0.8, unit="ratio"),)
    assert result.candidate_metrics == (MetricValue(metric="correctness", value=0.9, unit="ratio"),)
    assert result.metric_deltas[0].delta == pytest.approx(0.1)
    assert result.confidence == 1.0
    assert result.verdict == "promote"
    assert {gate.gate for gate in result.hard_gate_results} == {
        "tests",
        BENCHMARK_CONSISTENCY_GATE,
        EXECUTION_ENVIRONMENT_GATE,
    }
    assert all(gate.passed for gate in result.hard_gate_results)
    assert result.benchmark_case_evidence is not None
    assert len(result.benchmark_case_evidence) == 4
    assert {
        (item.subject, item.case_id)
        for item in result.benchmark_case_evidence
    } == {
        ("baseline", "case-1"),
        ("baseline", "case-2"),
        ("candidate", "case-1"),
        ("candidate", "case-2"),
    }
    assert all(item.commands[0].exit_code == 0 for item in result.benchmark_case_evidence)


def test_platform_coverage_is_a_hard_gate():
    pack, policy, spec = _contracts()
    payload = ENVIRONMENT.content_payload()
    payload.update(
        backend="podman",
        validation_scope="container",
        execution_os="Linux 6.8",
        architecture="x86_64",
        execution_workspace_path="/workspace",
        path_mappings=(
            {
                "host_path": ENVIRONMENT.host_workspace_path,
                "execution_path": "/workspace",
            },
        ),
        validated_platforms=("linux",),
    )
    linux_environment = ExecutionEnvironmentManifest.create(**payload)

    def linux_runner(request):
        result = _runner(request)
        return result.model_copy(update={"execution_environment": linux_environment})

    result = BenchmarkPackExecutor({"quality": linux_runner}).execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    environment_gate = next(
        gate
        for gate in result.hard_gate_results
        if gate.gate == EXECUTION_ENVIRONMENT_GATE
    )
    assert environment_gate.passed is False
    assert "missing-platform:windows" in environment_gate.evidence_refs
    assert result.verdict == "reject"


def test_baseline_candidate_environment_drift_aborts_evaluation():
    pack, policy, spec = _contracts()
    payload = ENVIRONMENT.content_payload()
    payload["backend"] = "different-host"
    drifted = ExecutionEnvironmentManifest.create(**payload)

    def drifting_environment_runner(request):
        result = _runner(request)
        environment = ENVIRONMENT if request.subject == "baseline" else drifted
        return result.model_copy(update={"execution_environment": environment})

    with pytest.raises(BenchmarkCaseFailed, match="identical execution environment"):
        BenchmarkPackExecutor({"quality": drifting_environment_runner}).execute(
            benchmark_pack=pack,
            experiment_spec=spec,
            scoring_policy=policy,
            completed_at=NOW,
        )


def test_baseline_and_candidate_heads_can_differ_with_one_environment_identity():
    pack, policy, spec = _contracts()
    candidate_payload = ENVIRONMENT.content_payload()
    candidate_payload["repository_head"] = "d" * 40
    candidate_environment = ExecutionEnvironmentManifest.create(**candidate_payload)

    def checkout_runner(request):
        result = _runner(request)
        environment = ENVIRONMENT if request.subject == "baseline" else candidate_environment
        return result.model_copy(update={"execution_environment": environment})

    result = BenchmarkPackExecutor({"quality": checkout_runner}).execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    assert result.execution_environment_identity is not None
    assert {item.subject for item in result.subject_checkouts} == {"baseline", "candidate"}
    assert {
        item.commit
        for item in result.subject_checkouts
        if item.subject == "baseline"
    } == {"c" * 40}
    assert {
        item.commit
        for item in result.subject_checkouts
        if item.subject == "candidate"
    } == {"d" * 40}


def test_missing_required_gate_rejects_without_fabricating_success():
    pack, policy, spec = _contracts()

    def no_gate_runner(request):
        value = 0.8 if request.subject == "baseline" else 0.9
        return BenchmarkCaseResult(
            case_id=request.case.case_id,
            execution_environment=ENVIRONMENT,
            metrics=(MetricValue(metric="correctness", value=value, unit="ratio"),),
            command_evidence=(_command_evidence(request),),
        )

    result = BenchmarkPackExecutor({"quality": no_gate_runner}).execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    tests_gate = next(gate for gate in result.hard_gate_results if gate.gate == "tests")
    assert tests_gate.passed is False
    assert "benchmark-executor:missing-required-gate" in tests_gate.evidence_refs
    assert result.verdict == "reject"


def test_disallowed_regression_is_recorded_and_rejected():
    pack, policy, spec = _contracts()

    def regressing_runner(request):
        value = 0.9 if request.subject == "baseline" else 0.8
        return BenchmarkCaseResult(
            case_id=request.case.case_id,
            execution_environment=ENVIRONMENT,
            metrics=(MetricValue(metric="correctness", value=value, unit="ratio"),),
            hard_gate_results=(HardGateResult(gate="tests", passed=True),),
            command_evidence=(_command_evidence(request),),
        )

    result = BenchmarkPackExecutor({"quality": regressing_runner}).execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    assert result.verdict == "reject"
    assert result.regressions[0].metric == "correctness"
    assert result.regressions[0].observed_delta == pytest.approx(0.1)
    assert result.regressions[0].allowed_delta == 0.0


def test_allowed_regression_can_observe_when_score_is_partial():
    pack, policy, spec = _contracts(
        objective="maintain",
        target_value=None,
        allowed_regressions=(AllowedRegression(metric="correctness", maximum_delta=0.2),),
    )

    def slightly_regressing_runner(request):
        value = 0.9 if request.subject == "baseline" else 0.8
        return BenchmarkCaseResult(
            case_id=request.case.case_id,
            execution_environment=ENVIRONMENT,
            metrics=(MetricValue(metric="correctness", value=value, unit="ratio"),),
            hard_gate_results=(HardGateResult(gate="tests", passed=True),),
            command_evidence=(_command_evidence(request),),
        )

    result = BenchmarkPackExecutor({"quality": slightly_regressing_runner}).execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    assert result.verdict == "promote"
    assert result.regressions[0].allowed_delta == 0.2


def test_timeout_and_runner_failures_abort_without_result():
    pack, policy, spec = _contracts()

    def slow_runner(_request):
        time.sleep(0.05)
        return _runner(_request)

    with pytest.raises(BenchmarkCaseTimedOut):
        BenchmarkPackExecutor(
            {"quality": slow_runner}, case_timeout_seconds=0.001
        ).execute(
            benchmark_pack=pack,
            experiment_spec=spec,
            scoring_policy=policy,
            completed_at=NOW,
        )

    def failing_runner(_request):
        raise RuntimeError("runner failed")

    with pytest.raises(BenchmarkCaseFailed):
        BenchmarkPackExecutor({"quality": failing_runner}).execute(
            benchmark_pack=pack,
            experiment_spec=spec,
            scoring_policy=policy,
            completed_at=NOW,
        )


def test_metric_shape_drift_and_missing_runner_are_rejected():
    pack, policy, spec = _contracts()

    def drifting_runner(request):
        metrics = [MetricValue(metric="correctness", value=0.8, unit="ratio")]
        if request.subject == "candidate":
            metrics.append(MetricValue(metric="latency", value=1.0, unit="ms"))
        return BenchmarkCaseResult(
            case_id=request.case.case_id,
            execution_environment=ENVIRONMENT,
            metrics=tuple(metrics),
            hard_gate_results=(HardGateResult(gate="tests", passed=True),),
            command_evidence=(_command_evidence(request),),
        )

    with pytest.raises(BenchmarkCaseFailed, match="metric shape"):
        BenchmarkPackExecutor({"quality": drifting_runner}).execute(
            benchmark_pack=pack,
            experiment_spec=spec,
            scoring_policy=policy,
            completed_at=NOW,
        )

    with pytest.raises(BenchmarkConfigurationError, match="not registered"):
        BenchmarkPackExecutor({}).execute(
            benchmark_pack=pack,
            experiment_spec=spec,
            scoring_policy=policy,
            completed_at=NOW,
        )


def test_execute_from_repository_persists_result(tmp_path: Path):
    pack, policy, spec = _contracts()
    repository = JsonEvaluationRepository(tmp_path / "evaluation")
    repository.put_benchmark_pack(pack)
    repository.put_scoring_policy(policy)
    repository.put_experiment_spec(spec)

    result = BenchmarkPackExecutor({"quality": _runner}).execute_from_repository(
        repository,
        experiment_spec_id=spec.experiment_spec_id,
        completed_at=NOW,
    )

    assert repository.get_experiment_result(result.experiment_result_id) == result
    assert repository.list_ids("experiment_results") == (result.experiment_result_id,)


def test_executor_has_no_governor_or_legacy_learning_imports():
    source_path = Path(__file__).parents[1] / "systems" / "evolution_evaluation" / "executor.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert not any(name.startswith("systems.supervisor") for name in imports)
    assert "SelfLearningConclusionStore" not in source
