"""Controlled BenchmarkPack execution without governance or body mutation."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from systems.evolution_evaluation.models import (
    BenchmarkCase,
    BenchmarkCaseExecutionEvidence,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    ExecutionEnvironmentIdentity,
    ExecutionEnvironmentManifest,
    ExperimentResult,
    ExperimentSpec,
    HardGateResult,
    MetricDelta,
    MetricValue,
    Regression,
    ScoringPolicy,
    SubjectCheckoutEvidence,
    benchmark_case_supports_platform,
)
from systems.evolution_evaluation.repository import EvaluationRepository


DEFAULT_BENCHMARK_EXECUTOR_VERSION = "benchmark-executor/1"
BENCHMARK_CONSISTENCY_GATE = "benchmark_pack_consistency"
EXECUTION_ENVIRONMENT_GATE = "execution_environment_coverage"


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
    validation_platform: str = Field(min_length=1)


class BenchmarkCaseResult(_FrozenModel):
    case_id: str = Field(min_length=1)
    metrics: tuple[MetricValue, ...] = Field(min_length=1)
    execution_environment: ExecutionEnvironmentManifest
    hard_gate_results: tuple[HardGateResult, ...] = ()
    command_evidence: tuple[BenchmarkCommandEvidence, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    subject_checkout: SubjectCheckoutEvidence | None = None
    validation_platform: str | None = None

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
        platform_runners: Mapping[str, Mapping[str, BenchmarkRunner]] | None = None,
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
        self.platform_runners = {
            str(platform).strip().lower(): {
                str(name).strip(): runner for name, runner in platform_map.items()
            }
            for platform, platform_map in dict(platform_runners or {}).items()
        }
        if any(
            not platform
            or any(not name or not callable(runner) for name, runner in runners.items())
            for platform, runners in self.platform_runners.items()
        ):
            raise ValueError("platform runner names and callables must be valid")
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
        validation_platforms = tuple(scoring_policy.required_validation_platforms)
        baseline_results = self._execute_subject(
            subject="baseline",
            snapshot_id=experiment_spec.baseline_snapshot_id,
            candidate_commit=None,
            benchmark_pack=benchmark_pack,
            validation_platforms=validation_platforms,
        )
        candidate_results = self._execute_subject(
            subject="candidate",
            snapshot_id=experiment_spec.candidate_snapshot_id,
            candidate_commit=experiment_spec.candidate_commit,
            benchmark_pack=benchmark_pack,
            validation_platforms=validation_platforms,
        )
        self._validate_result_shapes(baseline_results, candidate_results)
        (
            execution_environment,
            environment_identity,
            execution_environments,
            environment_identities,
            subject_checkouts,
        ) = (
            self._resolve_execution_environment(
                baseline_results=baseline_results,
                candidate_results=candidate_results,
            )
        )
        benchmark_case_evidence = self._build_case_evidence(
            baseline_results=baseline_results,
            candidate_results=candidate_results,
            subject_checkouts=subject_checkouts,
        )
        expected_identity_id = experiment_spec.execution_environment_identity_id
        if expected_identity_id is not None and (
            len(environment_identities) != 1
            or expected_identity_id
            != environment_identity.execution_environment_identity_id
        ):
            raise BenchmarkConfigurationError(
                "benchmark results do not match the experiment environment identity"
            )

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
        covered_platforms = {
            platform
            for environment in execution_environments
            for platform in environment.validated_platforms
        }
        required_platforms = set(scoring_policy.required_validation_platforms)
        missing_platforms = sorted(required_platforms - covered_platforms)
        hard_gates = tuple(
            sorted(
                (
                    *hard_gates,
                    HardGateResult(
                        gate=EXECUTION_ENVIRONMENT_GATE,
                        passed=not missing_platforms,
                        evidence_refs=(
                            *(
                                environment.execution_environment_id
                                for environment in execution_environments
                            ),
                            *(
                                f"missing-platform:{item}"
                                for item in missing_platforms
                            ),
                        ),
                    ),
                ),
                key=lambda gate: gate.gate,
            )
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
        expected_pairs = sum(
            1
            for platform in validation_platforms
            for case in benchmark_pack.cases
            if benchmark_case_supports_platform(case, platform)
        )
        confidence = completed_pairs / expected_pairs
        return ExperimentResult.create(
            experiment_spec_id=experiment_spec.experiment_spec_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            metric_deltas=deltas,
            regressions=regressions,
            confidence=confidence,
            hard_gate_results=hard_gates,
            execution_environment=execution_environment,
            verdict=verdict,
            completed_at=completed,
            execution_environment_identity=environment_identity,
            execution_environments=execution_environments,
            execution_environment_identities=environment_identities,
            subject_checkouts=subject_checkouts,
            benchmark_case_evidence=benchmark_case_evidence,
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
        platforms = tuple(scoring_policy.required_validation_platforms)
        cases_by_platform = {
            platform: tuple(
                case
                for case in benchmark_pack.cases
                if benchmark_case_supports_platform(case, platform)
            )
            for platform in platforms
        }
        empty_platforms = sorted(
            platform for platform, cases in cases_by_platform.items() if not cases
        )
        if empty_platforms:
            raise BenchmarkConfigurationError(
                "benchmark pack has no cases for selected platforms: "
                + ", ".join(empty_platforms)
            )
        if len(platforms) == 1 and not self.platform_runners:
            missing_runners = sorted(
                {
                    case.runner
                    for case in cases_by_platform[platforms[0]]
                    if case.runner not in self.runners
                }
            )
        else:
            missing_runners = sorted(
                f"{platform}:{case.runner}"
                for platform in platforms
                for case in cases_by_platform[platform]
                if case.runner not in self.platform_runners.get(platform, {})
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
        validation_platforms: tuple[str, ...],
    ) -> tuple[BenchmarkCaseResult, ...]:
        results: list[BenchmarkCaseResult] = []
        for platform in validation_platforms:
            platform_map = self.platform_runners.get(platform)
            for case in benchmark_pack.cases:
                if not benchmark_case_supports_platform(case, platform):
                    continue
                request = BenchmarkRunRequest(
                    subject=subject,
                    subject_snapshot_id=snapshot_id,
                    candidate_commit=candidate_commit,
                    benchmark_pack_id=benchmark_pack.benchmark_pack_id,
                    case=case,
                    timeout_seconds=self.case_timeout_seconds,
                    validation_platform=platform,
                )
                runner = (
                    platform_map[case.runner]
                    if platform_map is not None
                    else self.runners[case.runner]
                )
                results.append(self._run_case(runner, request))
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
        if result.execution_environment.validated_platforms != (
            request.validation_platform,
        ):
            raise BenchmarkCaseFailed(
                "benchmark runner returned evidence for a different platform: "
                f"expected {request.validation_platform}"
            )
        return result.model_copy(
            update={"validation_platform": request.validation_platform}
        )

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

    @staticmethod
    def _resolve_execution_environment(
        *,
        baseline_results: tuple[BenchmarkCaseResult, ...],
        candidate_results: tuple[BenchmarkCaseResult, ...],
    ) -> tuple[
        ExecutionEnvironmentManifest,
        ExecutionEnvironmentIdentity,
        tuple[ExecutionEnvironmentManifest, ...],
        tuple[ExecutionEnvironmentIdentity, ...],
        tuple[SubjectCheckoutEvidence, ...],
    ]:
        grouped = (
            ("baseline", baseline_results),
            ("candidate", candidate_results),
        )
        identities_by_platform: dict[str, ExecutionEnvironmentIdentity] = {}
        manifests_by_subject_platform: dict[
            tuple[str, str], list[ExecutionEnvironmentManifest]
        ] = {}
        all_manifests: dict[str, ExecutionEnvironmentManifest] = {}
        explicit_checkouts: dict[tuple[str, str], SubjectCheckoutEvidence] = {}
        for subject, results in grouped:
            if not results:
                raise BenchmarkCaseFailed(f"benchmark returned no {subject} results")
            for result in results:
                manifest = result.execution_environment
                identity = manifest.identity()
                platform = str(result.validation_platform or "").strip().lower()
                if not platform or manifest.validated_platforms != (platform,):
                    raise BenchmarkCaseFailed(
                        "benchmark result is missing its selected platform binding"
                    )
                prior_identity = identities_by_platform.get(platform)
                if prior_identity is not None and prior_identity != identity:
                    raise BenchmarkCaseFailed(
                        f"baseline and candidate must use one {platform} environment"
                    )
                identities_by_platform[platform] = identity
                all_manifests[manifest.execution_environment_id] = manifest
                key = (subject, platform)
                manifests_by_subject_platform.setdefault(key, []).append(manifest)
                checkout = result.subject_checkout
                if checkout is not None:
                    if checkout.subject != subject:
                        raise BenchmarkCaseFailed(
                            f"subject checkout mismatch: expected {subject}, got {checkout.subject}"
                        )
                    if (
                        checkout.execution_environment_identity_id
                        != identity.execution_environment_identity_id
                    ):
                        raise BenchmarkCaseFailed(
                            "subject checkout references a different environment identity"
                        )
                    prior = explicit_checkouts.get(key)
                    if prior is not None and _checkout_binding(prior) != _checkout_binding(
                        checkout
                    ):
                        raise BenchmarkCaseFailed(
                            f"{subject}:{platform} returned inconsistent checkout evidence"
                        )
                    if prior is None or checkout.checked_out_at < prior.checked_out_at:
                        explicit_checkouts[key] = checkout
        platforms = tuple(sorted(identities_by_platform))
        if not platforms:
            raise BenchmarkCaseFailed(
                "benchmark returned no execution environment identities"
            )
        subject_checkouts: list[SubjectCheckoutEvidence] = []
        for platform in platforms:
            identity = identities_by_platform[platform]
            for subject in ("baseline", "candidate"):
                key = (subject, platform)
                manifests = manifests_by_subject_platform.get(key, [])
                if not manifests:
                    raise BenchmarkCaseFailed(
                        f"benchmark returned no {subject}:{platform} results"
                    )
                heads = {manifest.repository_head.lower() for manifest in manifests}
                if len(heads) != 1:
                    raise BenchmarkCaseFailed(
                        f"{subject}:{platform} returned inconsistent checkout HEADs"
                    )
                checkout = explicit_checkouts.get(key)
                if checkout is None:
                    checkout = SubjectCheckoutEvidence.create(
                        subject=subject,
                        commit=next(iter(heads)),
                        worktree_path=manifests[0].execution_workspace_path,
                        execution_environment_identity_id=(
                            identity.execution_environment_identity_id
                        ),
                        checked_out_at=datetime.now(timezone.utc),
                    )
                elif checkout.commit.lower() != next(iter(heads)):
                    raise BenchmarkCaseFailed(
                        f"{subject}:{platform} checkout does not match manifest HEAD"
                    )
                subject_checkouts.append(checkout)
        representative_result = next(
            (
                result
                for result in candidate_results
                if result.validation_platform == "windows"
            ),
            candidate_results[0],
        )
        representative = representative_result.execution_environment
        representative_identity = representative.identity()
        environments = tuple(
            sorted(
                all_manifests.values(),
                key=lambda item: (
                    item.validated_platforms[0],
                    item.repository_head,
                    item.execution_environment_id,
                ),
            )
        )
        identities = tuple(
            identities_by_platform[platform] for platform in platforms
        )
        return (
            representative,
            representative_identity,
            environments,
            identities,
            tuple(
                sorted(
                    subject_checkouts,
                    key=lambda item: (
                        item.execution_environment_identity_id,
                        item.subject,
                    ),
                )
            ),
        )

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

    @staticmethod
    def _build_case_evidence(
        *,
        baseline_results: tuple[BenchmarkCaseResult, ...],
        candidate_results: tuple[BenchmarkCaseResult, ...],
        subject_checkouts: tuple[SubjectCheckoutEvidence, ...],
    ) -> tuple[BenchmarkCaseExecutionEvidence, ...]:
        checkouts = {
            (item.subject, item.execution_environment_identity_id): item
            for item in subject_checkouts
        }
        evidence: list[BenchmarkCaseExecutionEvidence] = []
        for subject, results in (
            ("baseline", baseline_results),
            ("candidate", candidate_results),
        ):
            for result in results:
                identity_id = (
                    result.execution_environment.identity().execution_environment_identity_id
                )
                checkout = checkouts[(subject, identity_id)]
                evidence.append(
                    BenchmarkCaseExecutionEvidence(
                        subject=subject,
                        case_id=result.case_id,
                        commands=result.command_evidence,
                        execution_environment_id=(
                            result.execution_environment.execution_environment_id
                        ),
                        execution_environment_identity_id=(
                            identity_id
                        ),
                        subject_checkout_evidence_id=(
                            checkout.subject_checkout_evidence_id
                        ),
                        evidence_refs=result.evidence_refs,
                    )
                )
        return tuple(evidence)

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
        gate_names.discard(EXECUTION_ENVIRONMENT_GATE)
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


def _checkout_binding(checkout: SubjectCheckoutEvidence) -> tuple[str, str, str, str]:
    return (
        checkout.subject,
        checkout.commit.lower(),
        checkout.worktree_path,
        checkout.execution_environment_identity_id,
    )


__all__ = [
    "BENCHMARK_CONSISTENCY_GATE",
    "EXECUTION_ENVIRONMENT_GATE",
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
