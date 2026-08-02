"""Pure intent and signal projections for the endogenous drive pipeline."""

from typing import Any, Dict, List

from systems.supervisor.endogenous_policy import has_truthfulness_review_signal


_REVIEW_API_B_JUDGEMENT_NEED = "review_api_b_judgement"


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def synthesize_intent_projections(
    *,
    needs: List[Any],
    perception: Any,
    reflection: Any,
    adaptive_policy: Any,
) -> List[Dict[str, Any]]:
    """Map current needs into serializable intent rows without owning DTOs."""
    intents: List[Dict[str, Any]] = []
    for need in needs:
        priority = _clamp01(
            need.severity * 0.45
            + need.urgency * 0.35
            + need.confidence * 0.20
        )
        if need.need_type == "expand_learning_frontier":
            priority = _clamp01(
                priority
                + adaptive_policy.learning_expansion_bias * 0.08
                - adaptive_policy.candidate_throttle * 0.1
            )
        elif need.need_type == "prepare_body_growth":
            priority = _clamp01(
                priority
                + adaptive_policy.body_growth_bias * 0.08
                - adaptive_policy.candidate_throttle * 0.06
            )
        elif need.need_type == _REVIEW_API_B_JUDGEMENT_NEED:
            priority = _clamp01(
                priority + adaptive_policy.governance_hygiene_bias * 0.08
            )
        elif need.need_type == "observe_before_acting":
            priority = _clamp01(
                priority + adaptive_policy.observation_bias * 0.12
            )

        row = {
            "priority": priority,
            "rationale": need.rationale,
            "source_needs": [need.need_type],
        }
        if need.need_type == "stabilize_memory_continuity":
            row.update(
                intent_type="maintain_memory_continuity",
                target_horizon="immediate",
                output_channel="task_candidate",
                candidate_family="memory_maintenance",
                candidate_kind="memory_maintenance",
            )
        elif need.need_type == "repair_truthfulness":
            row.update(
                intent_type="review_truthfulness_signals",
                target_horizon="immediate",
                output_channel="task_candidate",
                candidate_family="self_learning",
                candidate_kind="truthfulness_review",
            )
        elif need.need_type == "expand_learning_frontier":
            row.update(
                intent_type="expand_learning_frontier",
                target_horizon="near_term",
                output_channel="task_candidate",
                candidate_family="self_learning",
                candidate_kind=(
                    "shell_baseline_learning"
                    if perception.shell_slot_present and not perception.has_learning_history
                    else "exploratory_learning"
                ),
            )
        elif need.need_type == "prepare_body_growth":
            row.update(
                intent_type="prepare_body_growth",
                target_horizon="near_term",
                output_channel="task_candidate",
                candidate_family="body_upgrade",
                candidate_kind="body_improvement",
            )
        elif need.need_type == _REVIEW_API_B_JUDGEMENT_NEED:
            row.update(
                intent_type="review_governance_hygiene",
                target_horizon="near_term",
                output_channel="task_candidate",
                candidate_family="general_self_evolution",
                candidate_kind="governance_hygiene_review",
            )
        elif need.need_type == "observe_before_acting":
            row.update(
                intent_type="observe_before_acting",
                target_horizon=(
                    "immediate"
                    if reflection.api_b_judgement_blockage_pressure >= 0.55
                    else "near_term"
                ),
                output_channel="drive_signal",
            )
        else:
            continue
        intents.append(row)
    intents.sort(key=lambda item: item["priority"], reverse=True)
    return intents


