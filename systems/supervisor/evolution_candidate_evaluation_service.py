"""Governed handoff from an authored candidate to immutable evaluation records."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from systems.evolution_authoring import (
    EvolutionAuthoringRepository,
    EvolutionAuthoringResult,
    JsonEvolutionAuthoringRepository,
)
from systems.evolution_boundary import normalize_repo_path
from systems.evolution_evaluation import (
    AllowedRegression,
    BenchmarkPack,
    BenchmarkPackExecutor,
    EvaluationRepository,
    ExperimentResult,
    ExperimentSpec,
    JsonEvaluationRepository,
    MetricTarget,
    ScoringPolicy,
)
from systems.research_knowledge import JsonKnowledgeRepository
from systems.self_cognition import (
    JsonSelfCognitionRepository,
    SelfCognitionRepository,
    SelfCognitionSnapshot,
)
from systems.supervisor.evolution_evaluation_governance import (
    EvolutionEvaluationGovernanceVerifier,
)


class EvolutionCandidateEvaluationBlocked(RuntimeError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class EvolutionCandidateEvaluationOutcome:
    experiment_spec: ExperimentSpec
    experiment_result: ExperimentResult
    governance_authorization: dict[str, Any]


class EvolutionCandidateEvaluationService:
    """Validate candidate provenance, execute BenchmarkPack, and request governance."""

    def __init__(
        self,
        repository: str | Path,
        *,
        authoring_repository: EvolutionAuthoringRepository,
        self_cognition_repository: SelfCognitionRepository,
        knowledge_repository: Any,
        evaluation_repository: EvaluationRepository,
        benchmark_executor: BenchmarkPackExecutor,
        governance_verifier: EvolutionEvaluationGovernanceVerifier,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        if not (self.repository / ".git").exists():
            raise ValueError(
                f"candidate evaluation repository is not a Git worktree: {self.repository}"
            )
        self.authoring_repository = authoring_repository
        self.self_cognition_repository = self_cognition_repository
        self.knowledge_repository = knowledge_repository
        self.evaluation_repository = evaluation_repository
        self.benchmark_executor = benchmark_executor
        self.governance_verifier = governance_verifier

    @classmethod
    def from_root(
        cls,
        repository: str | Path,
        foundation_root: str | Path,
        *,
        benchmark_executor: BenchmarkPackExecutor,
    ) -> "EvolutionCandidateEvaluationService":
        root = Path(foundation_root).expanduser().resolve()
        authoring_repository = JsonEvolutionAuthoringRepository(root / "authoring")
        self_cognition_repository = JsonSelfCognitionRepository(root / "self-cognition")
        knowledge_repository = JsonKnowledgeRepository(root / "knowledge")
        evaluation_repository = JsonEvaluationRepository(root / "evaluation")
        governance_verifier = EvolutionEvaluationGovernanceVerifier(
            evaluation_repository=evaluation_repository,
            knowledge_repository=knowledge_repository,
            self_cognition_repository=self_cognition_repository,
            authoring_repository=authoring_repository,
        )
        return cls(
            repository,
            authoring_repository=authoring_repository,
            self_cognition_repository=self_cognition_repository,
            knowledge_repository=knowledge_repository,
            evaluation_repository=evaluation_repository,
            benchmark_executor=benchmark_executor,
            governance_verifier=governance_verifier,
        )

    def evaluate(
        self,
        *,
        authoring_result: EvolutionAuthoringResult,
        baseline_snapshot: SelfCognitionSnapshot,
        candidate_snapshot: SelfCognitionSnapshot,
        benchmark_pack: BenchmarkPack,
        scoring_policy: ScoringPolicy,
        target_metrics: tuple[MetricTarget, ...],
        hypothesis: str,
        allowed_regressions: tuple[AllowedRegression, ...] = (),
        knowledge_ids: tuple[str, ...] = (),
        execution_environment_identity_id: str | None = None,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> EvolutionCandidateEvaluationOutcome:
        authoring = EvolutionAuthoringResult.model_validate(
            authoring_result.model_dump(mode="json")
        )
        baseline = SelfCognitionSnapshot.model_validate(
            baseline_snapshot.model_dump(mode="json")
        )
        candidate = SelfCognitionSnapshot.model_validate(
            candidate_snapshot.model_dump(mode="json")
        )
        pack = BenchmarkPack.model_validate(benchmark_pack.model_dump(mode="json"))
        policy = ScoringPolicy.model_validate(scoring_policy.model_dump(mode="json"))
        self._validate_authoring_result(authoring)
        self._validate_snapshots(authoring, baseline, candidate)
        self._validate_git_candidate(authoring)
        self._validate_knowledge(knowledge_ids)
        timestamp = created_at or datetime.now(timezone.utc)
        spec = ExperimentSpec.create(
            authoring_result_id=authoring.authoring_result_id,
            baseline_snapshot_id=baseline.snapshot_id,
            candidate_commit=str(authoring.candidate_commit),
            candidate_snapshot_id=candidate.snapshot_id,
            hypothesis=hypothesis,
            knowledge_ids=knowledge_ids,
            target_metrics=target_metrics,
            allowed_regressions=allowed_regressions,
            benchmark_pack_id=pack.benchmark_pack_id,
            scoring_policy_id=policy.scoring_policy_id,
            execution_environment_identity_id=execution_environment_identity_id,
            created_at=timestamp,
        )
        self.authoring_repository.put(authoring)
        self.self_cognition_repository.put(baseline)
        self.self_cognition_repository.put(candidate)
        self.evaluation_repository.put_benchmark_pack(pack)
        self.evaluation_repository.put_scoring_policy(policy)
        self.evaluation_repository.put_experiment_spec(spec)
        result = self.benchmark_executor.execute_from_repository(
            self.evaluation_repository,
            experiment_spec_id=spec.experiment_spec_id,
            completed_at=completed_at,
        )
        authorization = self.governance_verifier.verify(result.experiment_result_id)
        if result.verdict == "promote" and not authorization.get("authorized"):
            raise EvolutionCandidateEvaluationBlocked(
                "promote_result_not_authorized",
                str(
                    authorization.get("reason") or "governance rejected promote result"
                ),
            )
        return EvolutionCandidateEvaluationOutcome(
            experiment_spec=spec,
            experiment_result=result,
            governance_authorization=dict(authorization),
        )

    @staticmethod
    def _validate_authoring_result(result: EvolutionAuthoringResult) -> None:
        if result.status != "candidate_created":
            raise EvolutionCandidateEvaluationBlocked(
                "authoring_not_successful",
                f"authoring result status is {result.status}",
            )
        if not result.candidate_commit or not result.candidate_ref:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_provenance_missing",
                "successful authoring result is missing candidate commit or ref",
            )

    @staticmethod
    def _validate_snapshots(
        result: EvolutionAuthoringResult,
        baseline: SelfCognitionSnapshot,
        candidate: SelfCognitionSnapshot,
    ) -> None:
        if baseline.git_commit.strip().lower() != result.baseline_commit:
            raise EvolutionCandidateEvaluationBlocked(
                "baseline_snapshot_commit_mismatch",
                "baseline snapshot does not describe the authored baseline commit",
            )
        if candidate.git_commit.strip().lower() != result.candidate_commit:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_snapshot_commit_mismatch",
                "candidate snapshot does not describe the authored candidate commit",
            )

    def _validate_git_candidate(self, result: EvolutionAuthoringResult) -> None:
        candidate_ref = str(result.candidate_ref)
        ref_commit = self._git("rev-parse", "--verify", f"{candidate_ref}^{{commit}}")
        if ref_commit.lower() != result.candidate_commit:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_ref_commit_mismatch",
                "candidate ref does not point at the authored candidate commit",
            )
        parents = self._git("rev-list", "--parents", "-n", "1", ref_commit).split()
        if parents != [str(result.candidate_commit), result.baseline_commit]:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_lineage_mismatch",
                "candidate must be one commit whose sole parent is the authored baseline",
            )
        actual_files = self._git_bytes(
            "diff",
            "--name-only",
            "-z",
            f"{result.baseline_commit}..{result.candidate_commit}",
            "--",
        )
        changed_files = tuple(
            sorted(
                normalize_repo_path(item.decode("utf-8", errors="replace"))
                for item in actual_files.split(b"\0")
                if item
            )
        )
        if changed_files != result.changed_files:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_changed_files_mismatch",
                "candidate Git diff does not match authoring evidence",
            )

    def _validate_knowledge(self, knowledge_ids: tuple[str, ...]) -> None:
        for knowledge_id in knowledge_ids:
            try:
                record = self.knowledge_repository.get(knowledge_id)
            except Exception as exc:
                raise EvolutionCandidateEvaluationBlocked(
                    "knowledge_artifact_unreadable",
                    f"knowledge artifact could not be read: {knowledge_id}",
                ) from exc
            if record is None:
                raise EvolutionCandidateEvaluationBlocked(
                    "knowledge_artifact_missing",
                    f"knowledge artifact not found: {knowledge_id}",
                )

    def _git(self, *args: str) -> str:
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
        if result.returncode != 0 or not output:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_git_verification_failed",
                (result.stderr or result.stdout).strip()[:1000]
                or f"Git command failed: {' '.join(args)}",
            )
        return output

    def _git_bytes(self, *args: str) -> bytes:
        result = subprocess.run(
            ("git", *args),
            cwd=self.repository,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise EvolutionCandidateEvaluationBlocked(
                "candidate_git_verification_failed",
                result.stderr.decode("utf-8", errors="replace")[:1000],
            )
        return result.stdout


__all__ = [
    "EvolutionCandidateEvaluationBlocked",
    "EvolutionCandidateEvaluationOutcome",
    "EvolutionCandidateEvaluationService",
]
