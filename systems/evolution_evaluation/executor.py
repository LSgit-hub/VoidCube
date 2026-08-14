"""Controlled BenchmarkPack execution without governance or body mutation."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from systems.evolution_evaluation.models import (
    BenchmarkCase,
    BenchmarkPack,
    ExperimentResult,
    ExperimentSpec,
    HardGateResult,
    MetricDelta,
    MetricValue,
    Regression,
    ScoringPolicy,
)
from systems.evolution_evaluation.repository import EvaluationRepository


DEFAULT_BENCHMARK_EXECUTOR_VERSION = "benchmark-executor/1"
BENCHMARK_CONSISTENCY_GATE = "benchmark_pack_consistency"


class BenchmarkExecutionError(RuntimeError):
    """Base error for an incomplete or invalid benchmark execution."""


class BenchmarkConfigurationError(BenchmarkExecutionError):
    pass


class BenchmarkCaseFailed(BenchmarkExecutionError):
    pass


class BenchmarkCaseTimedOut(BenchmarkExecutionError):
    pass


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class BenchmarkRunRequest(_FrozenModel):
    subject: Literal["baseline", "candidate"]
    subject_snapshot_id: str = Field(pattern=r"^self-cognition-[0-9a-f]{64}$")
    candidate_commit: str | None = None
    benchmark_pack_id: str = Field(pattern=r"^benchmark-pack-[0-9a-f]{64}$")
    case: BenchmarkCase
    timeout_seconds: float = Field(gt=0.0)


class BenchmarkCaseResult(_FrozenModel):
    case_id: str = Field(min_length=1)
    metrics: tuple[MetricValue, ...] = Field(min_length=1)
    hard_gate_results: tuple[HardGateResult, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_unique_results(self) -> Self:
        metric_names = [metric.metric for metric in self.metrics]
        gate_names = [gate.gate for gate in self.hard_gate_results]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("benchmark case metric names must be unique")
        if len(gate_names) != len(set(gate_names)):
            raise ValueError("benchmark case hard gate names must be unique")
        return self


class BenchmarkRunner(Protocol):
    def __call__(
        self,
        request: BenchmarkRunRequest,
    ) -> BenchmarkCaseResult | Mapping[str, object]: ...


class BenchmarkPackExecutor:
    """Run the same immutable benchmark pack for baseline and candidate subjects."""

    def __init__(
        self,
        runners: Mapping[str, BenchmarkRunner],
        *,
        case_timeout_seconds: float = 30.0,
        executor_version: str = DEFAULT_BENCHMARK_EXECUTOR_VERSION,
    ) -> None:
        if case_timeout_seconds <= 0:
            raise ValueError("case_timeout_seconds must be positive")
        version = str(executor_version).strip()
        if not version:
            raise ValueError("executor_version must not be empty")
        self.runners = {str(name).strip(): runner for name, runner in runners.items()}
        if any(not name for name in self.runners):
            raise ValueError("runner names must not be empty")
        if any(not callable(runner) for runner in self.runners.values()):
            raise ValueError("benchmark runners must be callable")
        self.case_timeout_seconds = float(case_timeout_seconds)
        self.executor_version = version

    def execute(
        self,
        *,
        benchmark_pack: BenchmarkPack,
        experiment_spec: ExperimentSpec,
        scoring_policy: ScoringPolicy,
        completed_at: datetime | None = None,
    ) -> ExperimentResult:
        self._validate_contract_links(
            benchmark_pack=benchmark_pack,
            experiment_spec=experiment_spec,
            scoring_policy=scoring_policy,
        )
        baseline_results = self._execute_subject(
            subject="baseline",
            snapshot_id=experiment_spec.baseline_snapshot_id,
            candidate_commit=None,
            benchmark_pack=benchmark_pack,
        )
        candidate_results = self._execute_subject(
            subject="candidate",
            snapshot_id=experiment_spec.candidate_snapshot_id,
            candidate_commit=experiment_spec.candidate_commit,
            benchmark_pack=benchmark_pack,
        )
        self._validate_result_shapes(baseline_results, candidate_results)

        baseline_metrics = self._aggregate_metrics(baseline_results)
        candidate_metrics = self._aggregate_metrics(candidate_results)
        baseline_by_name = {metric.metric: metric for metric in baseline_metrics}
        candidate_by_name = {metric.metric: metric for metric in candidate_metrics}
        deltas = tuple(
            MetricDelta(
                metric=name,
                delta=candidate_by_name[name].value - baseline_by_name[name].value,
            )
            for name in sorted(baseline_by_name)
        )
        delta_by_name = {metric.metric: metric.delta for metric in deltas}
        hard_gates = self._aggregate_hard_gates(
            benchmark_pack=benchmark_pack,
            scoring_policy=scoring_policy,
            results=(*baseline_results, *candidate_results),
        )
        regressions, has_disallowed_regression = self._regressions(
            experiment_spec=experiment_spec,
            metric_deltas=delta_by_name,
        )
        score = self._score(
            experiment_spec=experiment_spec,
            scoring_policy=scoring_policy,
            candidate_metrics=candidate_by_name,
            metric_deltas=delta_by_name,
        )
        all_gates_passed = all(gate.passed for gate in hard_gates)
        if not all_gates_passed or has_disallowed_regression:
            verdict = "reject"
        elif score >= scoring_policy.promote_threshold:
            verdict = "promote"
        elif score >= scoring_policy.observe_threshold:
            verdict = "observe"
        else:
            verdict = "reject"

        completed = _as_aware(completed_at or datetime.now(timezone.utc), "completed_at")
        completed_pairs = min(len(baseline_results), len(candidate_results))
        confidence = completed_pairs / len(benchmark_pack.cases)
        return ExperimentResult.create(
            experiment_spec_id=experiment_spec.experiment_spec_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            metric_deltas=deltas,
            regressions=regressions,
            confidence=confidence,
            hard_gate_results=hard_gates,
            verdict=verdict,
            completed_at=completed,
        )

    def execute_and_store(
        self,
        repository: EvaluationRepository,
        *,
        benchmark_pack: BenchmarkPack,
        experiment_spec: ExperimentSpec,
        scoring_policy: ScoringPolicy,
        completed_at: datetime | None = None,
    ) -> ExperimentResult:
        result = self.execute(
            benchmark_pack=benchmark_pack,
            experiment_spec=experiment_spec,
            scoring_policy=scoring_policy,
            completed_at=completed_at,
        )
        return repository.put_experiment_result(result)

    def execute_from_repository(
        self,
        repository: EvaluationRepository,
        *,
        experiment_spec_id: str,
        completed_at: datetime | None = None,
    ) -> ExperimentResult:
        experiment_spec = repository.get_experiment_spec(experiment_spec_id)
        if experiment_spec is None:
            raise BenchmarkConfigurationError(
                f"experiment spec not found: {experiment_spec_id}"
            )
        benchmark_pack = repository.get_benchmark_pack(experiment_spec.benchmark_pack_id)
        if benchmark_pack is None:
            raise BenchmarkConfigurationError(
                f"benchmark pack not found: {experiment_spec.benchmark_pack_id}"
            )
        scoring_policy = repository.get_scoring_policy(experiment_spec.scoring_policy_id)
        if scoring_policy is None:
            raise BenchmarkConfigurationError(
                f"scoring policy not found: {experiment_spec.scoring_policy_id}"
            )
        return self.execute_and_store(
            repository,
            benchmark_pack=benchmark_pack,
            experiment_spec=experiment_spec,
            scoring_policy=scoring_policy,
            completed_at=completed_at,
        )

    def _validate_contract_links(
        self,
        *,
        benchmark_pack: BenchmarkPack,
        experiment_spec: ExperimentSpec,
        scoring_policy: ScoringPolicy,
    ) -> None:
        if experiment_spec.benchmark_pack_id != benchmark_pack.benchmark_pack_id:
            raise BenchmarkConfigurationError("experiment spec references a different benchmark pack")
        if experiment_spec.scoring_policy_id != scoring_policy.scoring_policy_id:
            raise BenchmarkConfigurationError("experiment spec references a different scoring policy")
        available_metrics = {target.metric for target in experiment_spec.target_metrics}
        missing_dimensions = [
            dimension.name
            for dimension in scoring_policy.dimensions
            if dimension.name not in available_metrics
        ]
        if missing_dimensions:
            raise BenchmarkConfigurationError(
                "scoring dimensions require matching target metrics: "
                + ", ".join(sorted(missing_dimensions))
            )
        missing_runners = sorted(
            {case.runner for case in benchmark_pack.cases if case.runner not in self.runners}
        )
        if missing_runners:
            raise BenchmarkConfigurationError(
                "benchmark runners are not registered: " + ", ".join(missing_runners)
            )

    def _execute_subject(
        self,
        *,
        subject: Literal["baseline", "candidate"],
        snapshot_id: str,
        candidate_commit: str | None,
        benchmark_pack: BenchmarkPack,
    ) -> tuple[BenchmarkCaseResult, ...]:
        results: list[BenchmarkCaseResult] = []
        for case in benchmark_pack.cases:
            request = BenchmarkRunRequest(
                subject=subject,
                subject_snapshot_id=snapshot_id,
                candidate_commit=candidate_commit,
                benchmark_pack_id=benchmark_pack.benchmark_pack_id,
                case=case,
                timeout_seconds=self.case_timeout_seconds,
            )
            results.append(self._run_case(self.runners[case.runner], request))
        return tuple(results)

    def _run_case(
        self,
        runner: BenchmarkRunner,
        request: BenchmarkRunRequest,
    ) -> BenchmarkCaseResult:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voidcube-benchmark")
        future = pool.submit(runner, request)
        try:
            raw_result = future.result(timeout=request.timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise BenchmarkCaseTimedOut(
                f"benchmark case timed out: {request.subject}:{request.case.case_id}"
            ) from exc
        except Exception as exc:
            pool.shutdown(wait=False, cancel_futures=True)
            raise BenchmarkCaseFailed(
                f"benchmark case failed: {request.subject}:{request.case.case_id}"
            ) from exc
        pool.shutdown(wait=True)
        try:
            result = (
                raw_result
                if isinstance(raw_result, BenchmarkCaseResult)
                else BenchmarkCaseResult.model_validate(raw_result)
            )
        except ValidationError as exc:
            raise BenchmarkCaseFailed(
                f"benchmark case returned an invalid result: {request.subject}:{request.case.case_id}"
            ) from exc
        if result.case_id != request.case.case_id:
            raise BenchmarkCaseFailed(
                f"benchmark case_id mismatch: expected {request.case.case_id}, got {result.case_id}"
            )
        return result

    def _validate_result_shapes(
        self,
        baseline_results: tuple[BenchmarkCaseResult, ...],
        candidate_results: tuple[BenchmarkCaseResult, ...],
    ) -> None:
        expected_metrics: dict[str, str] | None = None
        for baseline, candidate in zip(baseline_results, candidate_results, strict=True):
            baseline_shape = {metric.metric: metric.unit for metric in baseline.metrics}
            candidate_shape = {metric.metric: metric.unit for metric in candidate.metrics}
            if baseline_shape != candidate_shape:
                raise BenchmarkCaseFailed(
                    f"baseline/candidate metric shape differs for case {baseline.case_id}"
                )
            if expected_metrics is None:
                expected_metrics = baseline_shape
            elif baseline_shape != expected_metrics:
                raise BenchmarkCaseFailed("benchmark cases returned inconsistent metric shapes")

    def _aggregate_metrics(
        self,
        results: tuple[BenchmarkCaseResult, ...],
    ) -> tuple[MetricValue, ...]:
        totals: dict[str, float] = {}
        units: dict[str, str] = {}
        for result in results:
            for metric in result.metrics:
                totals[metric.metric] = totals.get(metric.metric, 0.0) + metric.value
                units[metric.metric] = metric.unit
        return tuple(
            MetricValue(
                metric=name,
                value=totals[name] / len(results),
                unit=units[name],
            )
            for name in sorted(totals)
        )

    def _aggregate_hard_gates(
        self,
        *,
        benchmark_pack: BenchmarkPack,
        scoring_policy: ScoringPolicy,
        results: tuple[BenchmarkCaseResult, ...],
    ) -> tuple[HardGateResult, ...]:
        by_result = [
            {gate.gate: gate for gate in result.hard_gate_results}
            for result in results
        ]
        gate_names = {
            gate_name
            for gates in by_result
            for gate_name in gates
        } | set(scoring_policy.required_hard_gates)
        gate_names.discard(BENCHMARK_CONSISTENCY_GATE)
        aggregated: list[HardGateResult] = []
        for gate_name in sorted(gate_names):
            gate_results = [gates.get(gate_name) for gates in by_result]
            required = gate_name in scoring_policy.required_hard_gates
            passed = all(gate is not None and gate.passed for gate in gate_results)
            if not required:
                passed = all(gate.passed for gate in gate_results if gate is not None)
            evidence_refs = {
                evidence
                for gate in gate_results
                if gate is not None
                for evidence in gate.evidence_refs
            }
            if required and any(gate is None for gate in gate_results):
                evidence_refs.add("benchmark-executor:missing-required-gate")
            aggregated.append(
                HardGateResult(
                    gate=gate_name,
                    passed=passed,
                    evidence_refs=tuple(sorted(evidence_refs)),
                )
            )
        aggregated.append(
            HardGateResult(
                gate=BENCHMARK_CONSISTENCY_GATE,
                passed=True,
                evidence_refs=(
                    benchmark_pack.benchmark_pack_id,
                    f"executor:{self.executor_version}",
                    *sorted(
                        evidence
                        for result in results
                        for evidence in result.evidence_refs
                    ),
                ),
            )
        )
        return tuple(sorted(aggregated, key=lambda gate: gate.gate))

    def _regressions(
        self,
        *,
        experiment_spec: ExperimentSpec,
        metric_deltas: Mapping[str, float],
    ) -> tuple[tuple[Regression, ...], bool]:
        targets = {target.metric: target for target in experiment_spec.target_metrics}
        allowed = {
            regression.metric: regression.maximum_delta
            for regression in experiment_spec.allowed_regressions
        }
        regressions: list[Regression] = []
        disallowed = False
        for metric, delta in sorted(metric_deltas.items()):
            target = targets.get(metric)
            objective = target.objective if target is not None else "maintain"
            if objective == "increase":
                magnitude = max(0.0, -delta)
            elif objective == "decrease":
                magnitude = max(0.0, delta)
            else:
                magnitude = abs(delta)
            allowed_delta = allowed.get(metric, 0.0)
            if magnitude > 0:
                regressions.append(
                    Regression(
                        metric=metric,
                        observed_delta=magnitude,
                        allowed_delta=allowed_delta,
                    )
                )
            if magnitude > allowed_delta + 1e-12:
                disallowed = True
        return tuple(regressions), disallowed

    def _score(
        self,
        *,
        experiment_spec: ExperimentSpec,
        scoring_policy: ScoringPolicy,
        candidate_metrics: Mapping[str, MetricValue],
        metric_deltas: Mapping[str, float],
    ) -> float:
        targets = {target.metric: target for target in experiment_spec.target_metrics}
        score = 0.0
        for dimension in scoring_policy.dimensions:
            target = targets[dimension.name]
            candidate = candidate_metrics.get(dimension.name)
            if candidate is None:
                raise BenchmarkCaseFailed(
                    f"benchmark result is missing scoring metric: {dimension.name}"
                )
            delta = metric_deltas[dimension.name]
            if target.target_value is not None:
                if target.objective == "increase":
                    passed = candidate.value >= target.target_value
                elif target.objective == "decrease":
                    passed = candidate.value <= target.target_value
                else:
                    passed = abs(candidate.value - target.target_value) <= 1e-12
                dimension_score = 1.0 if passed else 0.0
            elif target.objective == "increase":
                dimension_score = 1.0 if delta > 0 else 0.5 if abs(delta) <= 1e-12 else 0.0
            elif target.objective == "decrease":
                dimension_score = 1.0 if delta < 0 else 0.5 if abs(delta) <= 1e-12 else 0.0
            else:
                allowed = next(
                    (
                        item.maximum_delta
                        for item in experiment_spec.allowed_regressions
                        if item.metric == dimension.name
                    ),
                    0.0,
                )
                dimension_score = 1.0 if abs(delta) <= allowed + 1e-12 else 0.0
            score += dimension.weight * dimension_score
        return score


def _as_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BenchmarkConfigurationError(f"{field_name} must include a timezone")
    return value


__all__ = [
    "BENCHMARK_CONSISTENCY_GATE",
    "DEFAULT_BENCHMARK_EXECUTOR_VERSION",
    "BenchmarkCaseFailed",
    "BenchmarkCaseResult",
    "BenchmarkCaseTimedOut",
    "BenchmarkConfigurationError",
    "BenchmarkExecutionError",
    "BenchmarkPackExecutor",
    "BenchmarkRunRequest",
    "BenchmarkRunner",
]