def emit_drive_signal_projections(
    *,
    perception: Any,
    world_model: Any,
    reflection: Any,
    adaptive_policy: Any,
    needs: List[Any],
    intents: List[Any],
) -> List[Dict[str, Any]]:
    """Project supervisory signals from explicit current-round inputs."""
    signals: List[Dict[str, Any]] = []
    need_lookup = {need.need_type: need for need in needs}
    intent_lookup = {intent.intent_type: intent for intent in intents}

    backlog_need = need_lookup.get(_REVIEW_API_B_JUDGEMENT_NEED)
    backlog_intent = intent_lookup.get("review_governance_hygiene")
    if world_model.governance_load_state in {"busy", "strained"}:
        signals.append(
            {
                "signal_type": "governance_review_suggestion",
                "priority": _clamp01(
                    (backlog_need.severity if backlog_need else 0.45)
                    + (0.08 if world_model.governance_load_state == "strained" else 0.0)
                ),
                "message": "API-B 判断在途提示：在继续累积更多自主工作前，应先观察并复核判断段。",
                "rationale": (
                    backlog_need.rationale
                    if backlog_need is not None
                    else "API-B 判断在途压力与复核债务都提示当前应先检查判断段。"
                ),
                "source_needs": (
                    [backlog_need.need_type]
                    if backlog_need is not None
                    else [_REVIEW_API_B_JUDGEMENT_NEED]
                ),
                "related_intent": backlog_intent.intent_type if backlog_intent is not None else None,
                "payload": {
                    "governance_load_state": world_model.governance_load_state,
                    "api_b_judgement_count": perception.api_b_judgement_count,
                    "stale_backlog_count": perception.stale_backlog_count,
                    "pending_review_count": perception.pending_review_count,
                },
            }
        )
    elif (
        backlog_need is not None
        and (
            perception.pending_review_count > 0
            or perception.stale_backlog_count > 0
            or perception.api_b_judgement_count > 0
        )
    ):
        signals.append(
            {
                "signal_type": "governance_review_suggestion",
                "priority": _clamp01(backlog_need.severity + 0.06),
                "message": "即便尚未出现完整阻塞，只要已经出现复核债务或陈旧治理项，也建议先做治理复核。",
                "rationale": backlog_need.rationale,
                "source_needs": [backlog_need.need_type],
                "related_intent": backlog_intent.intent_type if backlog_intent is not None else None,
                "payload": {
                    "governance_load_state": world_model.governance_load_state,
                    "api_b_judgement_count": perception.api_b_judgement_count,
                    "stale_backlog_count": perception.stale_backlog_count,
                    "pending_review_count": perception.pending_review_count,
                    "trigger": "early_review_debt",
                },
            }
        )

    truthfulness_need = need_lookup.get("repair_truthfulness")
    truthfulness_intent = intent_lookup.get("review_truthfulness_signals")
    if truthfulness_need is not None and has_truthfulness_review_signal(perception):
        signals.append(
            {
                "signal_type": "observation_signal",
                "priority": _clamp01(
                    truthfulness_need.severity
                    + 0.08
                    + adaptive_policy.truthfulness_bias * 0.1
                ),
                "message": "当前建议把观察焦点落在真实性侧，因为修正压力正在上升，即使整体内生驱动也在放缓。",
                "rationale": truthfulness_need.rationale,
                "source_needs": [truthfulness_need.need_type],
                "related_intent": (
                    truthfulness_intent.intent_type
                    if truthfulness_intent is not None
                    else None
                ),
                "payload": {
                    "observation_target": "truthfulness",
                    "correction_signals": perception.correction_signals,
                    "recent_errors": perception.recent_errors,
                    "uncertainty_count": perception.uncertainty_count,
                    "system_posture": perception.system_posture,
                },
            }
        )

    observe_need = need_lookup.get("observe_before_acting")
    observe_intent = intent_lookup.get("observe_before_acting")
    if observe_need is not None:
        signals.extend(
            [
                {
                    "signal_type": "observation_signal",
                    "priority": _clamp01(
                        observe_need.severity
                        + 0.06
                        + adaptive_policy.observation_bias * 0.12
                    ),
                    "message": "在继续扩大自主输出前，建议先补观察，因为当前内生驱动正遭遇阻塞或准备度偏弱。",
                    "rationale": observe_need.rationale,
                    "source_needs": [observe_need.need_type],
                    "related_intent": observe_intent.intent_type if observe_intent is not None else None,
                    "payload": {
                        "observation_target": reflection.dominant_constraint,
                        "api_b_judgement_blockage_state": reflection.api_b_judgement_blockage_state,
                        "autonomy_readiness": round(reflection.autonomy_readiness, 4),
                        "repeated_drive_pressure": round(reflection.repeated_drive_pressure, 4),
                    },
                },
                {
                    "signal_type": "autonomy_alignment_signal",
                    "priority": _clamp01(
                        observe_need.urgency
                        + 0.04
                        + adaptive_policy.observation_bias * 0.16
                    ),
                    "message": "在继续推出更多候选工作前，应先对齐并收紧自主输出节奏。",
                    "rationale": f"{reflection.rationale} {adaptive_policy.rationale}",
                    "source_needs": [observe_need.need_type],
                    "related_intent": observe_intent.intent_type if observe_intent is not None else None,
                    "payload": {
                        "dominant_constraint": reflection.dominant_constraint,
                        "learning_yield_state": reflection.learning_yield_state,
                        "api_b_judgement_blockage_state": reflection.api_b_judgement_blockage_state,
                    },
                },
            ]
        )
    elif perception.learning_quality >= 75.0:
        source_need = "prepare_body_growth"
        signals.append(
            {
                "signal_type": "observation_signal",
                "priority": _clamp01(
                    0.52 + 0.1 + min(perception.correction_signals, 4) * 0.04
                ),
                "message": "当前建议先补观察，因为学习质量显示可能正在形成新的成长窗口。",
                "rationale": (
                    need_lookup[source_need].rationale
                    if source_need in need_lookup
                    else "The current state warrants supervisory observation."
                ),
                "source_needs": [source_need],
                "related_intent": "prepare_body_growth",
                "payload": {
                    "observation_target": "body_growth",
                    "correction_signals": perception.correction_signals,
                    "learning_quality": round(perception.learning_quality, 4),
                    "system_posture": perception.system_posture,
                },
            }
        )

    signals.append(
        {
            "signal_type": "drive_posture_signal",
            "priority": _clamp01(0.4 + adaptive_policy.candidate_throttle * 0.3),
            "message": "本轮内生驱动已经为当前治理姿态与候选预算完成选择。",
            "rationale": adaptive_policy.rationale,
            "source_needs": [observe_need.need_type] if observe_need is not None else [],
            "related_intent": (
                observe_intent.intent_type
                if observe_need is not None and observe_intent is not None
                else None
            ),
            "payload": {
                "preferred_focus": adaptive_policy.preferred_focus,
                "candidate_budget": adaptive_policy.candidate_budget,
                "exploratory_learning_quota": adaptive_policy.exploratory_learning_quota,
                "body_growth_quota": adaptive_policy.body_growth_quota,
                "candidate_throttle": round(adaptive_policy.candidate_throttle, 4),
                "source_evidence": list(adaptive_policy.source_evidence),
            },
        }
    )
    signals.sort(key=lambda item: item["priority"], reverse=True)
    return signals
