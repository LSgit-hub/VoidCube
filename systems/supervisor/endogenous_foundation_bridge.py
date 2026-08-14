"""Read-only projection from evolution-foundation records into endogenous drive."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from systems.evolution_evaluation import JsonEvaluationRepository
from systems.research_knowledge import JsonKnowledgeRepository, is_artifact_fresh
from systems.self_cognition import JsonSelfCognitionRepository


FOUNDATION_PROJECTION_SCHEMA_VERSION = 1
FOUNDATION_SHADOW_MODE = "shadow_read_only"


class EndogenousFoundationReadOnlyProjection:
    """Load immutable foundation records without creating or mutating repository state."""

    def __init__(
        self,
        *,
        self_cognition_repository: Any,
        knowledge_repository: Any,
        evaluation_repository: Any,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._self_cognition_repository = self_cognition_repository
        self._knowledge_repository = knowledge_repository
        self._evaluation_repository = evaluation_repository
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_root(cls, root: str | Path, *, now: Callable[[], datetime] | None = None) -> "EndogenousFoundationReadOnlyProjection":
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
            now=now,
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
        }
        projection["shadow_tasks"] = self._shadow_tasks(projection)
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
            result_ids = self._evaluation_repository.list_ids("experiment_results")
            result_records = [
                self._evaluation_repository.get_experiment_result(record_id)
                for record_id in result_ids
            ]
            result_records = [record for record in result_records if record is not None]
            spec_ids = self._evaluation_repository.list_ids("experiment_specs")
            if not result_records:
                return {
                    "status": "unavailable",
                    "result_count": 0,
                    "experiment_spec_count": len(spec_ids),
                    "experiment_result_id": None,
                }, "evaluation:no_experiment_result"
            latest = max(result_records, key=lambda record: record.completed_at)
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
                "completed_at": latest.completed_at.isoformat(),
                "verdict": latest.verdict,
                "confidence": latest.confidence,
                "failed_hard_gates": failed_gates,
                "regression_count": len(latest.regressions),
                "content_hash": latest.content_hash,
            }, None
        except Exception as exc:
            return {
                "status": "error",
                "result_count": 0,
                "experiment_spec_count": 0,
                "experiment_result_id": None,
            }, f"evaluation:read_error:{type(exc).__name__}"

    @staticmethod
    def _shadow_tasks(projection: dict[str, Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        cognition = dict(projection["self_cognition"])
        knowledge = dict(projection["research_knowledge"])
        evaluation = dict(projection["evaluation"])
        if cognition.get("status") != "available":
            tasks.append(
                _shadow_task(
                    task_kind="fill_self_cognition",
                    title="补充代码自我认知快照",
                    rationale="当前没有完整可用的代码自我认知事实，先补齐只读快照。",
                    evidence_refs=[str(cognition.get("snapshot_id") or "self_cognition")],
                )
            )
        if knowledge.get("status") != "available" or float(knowledge.get("quality_score") or 0.0) < 0.5:
            tasks.append(
                _shadow_task(
                    task_kind="fill_research_knowledge",
                    title="补充外部知识 artifact",
                    rationale="当前外部知识缺失、过期或质量不足，建议先完成离线知识归一化。",
                    evidence_refs=[str(knowledge.get("knowledge_id") or "research_knowledge")],
                )
            )
        if (
            evaluation.get("status") != "available"
            or evaluation.get("verdict") in {"reject", "observe"}
            or int(evaluation.get("experiment_spec_count") or 0)
            > int(evaluation.get("result_count") or 0)
        ):
            tasks.append(
                _shadow_task(
                    task_kind="run_evolution_evaluation",
                    title="执行一次受控 BenchmarkPack 对比实验",
                    rationale="评测事实缺失或上一轮未达到稳定结论，建议先在影子模式补充对比证据。",
                    evidence_refs=[
                        str(evaluation.get("experiment_result_id") or "evaluation")
                    ],
                )
            )
        return tasks

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
) -> dict[str, Any]:
    return {
        "stable_key": f"evolution_foundation:{task_kind}",
        "task_kind": task_kind,
        "title": title,
        "rationale": rationale,
        "status": "shadow",
        "execution_allowed": False,
        "evidence_refs": sorted(set(evidence_refs)),
    }


__all__ = [
    "FOUNDATION_PROJECTION_SCHEMA_VERSION",
    "FOUNDATION_SHADOW_MODE",
    "EndogenousFoundationReadOnlyProjection",
]
