"""Pure factories for stable endogenous candidate families."""

from __future__ import annotations

from typing import Any, Dict

from systems.supervisor.endogenous_candidate_pipeline import (
    AdaptivePolicyLike,
    EndogenousTaskCandidate,
    adaptive_factor_for_candidate,
    build_scored_candidate,
    clamp01,
)


def build_memory_maintenance_candidate(
    *,
    urgency: float,
    backlog_pressure_penalty: float,
    adaptive_policy: AdaptivePolicyLike,
    drive_judgement: Dict[str, Any],
    observation_checks: Dict[str, Any],
    idle_seconds: Dict[str, Any],
) -> EndogenousTaskCandidate:
    return build_scored_candidate(
        stable_key="continuity:memory_maintenance_sweep",
        title="维持长期记忆连续性",
        summary=(
            "在当前观测周期内检查记忆维护需求，"
            "让长期身份、摘要与治理轨迹保持可用。"
        ),
        priority="high",
        governance_task_type="memory_maintenance",
        task_family="memory_maintenance",
        execution_kind="memory_maintenance",
        value_tags=["continuity"],
        candidate_kind="memory_maintenance",
        score_inputs={
            "core_value_strength": 1.0,
            "urgency": urgency,
            "novelty": 0.58,
            "specificity": 0.78,
            "execution_readiness": 1.0,
            "backlog_pressure_penalty": backlog_pressure_penalty,
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="memory_maintenance",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={"drive_judgement": dict(drive_judgement)},
        evidence={
            "observation_checks": dict(observation_checks),
            "idle_seconds": dict(idle_seconds),
        },
    )


def build_truthfulness_review_candidate(
    *,
    recent_errors: int,
    uncertainty_count: int,
    correction_signals: int,
    runtime_signal_present: bool,
    backlog_pressure_penalty: float,
    adaptive_policy: AdaptivePolicyLike,
    drive_judgement: Dict[str, Any],
) -> EndogenousTaskCandidate:
    return build_scored_candidate(
        stable_key="truthfulness:review_correction_signals",
        title="复核近期不确定性与纠偏信号",
        summary=(
            "把近期错误或高不确定性回答收成有边界的自学习跟进，"
            "而不是继续让它们停留在不可见状态。"
        ),
        priority="high",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["truthfulness"],
        candidate_kind="truthfulness_review",
        score_inputs={
            "core_value_strength": 0.98,
            "urgency": clamp01(
                0.35 + (min(correction_signals, 6) / 6.0) * 0.65
            ),
            "novelty": 0.72 if runtime_signal_present else 0.68,
            "specificity": clamp01(
                0.55 + min(correction_signals, 5) * 0.08
            ),
            "execution_readiness": 0.92,
            "backlog_pressure_penalty": backlog_pressure_penalty,
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="truthfulness_review",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={"drive_judgement": dict(drive_judgement)},
        evidence={
            "recent_errors": recent_errors,
            "uncertainty_high_count": uncertainty_count,
            "correction_signals": correction_signals,
            "signal_source": (
                "runtime_observation_snapshot"
                if runtime_signal_present
                else "raw_counts"
            ),
        },
    )


def build_governance_hygiene_review_candidate(
    *,
    urgency: float,
    api_b_judgement_count: int,
    adaptive_policy: AdaptivePolicyLike,
    drive_judgement: Dict[str, Any],
) -> EndogenousTaskCandidate:
    return build_scored_candidate(
        stable_key="continuity:governance_hygiene_review",
        title="观察 API-B 判断积压",
        summary=(
            "检查已规划、已延后或已暂停的 API-B 判断在途工作是否仍具备"
            "足够证据、责任归属和回滚约束。"
        ),
        priority="normal",
        governance_task_type="self_evolution",
        task_family="general_self_evolution",
        execution_kind="general_self_evolution",
        value_tags=["continuity", "truthfulness"],
        candidate_kind="governance_hygiene_review",
        score_inputs={
            "core_value_strength": 0.62,
            "urgency": urgency,
            "novelty": 0.38,
            "specificity": clamp01(
                0.46 + min(int(api_b_judgement_count or 0), 4) * 0.05
            ),
            "execution_readiness": 0.85,
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="governance_hygiene_review",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={"drive_judgement": dict(drive_judgement)},
        evidence={"trigger": "supervisor_backlog_governance"},
        constraints={"must_not_execute_without_review": True},
    )


def build_body_improvement_candidate(
    *,
    body_projection: Dict[str, Any],
    backlog_pressure_penalty: float,
    adaptive_policy: AdaptivePolicyLike,
    drive_judgement: Dict[str, Any],
) -> EndogenousTaskCandidate:
    target_paths = list(body_projection.get("target_paths") or [])
    domains = list(body_projection.get("structure_domains") or [])
    learning_quality = float(body_projection.get("learning_quality_score") or 0.0)
    return build_scored_candidate(
        stable_key=(
            "creativity:body_improvement:"
            f"{body_projection['mapping_key']}"
        ),
        title="定向改进替身：" + (domains[0] if domains else target_paths[0]),
        summary=(
            "依据已完成学习结论，定向检查并改进 shell 替身中的 "
            f"{', '.join(target_paths)}。只允许修改映射出的安全节点，"
            "提交 Git 变更后由 Supervisor 独立复核。"
        ),
        priority="high" if learning_quality >= 80.0 else "normal",
        governance_task_type="self_evolution",
        task_family="body_upgrade",
        execution_kind="body_improvement",
        value_tags=["creativity", "continuity"],
        candidate_kind="body_improvement",
        score_inputs={
            "core_value_strength": 0.78,
            "urgency": clamp01(learning_quality / 100.0),
            "novelty": clamp01(0.5 + len(domains) * 0.06),
            "specificity": clamp01(0.62 + len(target_paths) * 0.06),
            "execution_readiness": 0.88,
            "backlog_pressure_penalty": backlog_pressure_penalty,
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="body_improvement",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={
            "improvement_direction_source": body_projection.get("mapping_source"),
            "target_paths": target_paths,
            "structure_domains": domains,
            "learning_task_ids": [
                ref.get("mem_id")
                for ref in list(body_projection.get("learning_refs") or [])
            ],
            "learning_quality_score": learning_quality,
            "drive_judgement": dict(drive_judgement),
        },
        evidence={
            "trigger": "completed_learning_structure_mapping",
            "learning_quality_score": learning_quality,
            "learning_refs": list(body_projection.get("learning_refs") or []),
            "evidence_summary": list(body_projection.get("evidence_summary") or []),
            "structure_mapping": {
                "source": body_projection.get("mapping_source"),
                "domains": domains,
                "target_paths": target_paths,
            },
        },
        constraints=body_improvement_constraints(body_projection),
    )


def body_improvement_constraints(projection: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "execution_policy": "improve_shell_body",
        "target_slot": "shell",
        "target_slot_id": projection["target_slot_id"],
        "worktree_path": projection["worktree_path"],
        "target_paths": list(projection.get("target_paths") or []),
        "editable_dirs": list(projection.get("editable_dirs") or []),
        "forbidden_patterns": list(projection.get("forbidden_patterns") or []),
        "max_files_changed": int(projection.get("max_files_changed") or 5),
        "must_commit": True,
        "evolution_boundary_check": True,
        "structure_mapping_source": projection.get("mapping_source"),
    }
