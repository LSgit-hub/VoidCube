"""Authoritative governance admission for evaluated body commits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from systems.evolution_authoring import JsonEvolutionAuthoringRepository
from systems.evolution_evaluation import (
    EXECUTION_ENVIRONMENT_GATE,
    JsonEvaluationRepository,
    select_benchmark_platforms,
)
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
    authoring_repository: Any

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
            authoring_repository=JsonEvolutionAuthoringRepository(
                foundation_root / "authoring"
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
        authoring_result_id = str(spec.authoring_result_id or "").strip()
        if not authoring_result_id:
            return _rejection(
                "authoring_result_missing",
                experiment_result_id=result_id,
                experiment_spec_id=spec.experiment_spec_id,
            )
        authoring = self._read_reference(
            self.authoring_repository.get,
            authoring_result_id,
            "authoring_result",
            result_id,
        )
        if isinstance(authoring, dict):
            return authoring
        if authoring.status != "candidate_created":
            return _rejection(
                "authoring_result_not_successful",
                experiment_result_id=result_id,
                authoring_result_id=authoring_result_id,
            )
        if str(authoring.candidate_commit).lower() != candidate_commit:
            return _rejection(
                "authoring_candidate_commit_mismatch",
                experiment_result_id=result_id,
                authoring_result_id=authoring_result_id,
            )
        if not authoring.environment_dependency_fingerprint:
            return _rejection(
                "authoring_dependency_fingerprint_missing",
                experiment_result_id=result_id,
                authoring_result_id=authoring_result_id,
            )
        if not authoring.command_evidence:
            return _rejection(
                "authoring_command_evidence_missing",
                experiment_result_id=result_id,
                authoring_result_id=authoring_result_id,
            )
        if any(
            evidence.security_scanner_status is None
            or evidence.container_disk_quota_status is None
            for evidence in authoring.command_evidence
        ):
            return _rejection(
                "authoring_environment_capability_evidence_missing",
                experiment_result_id=result_id,
                authoring_result_id=authoring_result_id,
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

        selection = spec.platform_selection
        if selection is None:
            return _rejection(
                "platform_selection_missing",
                experiment_result_id=result_id,
                experiment_spec_id=spec.experiment_spec_id,
            )
        if tuple(selection.changed_files) != tuple(authoring.changed_files):
            return _rejection(
                "platform_selection_changed_files_mismatch",
                experiment_result_id=result_id,
                platform_selection_id=selection.selection_id,
            )
        if selection.dependency_fingerprint != authoring.environment_dependency_fingerprint:
            return _rejection(
                "platform_selection_dependency_mismatch",
                experiment_result_id=result_id,
                platform_selection_id=selection.selection_id,
            )
        expected_selection = select_benchmark_platforms(
            authoring.changed_files,
            str(authoring.environment_dependency_fingerprint),
            created_at=selection.created_at,
        )
        if selection != expected_selection:
            return _rejection(
                "platform_selection_not_deterministic",
                experiment_result_id=result_id,
                platform_selection_id=selection.selection_id,
            )
        missing_selection_platforms = sorted(
            set(selection.required_platforms)
            - set(policy.required_validation_platforms)
        )
        if missing_selection_platforms:
            return _rejection(
                "platform_selection_not_covered",
                experiment_result_id=result_id,
                platform_selection_id=selection.selection_id,
                missing_validation_platforms=missing_selection_platforms,
            )
        unexpected_policy_platforms = sorted(
            set(policy.required_validation_platforms)
            - set(selection.required_platforms)
        )
        if unexpected_policy_platforms:
            return _rejection(
                "validation_platform_not_selected",
                experiment_result_id=result_id,
                platform_selection_id=selection.selection_id,
                unexpected_validation_platforms=unexpected_policy_platforms,
            )

        case_evidence = result.benchmark_case_evidence
        if not case_evidence:
            return _rejection(
                "benchmark_command_evidence_missing",
                experiment_result_id=result_id,
            )
        if (
            result.execution_environments is None
            or result.execution_environment_identities is None
        ):
            return _rejection(
                "execution_environment_matrix_missing",
                experiment_result_id=result_id,
            )
        environments = tuple(result.execution_environments)
        identities = tuple(result.execution_environment_identities)
        environment_by_id = {
            item.execution_environment_id: item for item in environments
        }
        identity_ids = {
            item.execution_environment_identity_id for item in identities
        }
        evidence_platforms: dict[str, str] = {
            item.execution_environment_id: item.validated_platforms[0]
            for item in environments
        }
        expected_cases = {
            (platform, subject, case.case_id)
            for platform in policy.required_validation_platforms
            for subject in ("baseline", "candidate")
            for case in benchmark.cases
        }
        try:
            actual_cases = {
                (
                    evidence_platforms[item.execution_environment_id],
                    item.subject,
                    item.case_id,
                )
                for item in case_evidence
            }
        except KeyError:
            return _rejection(
                "benchmark_environment_evidence_unknown",
                experiment_result_id=result_id,
            )
        if actual_cases != expected_cases:
            return _rejection(
                "benchmark_case_evidence_incomplete",
                experiment_result_id=result_id,
            )
        if any(
            command.security_scanner_status is None
            or command.container_disk_quota_status is None
            for item in case_evidence
            for command in item.commands
        ):
            return _rejection(
                "benchmark_environment_capability_evidence_missing",
                experiment_result_id=result_id,
            )
        failed_commands = [
            f"{item.subject}:{item.case_id}:{command.command}"
            for item in case_evidence
            for command in item.commands
            if command.exit_code != 0 or command.timed_out
        ]
        if failed_commands:
            return _rejection(
                "benchmark_command_failed",
                experiment_result_id=result_id,
                failed_commands=failed_commands,
            )

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

        environment_gate = next(
            (
                gate
                for gate in result.hard_gate_results
                if gate.gate == EXECUTION_ENVIRONMENT_GATE
            ),
            None,
        )
        if environment_gate is None or not environment_gate.passed:
            return _rejection(
                "execution_environment_gate_failed",
                experiment_result_id=result_id,
            )
        environment = result.execution_environment
        if {item.execution_environment_id for item in case_evidence} != set(
            environment_by_id
        ):
            return _rejection(
                "benchmark_environment_matrix_mismatch",
                experiment_result_id=result_id,
            )
        checkout_by_id = {
            item.subject_checkout_evidence_id: item
            for item in result.subject_checkouts
        }
        for item in case_evidence:
            manifest = environment_by_id[item.execution_environment_id]
            checkout = checkout_by_id.get(item.subject_checkout_evidence_id)
            if (
                checkout is None
                or checkout.subject != item.subject
                or checkout.execution_environment_identity_id
                != item.execution_environment_identity_id
                or manifest.identity().execution_environment_identity_id
                != item.execution_environment_identity_id
                or manifest.repository_head.lower() != checkout.commit.lower()
            ):
                return _rejection(
                    "benchmark_environment_checkout_mismatch",
                    experiment_result_id=result_id,
                )
        candidate_environment_ids = {
            item.execution_environment_id
            for item in case_evidence
            if item.subject == "candidate"
        }
        candidate_matrix_ids = {
            item.execution_environment_id
            for item in environments
            if item.repository_head.lower() == candidate_commit
        }
        if candidate_environment_ids != candidate_matrix_ids:
            return _rejection(
                "candidate_environment_evidence_mismatch",
                experiment_result_id=result_id,
                execution_environment_id=environment.execution_environment_id,
            )
        missing_platforms = sorted(
            set(policy.required_validation_platforms)
            - {
                platform
                for item in environments
                for platform in item.validated_platforms
            }
        )
        if missing_platforms:
            return _rejection(
                "required_validation_platforms_missing",
                experiment_result_id=result_id,
                missing_validation_platforms=missing_platforms,
                execution_environment_id=environment.execution_environment_id,
            )
        candidate_checkouts = tuple(
            checkout
            for checkout in result.subject_checkouts
            if checkout.subject == "candidate"
        )
        if (
            len(candidate_checkouts) != len(identity_ids)
            or {
                checkout.execution_environment_identity_id
                for checkout in candidate_checkouts
            }
            != identity_ids
        ):
            return _rejection(
                "candidate_checkout_evidence_missing",
                experiment_result_id=result_id,
                execution_environment_id=environment.execution_environment_id,
            )
        if any(
            checkout.commit.strip().lower() != candidate_commit
            for checkout in candidate_checkouts
        ):
            return _rejection(
                "execution_environment_commit_mismatch",
                experiment_result_id=result_id,
                execution_environment_id=environment.execution_environment_id,
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
        if baseline_commit != str(authoring.baseline_commit).lower():
            return _rejection(
                "authoring_baseline_commit_mismatch",
                experiment_result_id=result_id,
                authoring_result_id=authoring_result_id,
            )
        baseline_checkouts = tuple(
            checkout
            for checkout in result.subject_checkouts
            if checkout.subject == "baseline"
        )
        if (
            len(baseline_checkouts) != len(identity_ids)
            or {
                checkout.execution_environment_identity_id
                for checkout in baseline_checkouts
            }
            != identity_ids
            or any(
                checkout.commit.lower() != baseline_commit
                for checkout in baseline_checkouts
            )
        ):
            return _rejection(
                "baseline_checkout_commit_mismatch",
                experiment_result_id=result_id,
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

        scanner_statuses = sorted(
            {item.security_scanner_status for item in authoring.command_evidence}
        )
        disk_quota_statuses = sorted(
            {item.container_disk_quota_status for item in authoring.command_evidence}
        )
        capability_warnings = [
            f"security_scanner_{status}"
            for status in scanner_statuses
            if status != "available"
        ] + [
            f"container_disk_quota_{status}"
            for status in disk_quota_statuses
            if status not in {"enforced", "not_applicable"}
        ]
        validation_scanner_statuses = sorted(
            {
                command.security_scanner_status
                for item in case_evidence
                for command in item.commands
            }
        )
        validation_disk_quota_statuses = sorted(
            {
                command.container_disk_quota_status
                for item in case_evidence
                for command in item.commands
            }
        )
        validation_capability_warnings = [
            f"security_scanner_{status}"
            for status in validation_scanner_statuses
            if status != "available"
        ] + [
            f"container_disk_quota_{status}"
            for status in validation_disk_quota_statuses
            if status not in {"enforced", "not_applicable"}
        ]
        return {
            "schema_version": EVALUATION_GOVERNANCE_SCHEMA_VERSION,
            "authorized": True,
            "reason": "promote_result_verified",
            "experiment_result_id": result_id,
            "experiment_spec_id": spec.experiment_spec_id,
            "authoring_result_id": authoring_result_id,
            "evaluated_baseline_commit": baseline_commit,
            "evaluated_candidate_commit": candidate_commit,
            "candidate_ref": authoring.candidate_ref,
            "changed_files": list(authoring.changed_files),
            "authoring_environment_manifest_id": authoring.environment_manifest_id,
            "authoring_environment_identity_id": authoring.environment_identity_id,
            "authoring_dependency_fingerprint": authoring.environment_dependency_fingerprint,
            "authoring_security_scanner_statuses": scanner_statuses,
            "authoring_container_disk_quota_statuses": disk_quota_statuses,
            "environment_capability_warnings": capability_warnings,
            "validation_security_scanner_statuses": validation_scanner_statuses,
            "validation_container_disk_quota_statuses": validation_disk_quota_statuses,
            "validation_environment_capability_warnings": validation_capability_warnings,
            "platform_selection_id": selection.selection_id,
            "selected_validation_platforms": list(selection.required_platforms),
            "platform_selection_reason_codes": list(selection.reason_codes),
            "baseline_snapshot_id": spec.baseline_snapshot_id,
            "candidate_snapshot_id": spec.candidate_snapshot_id,
            "benchmark_pack_id": benchmark.benchmark_pack_id,
            "scoring_policy_id": policy.scoring_policy_id,
            "knowledge_ids": list(spec.knowledge_ids),
            "required_hard_gates": list(policy.required_hard_gates),
            "completed_at": result.completed_at.isoformat(),
            "confidence": float(result.confidence),
            "verdict": str(result.verdict),
            "execution_environment_id": environment.execution_environment_id,
            "execution_environment_ids": sorted(environment_by_id),
            "execution_environment_identity_ids": sorted(identity_ids),
            "validation_scope": environment.validation_scope,
            "validated_platforms": list(environment.validated_platforms),
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
        "authoring_result_id",
        "evaluated_baseline_commit",
        "evaluated_candidate_commit",
        "candidate_ref",
        "baseline_snapshot_id",
        "candidate_snapshot_id",
        "benchmark_pack_id",
        "scoring_policy_id",
        "execution_environment_id",
        "authoring_environment_manifest_id",
        "authoring_environment_identity_id",
        "authoring_dependency_fingerprint",
        "platform_selection_id",
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

    for field in (
        "authoring_security_scanner_statuses",
        "authoring_container_disk_quota_statuses",
        "execution_environment_ids",
        "execution_environment_identity_ids",
        "validation_security_scanner_statuses",
        "validation_container_disk_quota_statuses",
    ):
        expected = tuple(str(item) for item in authorization.get(field) or [])
        if not expected:
            return {
                "valid": False,
                "reason": "evaluation_authorization_binding_mismatch",
                "field": field,
            }
        for source in (evidence, constraints):
            if tuple(str(item) for item in source.get(field) or []) != expected:
                return {
                    "valid": False,
                    "reason": "evaluation_authorization_binding_mismatch",
                    "field": field,
                }
    expected_warnings = tuple(
        str(item) for item in authorization.get("environment_capability_warnings") or []
    )
    for source in (evidence, constraints):
        if tuple(
            str(item) for item in source.get("environment_capability_warnings") or []
        ) != expected_warnings:
            return {
                "valid": False,
                "reason": "evaluation_authorization_binding_mismatch",
                "field": "environment_capability_warnings",
            }
    expected_validation_warnings = tuple(
        str(item)
        for item in authorization.get("validation_environment_capability_warnings")
        or []
    )
    for source in (evidence, constraints):
        if tuple(
            str(item)
            for item in source.get("validation_environment_capability_warnings") or []
        ) != expected_validation_warnings:
            return {
                "valid": False,
                "reason": "evaluation_authorization_binding_mismatch",
                "field": "validation_environment_capability_warnings",
            }

    expected_knowledge = tuple(str(item) for item in authorization.get("knowledge_ids") or [])
    if tuple(str(item) for item in evidence.get("knowledge_ids") or []) != expected_knowledge:
        return {"valid": False, "reason": "evaluation_knowledge_binding_mismatch"}
    if tuple(str(item) for item in constraints.get("knowledge_ids") or []) != expected_knowledge:
        return {"valid": False, "reason": "evaluation_knowledge_binding_mismatch"}

    expected_changed_files = tuple(
        str(item) for item in authorization.get("changed_files") or []
    )
    if tuple(str(item) for item in evidence.get("changed_files") or []) != expected_changed_files:
        return {
            "valid": False,
            "reason": "evaluation_changed_files_binding_mismatch",
        }
    if tuple(str(item) for item in constraints.get("changed_files") or []) != expected_changed_files:
        return {
            "valid": False,
            "reason": "evaluation_changed_files_binding_mismatch",
        }

    expected_selected_platforms = tuple(
        str(item) for item in authorization.get("selected_validation_platforms") or []
    )
    for source in (evidence, constraints):
        if tuple(
            str(item) for item in source.get("selected_validation_platforms") or []
        ) != expected_selected_platforms:
            return {
                "valid": False,
                "reason": "evaluation_platform_selection_binding_mismatch",
            }

    expected_scope = str(authorization.get("validation_scope") or "").strip()
    expected_platforms = tuple(
        str(item) for item in authorization.get("validated_platforms") or []
    )
    for source in (evidence, constraints):
        if str(source.get("validation_scope") or "").strip() != expected_scope:
            return {
                "valid": False,
                "reason": "evaluation_environment_binding_mismatch",
            }
        if tuple(str(item) for item in source.get("validated_platforms") or []) != expected_platforms:
            return {
                "valid": False,
                "reason": "evaluation_environment_binding_mismatch",
            }

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
