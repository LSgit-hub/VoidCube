"""Pure body-improvement projection assembly for endogenous drive."""

from __future__ import annotations

from typing import Any, Dict

from systems.supervisor.endogenous_body_eligibility import (
    resolve_body_improvement_eligibility,
)
from systems.supervisor.endogenous_body_mapping import build_body_structure_mapping


def build_body_improvement_projection(
    *,
    drive_context: Dict[str, Any],
    shell_slot_meta: Dict[str, Any],
) -> Dict[str, Any]:
    policy = dict(drive_context.get("policy") or {})
    shell_slot_id = str(shell_slot_meta.get("slot_id") or "").strip()
    shell_worktree = str(shell_slot_meta.get("worktree_path") or "").strip()
    eligibility = resolve_body_improvement_eligibility(
        completed_learning_tasks=list(
            drive_context.get("completed_learning_tasks") or []
        ),
        shell_slot_id=shell_slot_id,
        shell_worktree=shell_worktree,
        policy=policy,
        api_b_judgement_tasks=list(drive_context.get("api_b_judgement_tasks") or []),
    )
    if not eligibility.get("available"):
        return eligibility
    mapping = build_body_structure_mapping(
        completed_learning_tasks=list(eligibility["completed_learning_tasks"]),
        shell_slot_id=str(eligibility["shell_slot_id"]),
        shell_worktree=str(eligibility["shell_worktree"]),
        policy=policy,
        learning_quality_score=float(eligibility["learning_quality_score"]),
    )
    if not mapping.get("available"):
        return mapping

    foundation = dict(drive_context.get("evolution_foundation") or {})
    evaluation = dict(foundation.get("evaluation") or {})
    authorization = dict(evaluation.get("body_improvement_authorization") or {})
    if not authorization.get("authorized"):
        return {
            **mapping,
            "available": False,
            "reason": "evaluation_authorization_unavailable",
            "candidate_generation_ready": True,
            "evaluation_authorization": authorization,
        }

    result_id = str(authorization.get("experiment_result_id") or "").strip()
    mapping["mapping_key"] = f"{mapping['mapping_key']}:{result_id[-16:]}"
    mapping["candidate_generation_ready"] = False
    mapping["evaluation_authorization"] = authorization
    return mapping
