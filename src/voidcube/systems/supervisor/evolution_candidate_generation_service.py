"""Recoverable application service for authored and evaluated candidates."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from ..evolution_authoring import (
    AIAgentAuthoringAdapter,
    AuthoringAgent,
    EvolutionAuthoringExecutor,
    EvolutionAuthoringRepository,
    EvolutionAuthoringResult,
    EvolutionAuthoringSpec,
    JsonEvolutionAuthoringRepository,
    candidate_ref_for_task,
)
from ..evolution_candidate_generation import (
    EvolutionCandidateGenerationRequest,
    EvolutionCandidateGenerationState,
    JsonEvolutionCandidateGenerationRepository,
)
from ..evolution_evaluation import (
    BenchmarkExecutionError,
    EvaluationRepository,
    MetricTarget,
    NATIVE_COMPATIBILITY_METRIC,
    create_native_first_benchmark_pack,
    create_native_first_executor_factory,
    create_native_first_scoring_policy,
    select_benchmark_platforms,
)
from ..self_cognition import SelfCognitionCollector, SelfCognitionSnapshot
from .evolution_candidate_evaluation_service import (
    EvolutionCandidateEvaluationBlocked,
    EvolutionCandidateEvaluationOutcome,
    EvolutionCandidateEvaluationService,
)


CandidateMaterializer = Callable[..., Any]


class CandidateEvaluationService(Protocol):
    evaluation_repository: EvaluationRepository

    def evaluate(self, **kwargs: Any) -> EvolutionCandidateEvaluationOutcome: ...

    def resume(
        self,
        experiment_spec_id: str,
        *,
        completed_at: datetime | None = None,
    ) -> EvolutionCandidateEvaluationOutcome: ...


@dataclass(frozen=True, slots=True)
class EvolutionCandidateGenerationOutcome:
    state: EvolutionCandidateGenerationState
    authoring_result: EvolutionAuthoringResult | None = None
    evaluation_outcome: EvolutionCandidateEvaluationOutcome | None = None
    busy: bool = False


class EvolutionCandidateGenerationService:
    """Advance one persisted candidate cycle without creating a body task."""

    def __init__(
        self,
        repository: str | Path,
        *,
        candidate_repository: JsonEvolutionCandidateGenerationRepository,
        authoring_repository: EvolutionAuthoringRepository,
        authoring_executor: EvolutionAuthoringExecutor,
        authoring_agent: AuthoringAgent,
        evaluation_service: CandidateEvaluationService,
        snapshot_worktree_root: str | Path,
        snapshot_collector: Callable[
            [Path, str, datetime], SelfCognitionSnapshot
        ]
        | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_duration: timedelta = timedelta(minutes=30),
        blocked_cooldown: timedelta = timedelta(minutes=15),
        failed_cooldown: timedelta = timedelta(hours=1),
        materialize_candidate_commit: CandidateMaterializer | None = None,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        if not (self.repository / ".git").exists():
            raise ValueError(
                f"candidate generation repository is not a Git worktree: {self.repository}"
            )
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if blocked_cooldown < timedelta(0) or failed_cooldown < timedelta(0):
            raise ValueError("candidate cooldowns cannot be negative")
        self.candidate_repository = candidate_repository
        self.authoring_repository = authoring_repository
        self.authoring_executor = authoring_executor
        self.authoring_agent = authoring_agent
        self.evaluation_service = evaluation_service
        self.snapshot_worktree_root = Path(snapshot_worktree_root).expanduser().resolve()
        self._snapshot_collector = snapshot_collector or _collect_self_cognition
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_duration = lease_duration
        self.blocked_cooldown = blocked_cooldown
        self.failed_cooldown = failed_cooldown
        self._materialize_candidate_commit = materialize_candidate_commit

    @classmethod
    def from_root(
        cls,
        repository: str | Path,
        foundation_root: str | Path,
        *,
        authoring_agent: AuthoringAgent | None = None,
        python_executable: str | Path | None = None,
        capability_policy_profile: str | None = None,
        body_registry: Any | None = None,
    ) -> "EvolutionCandidateGenerationService":
        root = Path(foundation_root).expanduser().resolve()
        project_python = Path(python_executable or sys.executable).resolve()
        authoring_repository = JsonEvolutionAuthoringRepository(root / "authoring")
        evaluation_factory = create_native_first_executor_factory(
            repository,
            worktree_root=root / "worktrees" / "evaluation",
            python_executable=project_python,
        )
        evaluation_service = EvolutionCandidateEvaluationService.from_root(
            repository,
            root,
            benchmark_executor_factory=evaluation_factory,
            capability_policy_profile=capability_policy_profile,
        )
        return cls(
            repository,
            candidate_repository=JsonEvolutionCandidateGenerationRepository(
                root / "candidate-generation"
            ),
            authoring_repository=authoring_repository,
            authoring_executor=EvolutionAuthoringExecutor(
                repository,
                worktree_root=root / "worktrees" / "authoring",
                python_executable=project_python,
            ),
            authoring_agent=authoring_agent or AIAgentAuthoringAdapter(),
            evaluation_service=evaluation_service,
            snapshot_worktree_root=root / "worktrees" / "snapshots",
            materialize_candidate_commit=(
                body_registry.materialize_candidate_commit
                if body_registry is not None
                else None
            ),
        )

    async def execute(
        self,
        request_id: str,
        *,
        lease_owner: str,
    ) -> EvolutionCandidateGenerationOutcome:
        owner = str(lease_owner or "").strip()
        if not owner:
            raise ValueError("lease_owner must not be empty")
        request = self.candidate_repository.get_request(request_id)
        state = self.candidate_repository.get_current_state(request_id)
        if request is None or state is None:
            raise KeyError(f"unknown candidate request: {request_id}")
        if state.status == "authorized":
            return EvolutionCandidateGenerationOutcome(state=state)

        authoring_result = self._find_authoring_result(state.authoring_task_id)
        if state.status == "authoring" and authoring_result is not None:
            recovered = self._reconcile_authoring_result(
                state=state,
                result=authoring_result,
                lease_owner=owner,
            )
            if isinstance(recovered, EvolutionCandidateGenerationOutcome):
                return recovered
            state = recovered

        if state.status in {"pending", "blocked", "failed"} or (
            state.status == "authoring" and authoring_result is None
        ):
            if state.status == "authoring" and not self._lease_expired(state):
                if state.lease_owner != owner:
                    return EvolutionCandidateGenerationOutcome(state=state, busy=True)
            else:
                if state.status == "authoring" and self._candidate_ref_exists(
                    state.authoring_task_id
                ):
                    orphan = self._orphan_candidate_result(request, state)
                    self.authoring_repository.put(orphan)
                    failed = self._mark_authoring_failure(state, orphan)
                    return EvolutionCandidateGenerationOutcome(
                        state=failed,
                        authoring_result=orphan,
                    )
                claimed = self.candidate_repository.claim_authoring(
                    request_id,
                    lease_owner=owner,
                    claimed_at=self._now(),
                    lease_expires_at=self._lease_expiry(),
                )
                if claimed is None:
                    current = self._require_state(request_id)
                    return EvolutionCandidateGenerationOutcome(
                        state=current,
                        busy=current.status in {"authoring", "evaluating"},
                    )
                state = claimed

        if state.status == "authoring":
            if state.lease_owner != owner:
                return EvolutionCandidateGenerationOutcome(state=state, busy=True)
            result = await self._author(request, state)
            self.authoring_repository.put(result)
            if result.status != "candidate_created":
                failed = self._mark_authoring_failure(state, result)
                return EvolutionCandidateGenerationOutcome(
                    state=failed,
                    authoring_result=result,
                )
            state = self.candidate_repository.begin_evaluation(
                request_id,
                attempt_id=str(state.attempt_id),
                authoring_result_id=result.authoring_result_id,
                lease_owner=owner,
                started_at=self._now(),
                lease_expires_at=self._lease_expiry(),
            )
            authoring_result = result

        if state.status != "evaluating":
            return EvolutionCandidateGenerationOutcome(
                state=state,
                authoring_result=authoring_result,
            )
        if self._lease_expired(state):
            claimed = self.candidate_repository.claim_evaluation(
                request_id,
                lease_owner=owner,
                claimed_at=self._now(),
                lease_expires_at=self._lease_expiry(),
            )
            if claimed is None:
                current = self._require_state(request_id)
                return EvolutionCandidateGenerationOutcome(
                    state=current,
                    busy=current.status in {"authoring", "evaluating"},
                )
            state = claimed
        elif state.lease_owner != owner:
            return EvolutionCandidateGenerationOutcome(
                state=state,
                authoring_result=authoring_result,
                busy=True,
            )
        if authoring_result is None:
            authoring_result = self._require_authoring_result(state.authoring_result_id)
        return await self._evaluate(request, state, authoring_result)

    def _reconcile_authoring_result(
        self,
        *,
        state: EvolutionCandidateGenerationState,
        result: EvolutionAuthoringResult,
        lease_owner: str,
    ) -> EvolutionCandidateGenerationState | EvolutionCandidateGenerationOutcome:
        if result.status != "candidate_created":
            failed = self._mark_authoring_failure(state, result)
            return EvolutionCandidateGenerationOutcome(
                state=failed,
                authoring_result=result,
            )
        if state.lease_owner == lease_owner and not self._lease_expired(state):
            return self.candidate_repository.begin_evaluation(
                state.request_id,
                attempt_id=str(state.attempt_id),
                authoring_result_id=result.authoring_result_id,
                lease_owner=lease_owner,
                started_at=self._now(),
                lease_expires_at=self._lease_expiry(),
            )
        if not self._lease_expired(state):
            return EvolutionCandidateGenerationOutcome(
                state=state,
                authoring_result=result,
                busy=True,
            )
        return self.candidate_repository.recover_evaluation(
            state.request_id,
            attempt_id=str(state.attempt_id),
            authoring_result_id=result.authoring_result_id,
            lease_owner=lease_owner,
            resumed_at=self._now(),
            lease_expires_at=self._lease_expiry(),
        )

    async def _author(
        self,
        request: EvolutionCandidateGenerationRequest,
        state: EvolutionCandidateGenerationState,
    ) -> EvolutionAuthoringResult:
        started_at = self._now()
        preflight = self._preflight_error(request.baseline_commit)
        if preflight is not None:
            return EvolutionAuthoringResult.create(
                task_id=str(state.authoring_task_id),
                status="blocked",
                baseline_commit=request.baseline_commit,
                error_code=preflight[0],
                error_reason=preflight[1],
                started_at=started_at,
                finished_at=self._now(),
            )
        spec = EvolutionAuthoringSpec(
            task_id=str(state.authoring_task_id),
            objective=request.objective,
            improvement_hypothesis=request.improvement_hypothesis,
            baseline_commit=request.baseline_commit,
            allowed_paths=request.allowed_paths,
            forbidden_patterns=request.forbidden_patterns,
            max_files_changed=request.max_files_changed,
            test_commands=request.test_commands,
            command_timeout_seconds=request.command_timeout_seconds,
            commit_message=_commit_message(request.objective),
        )
        try:
            return await self.authoring_executor.execute(
                spec,
                agent=self.authoring_agent,
            )
        except Exception as exc:
            return EvolutionAuthoringResult.create(
                task_id=spec.task_id,
                status="authoring_failed",
                baseline_commit=spec.baseline_commit,
                error_code="authoring_service_error",
                error_reason=f"Authoring executor failed with {type(exc).__name__}.",
                started_at=started_at,
                finished_at=self._now(),
            )

    async def _evaluate(
        self,
        request: EvolutionCandidateGenerationRequest,
        state: EvolutionCandidateGenerationState,
        authoring: EvolutionAuthoringResult,
    ) -> EvolutionCandidateGenerationOutcome:
        try:
            spec_id = self._find_experiment_spec(authoring.authoring_result_id)
            if spec_id is not None:
                outcome = await asyncio.to_thread(
                    self.evaluation_service.resume,
                    spec_id,
                    completed_at=self._now(),
                )
            else:
                collected_at = self._now()
                baseline, candidate = await asyncio.to_thread(
                    self._collect_snapshots,
                    authoring,
                    collected_at,
                )
                selection = select_benchmark_platforms(
                    authoring.changed_files,
                    str(authoring.environment_dependency_fingerprint),
                    created_at=collected_at,
                )
                pack = create_native_first_benchmark_pack(created_at=collected_at)
                policy = create_native_first_scoring_policy(
                    selection.required_platforms,
                    created_at=collected_at,
                )
                target_metrics = _with_native_compatibility(request.target_metrics)
                outcome = await asyncio.to_thread(
                    self.evaluation_service.evaluate,
                    authoring_result=authoring,
                    baseline_snapshot=baseline,
                    candidate_snapshot=candidate,
                    benchmark_pack=pack,
                    scoring_policy=policy,
                    target_metrics=target_metrics,
                    hypothesis=request.improvement_hypothesis,
                    knowledge_ids=request.knowledge_ids,
                    created_at=collected_at,
                    completed_at=self._now(),
                )
        except EvolutionCandidateEvaluationBlocked as exc:
            failed = self._mark_evaluation_failure(
                state,
                status="blocked",
                error_code=exc.code,
                error_reason=exc.reason,
            )
            return EvolutionCandidateGenerationOutcome(
                state=failed,
                authoring_result=authoring,
            )
        except BenchmarkExecutionError as exc:
            failed = self._mark_evaluation_failure(
                state,
                status="blocked",
                error_code="evaluation_execution_blocked",
                error_reason=f"Evaluation was blocked by {type(exc).__name__}.",
            )
            return EvolutionCandidateGenerationOutcome(
                state=failed,
                authoring_result=authoring,
            )
        except Exception as exc:
            failed = self._mark_evaluation_failure(
                state,
                status="failed",
                error_code="evaluation_service_error",
                error_reason=f"Evaluation failed with {type(exc).__name__}.",
            )
            return EvolutionCandidateGenerationOutcome(
                state=failed,
                authoring_result=authoring,
            )

        result = outcome.experiment_result
        if result.verdict != "promote" or not outcome.governance_authorization.get(
            "authorized"
        ):
            failed = self._mark_evaluation_failure(
                state,
                status="failed",
                error_code="candidate_not_authorized",
                error_reason=f"Evaluation completed with verdict {result.verdict}.",
                experiment_result_id=result.experiment_result_id,
            )
            return EvolutionCandidateGenerationOutcome(
                state=failed,
                authoring_result=authoring,
                evaluation_outcome=outcome,
            )
        if self._materialize_candidate_commit is not None:
            try:
                self._materialize_candidate_commit(
                    request.target_body_slot_id,
                    baseline_commit=request.baseline_commit,
                    candidate_commit=str(authoring.candidate_commit),
                    changed_files=tuple(authoring.changed_files),
                    source_label=(
                        f"evaluated:{result.experiment_result_id}"
                    ),
                )
            except Exception as exc:
                failed = self._mark_evaluation_failure(
                    state,
                    status="failed",
                    error_code="candidate_materialization_failed",
                    error_reason=(
                        "Evaluation was authorized but the exact candidate could not "
                        f"be materialized: {type(exc).__name__}: {exc}"
                    ),
                    experiment_result_id=result.experiment_result_id,
                )
                return EvolutionCandidateGenerationOutcome(
                    state=failed,
                    authoring_result=authoring,
                    evaluation_outcome=outcome,
                )
        authorized = self.candidate_repository.mark_authorized(
            state.request_id,
            attempt_id=str(state.attempt_id),
            experiment_result_id=result.experiment_result_id,
            completed_at=self._now(),
        )
        return EvolutionCandidateGenerationOutcome(
            state=authorized,
            authoring_result=authoring,
            evaluation_outcome=outcome,
        )

    def _collect_snapshots(
        self,
        authoring: EvolutionAuthoringResult,
        collected_at: datetime,
    ) -> tuple[SelfCognitionSnapshot, SelfCognitionSnapshot]:
        if not authoring.candidate_commit:
            raise ValueError("authoring result has no candidate commit")
        snapshots = []
        for subject, commit in (
            ("baseline", authoring.baseline_commit),
            ("candidate", authoring.candidate_commit),
        ):
            target = self.snapshot_worktree_root / authoring.task_id / subject
            self._prepare_snapshot_worktree(target, commit)
            try:
                snapshot = self._snapshot_collector(target, subject, collected_at)
                if snapshot.git_commit.lower() != commit.lower():
                    raise RuntimeError(
                        f"{subject} self-cognition snapshot has the wrong Git commit"
                    )
                snapshots.append(snapshot)
            finally:
                self._remove_snapshot_worktree(target)
        return snapshots[0], snapshots[1]

    def _prepare_snapshot_worktree(self, target: Path, commit: str) -> None:
        if target.exists():
            self._remove_snapshot_worktree(target)
            if target.exists() and any(target.iterdir()):
                raise RuntimeError(f"stale snapshot worktree is not empty: {target}")
            if target.exists():
                target.rmdir()
        self._git("worktree", "prune", require_output=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "--detach", str(target), commit)

    def _remove_snapshot_worktree(self, target: Path) -> None:
        if not target.exists():
            return
        subprocess.run(
            ("git", "worktree", "remove", "--force", str(target)),
            cwd=self.repository,
            capture_output=True,
            timeout=60,
            check=False,
        )

    def _preflight_error(self, baseline_commit: str) -> tuple[str, str] | None:
        try:
            self._git("rev-parse", f"{baseline_commit}^{{commit}}")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return (
                "baseline_unavailable",
                f"Candidate baseline is not available in the repository: {type(exc).__name__}.",
            )
        try:
            status = self._git(
                "status",
                "--porcelain",
                "--untracked-files=all",
                require_output=False,
            )
            if status:
                return (
                    "main_repository_dirty",
                    "Main repository must be clean before candidate authoring.",
                )
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return (
                "main_repository_unavailable",
                f"Repository preflight failed with {type(exc).__name__}.",
            )
        return None

    def _git(self, *args: str, require_output: bool = True) -> str:
        result = subprocess.run(
            ("git", *args),
            cwd=self.repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        output = result.stdout.strip()
        if result.returncode != 0 or (require_output and not output):
            raise RuntimeError(f"Git command failed: {' '.join(args)}")
        return output

    def _find_authoring_result(
        self, task_id: str | None
    ) -> EvolutionAuthoringResult | None:
        if not task_id:
            return None
        matches = []
        for result_id in self.authoring_repository.list_ids():
            result = self.authoring_repository.get(result_id)
            if result is not None and result.task_id == task_id:
                matches.append(result)
        if len(matches) > 1:
            raise RuntimeError(f"multiple authoring results exist for task {task_id}")
        return matches[0] if matches else None

    def _candidate_ref_exists(self, task_id: str | None) -> bool:
        if not task_id:
            return False
        result = subprocess.run(
            (
                "git",
                "rev-parse",
                "--verify",
                f"{candidate_ref_for_task(task_id)}^{{commit}}",
            ),
            cwd=self.repository,
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0

    def _orphan_candidate_result(
        self,
        request: EvolutionCandidateGenerationRequest,
        state: EvolutionCandidateGenerationState,
    ) -> EvolutionAuthoringResult:
        timestamp = self._now()
        return EvolutionAuthoringResult.create(
            task_id=str(state.authoring_task_id),
            status="blocked",
            baseline_commit=request.baseline_commit,
            error_code="candidate_ref_without_result",
            error_reason=(
                "Candidate ref exists without immutable authoring evidence; "
                "the attempt cannot be reconstructed safely."
            ),
            started_at=timestamp,
            finished_at=timestamp,
        )

    def _require_authoring_result(
        self, result_id: str | None
    ) -> EvolutionAuthoringResult:
        if not result_id:
            raise RuntimeError("evaluating state has no authoring result ID")
        result = self.authoring_repository.get(result_id)
        if result is None:
            raise RuntimeError(f"authoring result not found: {result_id}")
        return result

    def _find_experiment_spec(self, authoring_result_id: str) -> str | None:
        matches = []
        repository = self.evaluation_service.evaluation_repository
        for spec_id in repository.list_ids("experiment_specs"):
            spec = repository.get_experiment_spec(spec_id)
            if spec is not None and spec.authoring_result_id == authoring_result_id:
                matches.append(spec_id)
        if len(matches) > 1:
            raise EvolutionCandidateEvaluationBlocked(
                "experiment_spec_ambiguous",
                "multiple experiment specs reference the same authoring result",
            )
        return matches[0] if matches else None

    def _mark_authoring_failure(
        self,
        state: EvolutionCandidateGenerationState,
        result: EvolutionAuthoringResult,
    ) -> EvolutionCandidateGenerationState:
        status = "blocked" if result.status == "blocked" else "failed"
        return self.candidate_repository.mark_failure(
            state.request_id,
            attempt_id=str(state.attempt_id),
            status=status,
            error_code=str(result.error_code),
            error_reason=str(result.error_reason),
            completed_at=self._now(),
            cooldown_until=self._now()
            + (self.blocked_cooldown if status == "blocked" else self.failed_cooldown),
            authoring_result_id=result.authoring_result_id,
        )

    def _mark_evaluation_failure(
        self,
        state: EvolutionCandidateGenerationState,
        *,
        status: str,
        error_code: str,
        error_reason: str,
        experiment_result_id: str | None = None,
    ) -> EvolutionCandidateGenerationState:
        return self.candidate_repository.mark_failure(
            state.request_id,
            attempt_id=str(state.attempt_id),
            status=status,
            error_code=error_code,
            error_reason=error_reason[:4000],
            completed_at=self._now(),
            cooldown_until=self._now()
            + (self.blocked_cooldown if status == "blocked" else self.failed_cooldown),
            experiment_result_id=experiment_result_id,
        )

    def _lease_expired(self, state: EvolutionCandidateGenerationState) -> bool:
        return state.lease_expires_at is None or state.lease_expires_at <= self._now()

    def _lease_expiry(self) -> datetime:
        return self._now() + self.lease_duration

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate generation clock must be timezone-aware")
        return value

    def _require_state(self, request_id: str) -> EvolutionCandidateGenerationState:
        state = self.candidate_repository.get_current_state(request_id)
        if state is None:
            raise RuntimeError(f"candidate state disappeared: {request_id}")
        return state


def _collect_self_cognition(
    worktree: Path,
    subject: str,
    collected_at: datetime,
) -> SelfCognitionSnapshot:
    return SelfCognitionCollector(
        worktree,
        body_id=f"evolution-{subject}",
    ).collect(collected_at=collected_at)


def _with_native_compatibility(
    target_metrics: tuple[MetricTarget, ...],
) -> tuple[MetricTarget, ...]:
    if any(item.metric == NATIVE_COMPATIBILITY_METRIC for item in target_metrics):
        return target_metrics
    return (
        *target_metrics,
        MetricTarget(metric=NATIVE_COMPATIBILITY_METRIC, objective="maintain"),
    )


def _commit_message(objective: str) -> str:
    normalized = " ".join(str(objective).split())
    prefix = "Autonomous evolution: "
    return (prefix + normalized)[:200].rstrip()


__all__ = [
    "EvolutionCandidateGenerationOutcome",
    "EvolutionCandidateGenerationService",
]
