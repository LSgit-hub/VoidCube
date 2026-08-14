"""Authoritative governance admission for evaluated body commits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from systems.evolution_evaluation import JsonEvaluationRepository
from systems.research_knowledge import JsonKnowledgeRepository
from systems.self_cognition import JsonSelfCognitionRepository


EVALUATION_GOVERNANCE_SCHEMA_VERSION = 1
_FULL_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


@dataclass(frozen=True, slots=True)
class EvolutionEvaluationGovernanceVerifier:
    """Resolve immutable evaluation records into a deployable authorization."""

    evaluation_repository: Any
    knowledge_repository: Any
    self_cognition_repository: Any

    @classmethod
    def from_root(cls, root: str | Path) -> "EvolutionEvaluationGovernanceVerifier":
        foundation_root = Path(root).resolve()
        return cls(
            evaluation_repository=JsonEvaluationRepository(
                foundation_root / "evaluation"
            ),
            knowledge_repository=JsonKnowledgeRepository(
                foundation_root / "knowledge"
            ),
            self_cognition_repository=JsonSelfCognitionRepository(
                foundation_root / "self-cognition"
            ),
        )

    def latest_authorization(self) -> dict[str, Any]:
        """Return the newest valid promote result, not merely the newest result."""

        try:
            result_ids = self.evaluation_repository.list_ids("experiment_results")
        except Exception as exc:
            return _rejection(
                "evaluation_repository_unavailable",
                error_type=type(exc).__name__,
            )
        if not result_ids:
            return _rejection("experiment_result_missing")

        records = []
        read_errors: list[str] = []
        for result_id in result_ids:
            try:
                record = self.evaluation_repository.get_experiment_result(result_id)
            except Exception as exc:
                read_errors.append(f"{result_id}:{type(exc).__name__}")
                continue
            if record is not None:
                records.append(record)
        if not records:
            return _rejection(
                "experiment_results_unreadable",
                read_errors=read_errors,
            )

        records.sort(key=lambda item: item.completed_at, reverse=True)
        rejected: list[dict[str, str]] = []
        for record in records:
            authorization = self._verify_record(record)
            if authorization.get("authorized"):
                if read_errors:
                    authorization["ignored_read_errors"] = read_errors
                return authorization
            rejected.append(
                {
                    "experiment_result_id": record.experiment_result_id,
                    "reason": str(authorization.get("reason") or "not_authorized"),
                }
            )
        return {
            **_rejection("no_promotable_experiment_result"),
            "rejected_results": rejected,
            "read_errors": read_errors,
        }

    def verify(self, experiment_result_id: str) -> dict[str, Any]:
        result_id = str(experiment_result_id or "").strip()
        if not result_id:
            return _rejection("experiment_result_id_missing")
        try:
            result = self.evaluation_repository.get_experiment_result(result_id)
        except ValueError:
            return _rejection(
                "experiment_result_id_invalid",
                experiment_result_id=result_id,
            )
        except Exception as exc:
            return _rejection(
                "experiment_result_unreadable",
                experiment_result_id=result_id,
                error_type=type(exc).__name__,
            )
        if result is None:
            return _rejection(
                "experiment_result_not_found",
                experiment_result_id=result_id,
            )
        return self._verify_record(result)

    def _verify_record(self, result: Any) -> dict[str, Any]:
        result_id = str(result.experiment_result_id)
        if str(result.verdict) != "promote":
            return _rejection(
                "experiment_verdict_not_promote",
                experiment_result_id=result_id,
                verdict=str(result.verdict),
            )
        if not result.hard_gate_results or not all(
            gate.passed for gate in result.hard_gate_results
        ):
            return _rejection(
                "experiment_hard_gate_failed",
                experiment_result_id=result_id,
            )

        spec = self._read_reference(
            self.evaluation_repository.get_experiment_spec,
            result.experiment_spec_id,
            "experiment_spec",
            result_id,
        )
        if isinstance(spec, dict):
            return spec

        candidate_commit = str(spec.candidate_commit).strip().lower()
        if _FULL_GIT_COMMIT.fullmatch(candidate_commit) is None:
            return _rejection(
                "evaluated_candidate_commit_not_auditable",
                experiment_result_id=result_id,
                experiment_spec_id=spec.experiment_spec_id,
            )

        benchmark = self._read_reference(
            self.evaluation_repository.get_benchmark_pack,
            spec.benchmark_pack_id,
            "benchmark_pack",
            result_id,
        )
        if isinstance(benchmark, dict):
            return benchmark
        policy = self._read_reference(
            self.evaluation_repository.get_scoring_policy,
            spec.scoring_policy_id,
            "scoring_policy",
            result_id,
        )
        if isinstance(policy, dict):
            return policy

        gate_results = {gate.gate: bool(gate.passed) for gate in result.hard_gate_results}
        missing_gates = sorted(set(policy.required_hard_gates) - set(gate_results))
        if missing_gates:
            return _rejection(
                "required_hard_gates_missing",
                experiment_result_id=result_id,
                missing_hard_gates=missing_gates,
            )
        failed_required_gates = sorted(
            gate for gate in policy.required_hard_gates if not gate_results.get(gate)
        )
        if failed_required_gates:
            return _rejection(
                "required_hard_gate_failed",
                experiment_result_id=result_id,
                failed_hard_gates=failed_required_gates,
            )

        baseline_snapshot = self._read_reference(
            self.self_cognition_repository.get,
            spec.baseline_snapshot_id,
            "baseline_snapshot",
            result_id,
        )
        if isinstance(baseline_snapshot, dict):
            return baseline_snapshot
        candidate_snapshot = self._read_reference(
            self.self_cognition_repository.get,
            spec.candidate_snapshot_id,
            "candidate_snapshot",
            result_id,
        )
        if isinstance(candidate_snapshot, dict):
            return candidate_snapshot

        baseline_commit = str(baseline_snapshot.git_commit).strip().lower()
        snapshot_candidate_commit = str(candidate_snapshot.git_commit).strip().lower()
        if _FULL_GIT_COMMIT.fullmatch(baseline_commit) is None:
            return _rejection(
                "evaluated_baseline_commit_not_auditable",
                experiment_result_id=result_id,
            )
        if snapshot_candidate_commit != candidate_commit:
            return _rejection(
                "candidate_snapshot_commit_mismatch",
                experiment_result_id=result_id,
                evaluated_candidate_commit=candidate_commit,
                candidate_snapshot_commit=snapshot_candidate_commit,
            )
        if baseline_commit == candidate_commit:
            return _rejection(
                "evaluated_candidate_matches_baseline",
                experiment_result_id=result_id,
            )

        for knowledge_id in spec.knowledge_ids:
            knowledge = self._read_reference(
                self.knowledge_repository.get,
                knowledge_id,
                "knowledge_artifact",
                result_id,
            )
            if isinstance(knowledge, dict):
                knowledge["knowledge_id"] = knowledge_id
                return knowledge

        return {
            "schema_version": EVALUATION_GOVERNANCE_SCHEMA_VERSION,
            "authorized": True,
            "reason": "promote_result_verified",
            "experiment_result_id": result_id,
            "experiment_spec_id": spec.experiment_spec_id,
            "evaluated_baseline_commit": baseline_commit,
            "evaluated_candidate_commit": candidate_commit,
            "baseline_snapshot_id": spec.baseline_snapshot_id,
            "candidate_snapshot_id": spec.candidate_snapshot_id,
            "benchmark_pack_id": benchmark.benchmark_pack_id,
            "scoring_policy_id": policy.scoring_policy_id,
            "knowledge_ids": list(spec.knowledge_ids),
            "required_hard_gates": list(policy.required_hard_gates),
            "completed_at": result.completed_at.isoformat(),
            "confidence": float(result.confidence),
            "verdict": str(result.verdict),
        }

    @staticmethod
    def _read_reference(
        loader: Any,
        record_id: str,
        label: str,
        experiment_result_id: str,
    ) -> Any:
        try:
            record = loader(record_id)
        except Exception as exc:
            return _rejection(
                f"{label}_unreadable",
                experiment_result_id=experiment_result_id,
                error_type=type(exc).__name__,
            )
        if record is None:
            return _rejection(
                f"{label}_not_found",
                experiment_result_id=experiment_result_id,
            )
        return record


def validate_body_improvement_authorization_binding(
    *,
    evidence: Mapping[str, Any],
    constraints: Mapping[str, Any],
    authorization: Mapping[str, Any],
    actual_commit: str | None = None,
    actual_baseline_commit: str | None = None,
) -> dict[str, Any]:
    """Ensure a task and report are bound to the exact evaluated commit."""

    if not authorization.get("authorized"):
        return {
            "valid": False,
            "reason": str(authorization.get("reason") or "evaluation_not_authorized"),
        }
    for flag in (
        "must_match_evaluated_commit",
        "requires_governor_review",
        "requires_user_consent",
    ):
        if constraints.get(flag) is not True:
            return {"valid": False, "reason": f"{flag}_missing"}

    fields = (
        "experiment_result_id",
        "experiment_spec_id",
        "evaluated_baseline_commit",
        "evaluated_candidate_commit",
        "baseline_snapshot_id",
        "candidate_snapshot_id",
        "benchmark_pack_id",
        "scoring_policy_id",
    )
    for field in fields:
        expected = str(authorization.get(field) or "").strip().lower()
        evidence_value = str(evidence.get(field) or "").strip().lower()
        constraint_value = str(constraints.get(field) or "").strip().lower()
        if not expected or evidence_value != expected or constraint_value != expected:
            return {
                "valid": False,
                "reason": "evaluation_authorization_binding_mismatch",
                "field": field,
            }

    expected_knowledge = tuple(str(item) for item in authorization.get("knowledge_ids") or [])
    if tuple(str(item) for item in evidence.get("knowledge_ids") or []) != expected_knowledge:
        return {"valid": False, "reason": "evaluation_knowledge_binding_mismatch"}
    if tuple(str(item) for item in constraints.get("knowledge_ids") or []) != expected_knowledge:
        return {"valid": False, "reason": "evaluation_knowledge_binding_mismatch"}

    evaluated_commit = str(authorization["evaluated_candidate_commit"]).lower()
    if actual_commit is not None and str(actual_commit).strip().lower() != evaluated_commit:
        return {"valid": False, "reason": "evaluated_candidate_commit_mismatch"}
    evaluated_baseline = str(authorization["evaluated_baseline_commit"]).lower()
    if (
        actual_baseline_commit is not None
        and str(actual_baseline_commit).strip().lower() != evaluated_baseline
    ):
        return {"valid": False, "reason": "evaluated_baseline_commit_mismatch"}
    return {"valid": True, "reason": "evaluation_authorization_bound"}


def _rejection(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema_version": EVALUATION_GOVERNANCE_SCHEMA_VERSION,
        "authorized": False,
        "reason": reason,
        **details,
    }


__all__ = [
    "EVALUATION_GOVERNANCE_SCHEMA_VERSION",
    "EvolutionEvaluationGovernanceVerifier",
    "validate_body_improvement_authorization_binding",
]
