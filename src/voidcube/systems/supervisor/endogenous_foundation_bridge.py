"""Read-only projection from evolution-foundation records into endogenous drive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..evolution_authoring import JsonEvolutionAuthoringRepository
from ..evolution_evaluation import (
    EnvironmentCapabilityPolicy,
    JsonEvaluationRepository,
    resolve_environment_capability_policy,
)
from ..research_knowledge import JsonKnowledgeRepository, is_artifact_fresh
from ..self_cognition import JsonSelfCognitionRepository
from .evolution_evaluation_governance import (
    EvolutionEvaluationGovernanceVerifier,
)


FOUNDATION_PROJECTION_SCHEMA_VERSION = 1
FOUNDATION_SHADOW_MODE = "shadow_read_only"
FOUNDATION_SHADOW_POLICY_VERSION = "foundation-shadow-v1"


@dataclass(frozen=True, slots=True)
class FoundationShadowPolicy:
    """Versioned thresholds used only to classify shadow debt."""

    policy_version: str = FOUNDATION_SHADOW_POLICY_VERSION
    min_knowledge_quality_score: float = 0.5
    retry_evaluation_verdicts: tuple[str, ...] = ("observe", "reject")

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")
        if not 0.0 <= self.min_knowledge_quality_score <= 1.0:
            raise ValueError("min_knowledge_quality_score must be between 0 and 1")
        if not self.retry_evaluation_verdicts:
            raise ValueError("retry_evaluation_verdicts must not be empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "min_knowledge_quality_score": self.min_knowledge_quality_score,
            "retry_evaluation_verdicts": list(self.retry_evaluation_verdicts),
        }


class EndogenousFoundationReadOnlyProjection:
    """Load immutable foundation records without creating or mutating repository state."""

    def __init__(
        self,
        *,
        self_cognition_repository: Any,
        knowledge_repository: Any,
        evaluation_repository: Any,
        authoring_repository: Any,
        now: Callable[[], datetime] | None = None,
        shadow_policy: FoundationShadowPolicy | None = None,
        capability_policy: EnvironmentCapabilityPolicy | None = None,
    ) -> None:
        self._self_cognition_repository = self_cognition_repository
        self._knowledge_repository = knowledge_repository
        self._evaluation_repository = evaluation_repository
        self._evaluation_governance = EvolutionEvaluationGovernanceVerifier(
            evaluation_repository=evaluation_repository,
            knowledge_repository=knowledge_repository,
            self_cognition_repository=self_cognition_repository,
            authoring_repository=authoring_repository,
            capability_policy=(
                capability_policy
                or EnvironmentCapabilityPolicy.for_profile("development")
            ),
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._shadow_policy = shadow_policy or FoundationShadowPolicy()

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        shadow_policy: FoundationShadowPolicy | None = None,
        capability_policy: EnvironmentCapabilityPolicy | None = None,
        capability_policy_profile: str | None = None,
    ) -> "EndogenousFoundationReadOnlyProjection":
        foundation_root = Path(root).resolve()
        return cls(
            self_cognition_repository=JsonSelfCognitionRepository(
                foundation_root / "self-cognition"
            ),
            knowledge_repository=JsonKnowledgeRepository(
                foundation_root / "knowledge"
            ),
            evaluation_repository=JsonEvaluationRepository(
                foundation_root / "evaluation"
            ),
            authoring_repository=JsonEvolutionAuthoringRepository(
                foundation_root / "authoring"
            ),
            now=now,
            shadow_policy=shadow_policy,
            capability_policy=resolve_environment_capability_policy(
                policy=capability_policy,
                profile=capability_policy_profile,
            ),
        )

    def load(self) -> dict[str, Any]:
        reference_time = self._as_utc(self._now())
        self_cognition, self_error = self._load_self_cognition()
        knowledge, knowledge_error = self._load_knowledge(reference_time)
        evaluation, evaluation_error = self._load_evaluation()
        errors = [
            error
            for error in (self_error, knowledge_error, evaluation_error)
            if error
        ]
        projection = {
            "schema_version": FOUNDATION_PROJECTION_SCHEMA_VERSION,
            "mode": FOUNDATION_SHADOW_MODE,
            "read_only": True,
            "observed_at": reference_time.isoformat(),
            "self_cognition": self_cognition,
            "research_knowledge": knowledge,
            "evaluation": evaluation,
            "known_gaps": errors,
            "shadow_policy": self._shadow_policy.as_dict(),
        }
        projection["shadow_tasks"] = self._shadow_tasks(projection)
        projection["shadow_calibration"] = self._shadow_calibration(
            projection["shadow_tasks"]
        )
        return projection

    def _load_self_cognition(self) -> tuple[dict[str, Any], str | None]:
        try:
            record_ids = self._self_cognition_repository.list_ids()
            records = [
                self._self_cognition_repository.get(record_id)
                for record_id in record_ids
            ]
            records = [record for record in records if record is not None]
            if not records:
                return {
                    "status": "unavailable",
                    "record_count": 0,
                    "snapshot_id": None,
                }, "self_cognition:no_snapshot"
            latest = max(records, key=lambda record: record.collected_at)
            gaps = sorted({*latest.known_gaps, *latest.uncovered_areas})
            return {
                "status": "degraded" if gaps else "available",
                "record_count": len(records),
                "snapshot_id": latest.snapshot_id,
                "collected_at": latest.collected_at.isoformat(),
                "module_count": len(latest.modules),
                "capability_count": len(latest.capabilities),
                "health_metric_count": len(latest.health_metrics),
                "known_gaps": list(latest.known_gaps),
                "uncovered_areas": list(latest.uncovered_areas),
                "content_hash": latest.content_hash,
            }, None
        except Exception as exc:
            return {
                "status": "error",
                "record_count": 0,
                "snapshot_id": None,
            }, f"self_cognition:read_error:{type(exc).__name__}"

    def _load_knowledge(self, reference_time: datetime) -> tuple[dict[str, Any], str | None]:
        try:
            record_ids = self._knowledge_repository.list_ids()
            records = [
                self._knowledge_repository.get(record_id)
                for record_id in record_ids
            ]
            records = [record for record in records if record is not None]
            if not records:
                return {
                    "status": "unavailable",
                    "record_count": 0,
                    "knowledge_id": None,
                }, "research_knowledge:no_artifact"
            latest = max(records, key=lambda record: record.ingested_at)
            fresh = is_artifact_fresh(latest, as_of=reference_time)
            status = "available" if fresh else "stale"
            return {
                "status": status,
                "record_count": len(records),
                "knowledge_id": latest.knowledge_id,
                "topic": latest.topic,
                "ingested_at": latest.ingested_at.isoformat(),
                "valid_until": latest.valid_until.isoformat() if latest.valid_until else None,
                "fresh": fresh,
                "claim_count": len(latest.claims),
                "source_count": len(latest.sources),
                "confidence": latest.confidence,
                "quality_score": latest.quality_score,
                "content_hash": latest.content_hash,
            }, None
        except Exception as exc:
            return {
                "status": "error",
                "record_count": 0,
                "knowledge_id": None,
            }, f"research_knowledge:read_error:{type(exc).__name__}"

    def _load_evaluation(self) -> tuple[dict[str, Any], str | None]:
        try:
            authorization = self._evaluation_governance.latest_authorization()
            result_ids = self._evaluation_repository.list_ids("experiment_results")
            result_records = []
            read_errors: list[str] = []
            for record_id in result_ids:
                try:
                    record = self._evaluation_repository.get_experiment_result(record_id)
                except Exception as exc:
                    read_errors.append(f"{record_id}:{type(exc).__name__}")
                    continue
                if record is not None:
                    result_records.append(record)
            spec_ids = self._evaluation_repository.list_ids("experiment_specs")
            if not result_records:
                return {
                    "status": "unavailable",
                    "result_count": 0,
                    "experiment_spec_count": len(spec_ids),
                    "experiment_result_id": None,
                    "body_improvement_authorization": authorization,
                }, (
                    f"evaluation:no_experiment_result:{','.join(read_errors)}"
                    if read_errors
                    else "evaluation:no_experiment_result"
                )
            latest = max(result_records, key=lambda record: record.completed_at)
            latest_spec = self._evaluation_repository.get_experiment_spec(
                latest.experiment_spec_id
            )
            failed_gates = [
                gate.gate
                for gate in latest.hard_gate_results
                if not gate.passed
            ]
            return {
                "status": "available",
                "result_count": len(result_records),
                "experiment_spec_count": len(spec_ids),
                "experiment_result_id": latest.experiment_result_id,
                "experiment_spec_id": latest.experiment_spec_id,
                "completed_at": latest.completed_at.isoformat(),
                "verdict": latest.verdict,
                "confidence": latest.confidence,
                "failed_hard_gates": failed_gates,
                "regression_count": len(latest.regressions),
                "content_hash": latest.content_hash,
                "candidate_commit": (
                    latest_spec.candidate_commit if latest_spec is not None else None
                ),
                "baseline_snapshot_id": (
                    latest_spec.baseline_snapshot_id if latest_spec is not None else None
                ),
                "candidate_snapshot_id": (
                    latest_spec.candidate_snapshot_id if latest_spec is not None else None
                ),
                "benchmark_pack_id": (
                    latest_spec.benchmark_pack_id if latest_spec is not None else None
                ),
                "scoring_policy_id": (
                    latest_spec.scoring_policy_id if latest_spec is not None else None
                ),
                "knowledge_ids": (
                    list(latest_spec.knowledge_ids) if latest_spec is not None else []
                ),
                "body_improvement_authorization": authorization,
                "corrupted_result_records": read_errors,
            }, (
                f"evaluation:partial_read_error:{','.join(read_errors)}"
                if read_errors
                else None
            )
        except Exception as exc:
            return {
                "status": "error",
                "result_count": 0,
                "experiment_spec_count": 0,
                "experiment_result_id": None,
                "body_improvement_authorization": {
                    "schema_version": 1,
                    "authorized": False,
                    "reason": "evaluation_projection_read_error",
                    "error_type": type(exc).__name__,
                },
            }, f"evaluation:read_error:{type(exc).__name__}"

    def _shadow_tasks(self, projection: dict[str, Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        cognition = dict(projection["self_cognition"])
        knowledge = dict(projection["research_knowledge"])
        evaluation = dict(projection["evaluation"])
        if cognition.get("status") != "available":
            cognition_reason = {
                "unavailable": "self_cognition_unavailable",
                "degraded": "self_cognition_degraded",
                "error": "self_cognition_read_error",
            }.get(str(cognition.get("status")), "self_cognition_unavailable")
            tasks.append(
                _shadow_task(
                    task_kind="fill_self_cognition",
                    title="补充代码自我认知快照",
                    rationale="当前没有完整可用的代码自我认知事实，先补齐只读快照。",
                    evidence_refs=[str(cognition.get("snapshot_id") or "self_cognition")],
                    trigger_reasons=[cognition_reason],
                )
            )
        knowledge_reasons: list[str] = []
        knowledge_status = str(knowledge.get("status") or "unavailable")
        if knowledge_status != "available":
            knowledge_reasons.append(
                {
                    "unavailable": "knowledge_missing",
                    "stale": "knowledge_stale",
                    "error": "knowledge_read_error",
                }.get(knowledge_status, "knowledge_missing")
            )
        quality_score = knowledge.get("quality_score")
        if quality_score is not None and float(quality_score) < self._shadow_policy.min_knowledge_quality_score:
            knowledge_reasons.append("knowledge_low_quality")
        if knowledge_reasons:
            tasks.append(
                _shadow_task(
                    task_kind="fill_research_knowledge",
                    title="补充外部知识 artifact",
                    rationale="当前外部知识缺失、过期或质量不足，建议先完成离线知识归一化。",
                    evidence_refs=[str(knowledge.get("knowledge_id") or "research_knowledge")],
                    trigger_reasons=knowledge_reasons,
                )
            )
        evaluation_reasons: list[str] = []
        evaluation_status = str(evaluation.get("status") or "unavailable")
        if evaluation_status != "available":
            evaluation_reasons.append(
                {
                    "unavailable": "evaluation_missing",
                    "error": "evaluation_read_error",
                }.get(evaluation_status, "evaluation_missing")
            )
        verdict = str(evaluation.get("verdict") or "")
        if verdict in self._shadow_policy.retry_evaluation_verdicts:
            evaluation_reasons.append(f"evaluation_verdict_{verdict}")
        if int(evaluation.get("experiment_spec_count") or 0) > int(
            evaluation.get("result_count") or 0
        ):
            evaluation_reasons.append("evaluation_pending_specs")
        if (
            evaluation_reasons
        ):
            tasks.append(
                _shadow_task(
                    task_kind="run_evolution_evaluation",
                    title="执行一次受控 BenchmarkPack 对比实验",
                    rationale="评测事实缺失或上一轮未达到稳定结论，建议先在影子模式补充对比证据。",
                    evidence_refs=[
                        str(evaluation.get("experiment_result_id") or "evaluation")
                    ],
                    trigger_reasons=evaluation_reasons,
                )
            )
        return tasks

    def _shadow_calibration(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        task_kind_counts = {
            task_kind: sum(1 for task in tasks if task.get("task_kind") == task_kind)
            for task_kind in (
                "fill_self_cognition",
                "fill_research_knowledge",
                "run_evolution_evaluation",
            )
        }
        trigger_reason_counts: dict[str, int] = {}
        for task in tasks:
            for reason in list(task.get("trigger_reasons") or []):
                trigger_reason_counts[str(reason)] = trigger_reason_counts.get(str(reason), 0) + 1
        return {
            "policy_version": self._shadow_policy.policy_version,
            "status": "clear" if not tasks else "debt_observed",
            "shadow_task_count": len(tasks),
            "task_kind_counts": task_kind_counts,
            "trigger_reason_counts": dict(sorted(trigger_reason_counts.items())),
            "execution_allowed": False,
        }

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _shadow_task(
    *,
    task_kind: str,
    title: str,
    rationale: str,
    evidence_refs: list[str],
    trigger_reasons: list[str],
) -> dict[str, Any]:
    return {
        "stable_key": f"evolution_foundation:{task_kind}",
        "task_kind": task_kind,
        "title": title,
        "rationale": rationale,
        "status": "shadow",
        "execution_allowed": False,
        "evidence_refs": sorted(set(evidence_refs)),
        "trigger_reasons": sorted(set(trigger_reasons)),
    }


__all__ = [
    "FOUNDATION_PROJECTION_SCHEMA_VERSION",
    "FOUNDATION_SHADOW_MODE",
    "FOUNDATION_SHADOW_POLICY_VERSION",
    "EndogenousFoundationReadOnlyProjection",
    "FoundationShadowPolicy",
]
