"""Pure reflection projection for endogenous drive deliberation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from systems.supervisor.endogenous_body_eligibility import has_recent_body_improvement
from systems.supervisor.endogenous_history import (
    normalize_historical_outcomes,
    summarize_historical_pressure,
)


_REVIEW_BACKLOG_STATUSES = {"deferred", "paused", "awaiting_review", "retry"}
_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"


class PerceptionView(Protocol):
    learning_quality: float
    stale_backlog_count: int
    api_b_judgement_count: int
    active_sessions: int
    user_mode: str


class WorldModelView(Protocol):
    governance_load_state: str
    self_confidence: float
    learning_momentum: float
    body_upgrade_readiness: float


def build_reflection_projection(
    *,
    perception: PerceptionView,
    world_model: WorldModelView,
    drive_context: Dict[str, Any],
    shell_slot_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    completed_learning_tasks = list(drive_context.get("completed_learning_tasks") or [])
    api_b_judgement_tasks = list(drive_context.get("autonomous_chain_live_tasks") or [])
    drive_history = dict(drive_context.get("drive_history") or {})
    historical_outcomes = normalize_historical_outcomes(
        [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
    )
    recent_learning_count = len(completed_learning_tasks[:3])
    quality_values: List[float] = []
    for task in completed_learning_tasks[:3]:
        try:
            quality_values.append(_clamp01(float(task.get("quality_score") or 0.0)))
        except (TypeError, ValueError):
            continue
    recent_learning_quality = (
        sum(quality_values) / len(quality_values)
        if quality_values
        else _clamp01(perception.learning_quality / 100.0)
    )
    learning_yield_state = "cold"
    if recent_learning_quality >= 0.75:
        learning_yield_state = "strong"
    elif recent_learning_quality >= 0.45 or recent_learning_count > 0:
        learning_yield_state = "mixed"

    blocked_status_count = 0
    repeated_drive_count = 0
    recent_endogenous_keys: set[str] = set()
    for task in api_b_judgement_tasks:
        status = str(task.get("status") or "").strip().lower()
        if status in _REVIEW_BACKLOG_STATUSES:
            blocked_status_count += 1
        metadata = dict(task.get("metadata") or {})
        evidence = dict(task.get("evidence") or {})
        endogenous_key = str(
            metadata.get("endogenous_drive_key")
            or evidence.get("endogenous_drive_key")
            or ""
        ).strip()
        if endogenous_key:
            recent_endogenous_keys.add(endogenous_key)
            repeated_drive_count += 1

    blockage_pressure = _clamp01(
        blocked_status_count * 0.18
        + perception.stale_backlog_count * 0.16
        + max(0, perception.api_b_judgement_count - 2) * 0.05
    )
    if world_model.governance_load_state == "strained":
        blockage_pressure = _clamp01(blockage_pressure + 0.2)
    elif world_model.governance_load_state == "busy":
        blockage_pressure = _clamp01(blockage_pressure + 0.08)
    blockage_state = "clear"
    if blockage_pressure >= 0.6:
        blockage_state = "blocked"
    elif blockage_pressure >= 0.32:
        blockage_state = "dragging"

    body_growth_blocked = False
    if shell_slot_meta:
        policy = dict(drive_context.get("policy") or {})
        body_growth_blocked = has_recent_body_improvement(
            list(drive_context.get("api_b_judgement_tasks") or []),
            shell_slot_id=str(shell_slot_meta.get("slot_id") or ""),
            cooldown_hours=int(policy.get("body_improvement_cooldown_hours", 12) or 12),
        )
    repeated_drive_pressure = _clamp01(
        repeated_drive_count * 0.08
        + max(0, len(recent_endogenous_keys) - 1) * 0.04
        + (0.14 if blockage_state != "clear" else 0.0)
    )

    recent_historical_outcomes = historical_outcomes[:12]
    recent_self_learning_outcomes = [
        item
        for item in historical_outcomes
        if str(item.get("task_family") or item.get("governance_task_type") or "")
        .strip()
        .lower()
        == "self_learning"
    ][:12]
    historical_pressure = summarize_historical_pressure(
        recent_historical_outcomes=recent_historical_outcomes,
        recent_self_learning_outcomes=recent_self_learning_outcomes,
    )
    historical_scope = str(historical_pressure["scope"] or "global")
    historical_total = int(historical_pressure["total"] or 0)
    historical_success_ratio = float(historical_pressure["success_ratio"] or 0.5)
    historical_drag_ratio = float(historical_pressure["drag_ratio"] or 0.0)
    recent_relapse_drag_count = int(historical_pressure["recent_relapse_drag_count"] or 0)
    recent_relapse_drag_ratio = float(historical_pressure["recent_relapse_drag_ratio"] or 0.0)
    autonomy_readiness = _clamp01(
        world_model.self_confidence * 0.34
        + world_model.learning_momentum * 0.24
        + world_model.body_upgrade_readiness * 0.12
        + recent_learning_quality * 0.18
        + historical_success_ratio * 0.1
        - blockage_pressure * 0.24
        - repeated_drive_pressure * 0.12
        - historical_drag_ratio * 0.16
        - recent_relapse_drag_ratio * 0.06
        - (0.08 if body_growth_blocked else 0.0)
    )
    historical_underdelivery_active = bool(historical_pressure.get("underdelivery_active"))

    dominant_constraint = "none"
    if blockage_pressure >= 0.55:
        dominant_constraint = _API_B_JUDGEMENT_BLOCKAGE
    elif body_growth_blocked:
        dominant_constraint = "body_growth_cooldown"
    elif historical_underdelivery_active:
        dominant_constraint = "historical_underdelivery"
    elif recent_learning_quality < 0.4 and recent_learning_count > 0:
        dominant_constraint = "weak_learning_yield"
    elif perception.active_sessions > 0 and perception.user_mode == "serving_user":
        dominant_constraint = "user_service_priority"

    rationale_parts = [
        f"近期学习收益状态为 {learning_yield_state}",
        f"API-B 判断在途阻塞状态为 {blockage_state}",
    ]
    if historical_total > 0:
        rationale_parts.append(f"历史 {historical_scope} 成功比率为 {historical_success_ratio:.2f}")
    if body_growth_blocked:
        rationale_parts.append("近期 shell 改进活动暂时阻断了替身成长")
    if dominant_constraint != "none":
        rationale_parts.append(f"当前主约束是 {dominant_constraint}")
    return {
        "recent_learning_count": recent_learning_count,
        "recent_learning_quality": recent_learning_quality,
        "learning_yield_state": learning_yield_state,
        "api_b_judgement_blockage_pressure": blockage_pressure,
        "api_b_judgement_blockage_state": blockage_state,
        "body_growth_blocked": body_growth_blocked,
        "repeated_drive_pressure": repeated_drive_pressure,
        "autonomy_readiness": autonomy_readiness,
        "dominant_constraint": dominant_constraint,
        "rationale": "; ".join(rationale_parts) + ".",
        "source_evidence": [
            f"recent_learning_count={recent_learning_count}",
            f"recent_learning_quality={recent_learning_quality:.2f}",
            f"blocked_status_count={blocked_status_count}",
            f"stale_backlog_count={perception.stale_backlog_count}",
            f"body_growth_blocked={body_growth_blocked}",
            f"repeated_drive_count={repeated_drive_count}",
            f"historical_scope={historical_scope}",
            f"historical_outcomes={historical_total}",
            f"historical_success_ratio={historical_success_ratio:.2f}",
            f"historical_drag_ratio={historical_drag_ratio:.2f}",
            f"recent_relapse_drag_count={recent_relapse_drag_count}",
            f"recent_relapse_drag_ratio={recent_relapse_drag_ratio:.2f}",
        ],
    }


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
