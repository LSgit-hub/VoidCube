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
        completed_learning_tasks=list(drive_context.get("completed_learning_tasks") or []),
        shell_slot_id=shell_slot_id,
        shell_worktree=shell_worktree,
        policy=policy,
        api_b_judgement_tasks=list(drive_context.get("api_b_judgement_tasks") or []),
    )
    if not eligibility.get("available"):
        return eligibility
    return build_body_structure_mapping(
        completed_learning_tasks=list(eligibility["completed_learning_tasks"]),
        shell_slot_id=str(eligibility["shell_slot_id"]),
        shell_worktree=str(eligibility["shell_worktree"]),
        policy=policy,
        learning_quality_score=float(eligibility["learning_quality_score"]),
    )
