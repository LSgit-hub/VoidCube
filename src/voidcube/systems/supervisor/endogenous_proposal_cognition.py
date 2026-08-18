"""Pure read-model assembly for proposal cognition."""

from __future__ import annotations

from typing import Any, Dict


def compact_proposal_memory(
    *,
    recent_reference_alignment: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
    recent_cognitive_alignment: Dict[str, Any],
    cognitive_assessment_memory: Dict[str, Any],
    self_iteration_hypotheses: Dict[str, Any],
    self_iteration_trend_memory: Dict[str, Any],
    switch_self_regulation_memory: Dict[str, Any],
    post_task_effect_memory: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    def stored_count(item: Dict[str, Any], key: str) -> int:
        return max(0, int(item.get(key) or 0))

    def signal_count(item: Dict[str, Any], keys: tuple[str, ...]) -> int:
        return max([0, *(stored_count(item, key) for key in keys)])

    def text_value(item: Dict[str, Any], key: str) -> str:
        return str(item.get(key) or "").strip()

    return {
        "recent_reference_alignment": {
            "available": bool(recent_reference_alignment.get("available")),
            "average_alignment_score": round(
                _clamp_ratio(recent_reference_alignment.get("average_alignment_score") or 0.0),
                4,
            ),
            "weak_or_partial_count": stored_count(
                recent_reference_alignment, "weak_or_partial_count"
            ),
            "entry_count": stored_count(recent_reference_alignment, "entry_count"),
            "primary_missing_evidence_node": text_value(
                recent_reference_alignment, "primary_missing_evidence_node"
            )
            or None,
            "primary_missing_agenda_node": text_value(
                recent_reference_alignment, "primary_missing_agenda_node"
            )
            or None,
            "missing_evidence_node_count": stored_count(
                recent_reference_alignment, "missing_evidence_node_count"
            ),
            "missing_agenda_node_count": stored_count(
                recent_reference_alignment, "missing_agenda_node_count"
            ),
        },
        "proposal_drift_memory": {
            "available": bool(proposal_drift_memory.get("available")),
            "average_score": round(
                _clamp_ratio(proposal_drift_memory.get("average_score") or 0.0),
                4,
            ),
            "drift_state": text_value(proposal_drift_memory, "drift_state"),
            "quality_counts": dict(proposal_drift_memory.get("quality_counts") or {}),
            "posture_alignment_signal_count": stored_count(
                proposal_drift_memory, "posture_alignment_signal_count"
            ),
            "priority_basis_signal_count": stored_count(
                proposal_drift_memory, "priority_basis_signal_count"
            ),
            "missing_posture_alignment_count": stored_count(
                proposal_drift_memory, "missing_posture_alignment_count"
            ),
            "missing_priority_basis_count": stored_count(
                proposal_drift_memory, "missing_priority_basis_count"
            ),
            "posture_alignment_health": text_value(
                proposal_drift_memory, "posture_alignment_health"
            ),
            "priority_basis_health": text_value(
                proposal_drift_memory, "priority_basis_health"
            ),
            "dominant_posture_conflict_reason": text_value(
                proposal_drift_memory, "dominant_posture_conflict_reason"
            )
            or None,
        },
        "recent_cognitive_alignment": {
            "available": bool(recent_cognitive_alignment.get("available")),
            "average_score": round(
                _clamp_ratio(recent_cognitive_alignment.get("average_score") or 0.0),
                4,
            ),
            "quality_counts": dict(recent_cognitive_alignment.get("quality_counts") or {}),
            "dominant_task_shape": text_value(
                recent_cognitive_alignment, "dominant_task_shape"
            )
            or None,
            "reason_count": stored_count(recent_cognitive_alignment, "reason_count"),
            "posture_alignment_signal_count": stored_count(
                recent_cognitive_alignment, "posture_alignment_signal_count"
            ),
            "priority_basis_signal_count": stored_count(
                recent_cognitive_alignment, "priority_basis_signal_count"
            ),
            "missing_posture_alignment_count": stored_count(
                recent_cognitive_alignment, "missing_posture_alignment_count"
            ),
            "missing_priority_basis_count": stored_count(
                recent_cognitive_alignment, "missing_priority_basis_count"
            ),
            "entry_count": stored_count(recent_cognitive_alignment, "entry_count"),
        },
        "cognitive_assessment_memory": {
            "available": bool(cognitive_assessment_memory.get("available")),
            "dominant_constraint": text_value(
                cognitive_assessment_memory, "dominant_constraint"
            ),
            "current_judgement_count": max(
                signal_count(cognitive_assessment_memory, ("current_judgement_count",)),
                int(bool(text_value(cognitive_assessment_memory, "current_judgement"))),
            ),
            "self_iteration_target_count": max(
                signal_count(
                    cognitive_assessment_memory,
                    ("self_iteration_target_count", "target_count"),
                ),
                int(bool(text_value(cognitive_assessment_memory, "self_iteration_target"))),
            ),
            "self_iteration_hypothesis_count": max(
                signal_count(
                    cognitive_assessment_memory,
                    ("self_iteration_hypothesis_count", "hypothesis_count"),
                ),
                int(bool(text_value(cognitive_assessment_memory, "self_iteration_hypothesis"))),
            ),
        },
        "self_iteration_hypotheses": {
            "available": bool(self_iteration_hypotheses.get("available")),
            "dominant_hypothesis": text_value(
                self_iteration_hypotheses, "dominant_hypothesis"
            ),
            "top_target_domain": text_value(self_iteration_hypotheses, "top_target_domain"),
            "hypothesis_count": max(
                stored_count(self_iteration_hypotheses, "hypothesis_count"),
                int(bool(text_value(self_iteration_hypotheses, "dominant_hypothesis"))),
            ),
        },
        "self_iteration_trend_memory": {
            "available": bool(self_iteration_trend_memory.get("available")),
            "dominant_target": text_value(self_iteration_trend_memory, "dominant_target"),
            "trend_state": text_value(self_iteration_trend_memory, "trend_state"),
            "target_stability": text_value(
                self_iteration_trend_memory, "target_stability"
            ),
            "target_count": max(
                signal_count(
                    self_iteration_trend_memory,
                    ("target_count", "target_signal_count"),
                ),
                int(bool(text_value(self_iteration_trend_memory, "dominant_target"))),
            ),
            "hypothesis_count": max(
                signal_count(
                    self_iteration_trend_memory,
                    ("hypothesis_count", "hypothesis_signal_count"),
                ),
                int(bool(text_value(self_iteration_trend_memory, "dominant_hypothesis"))),
            ),
            "stay_or_switch_count": max(
                signal_count(
                    self_iteration_trend_memory,
                    ("stay_or_switch_count", "stay_or_switch_signal_count"),
                ),
                int(bool(text_value(self_iteration_trend_memory, "dominant_stay_or_switch"))),
            ),
            "switch_reason_count": max(
                signal_count(
                    self_iteration_trend_memory,
                    ("switch_reason_count", "switch_reason_signal_count"),
                ),
                int(bool(text_value(self_iteration_trend_memory, "dominant_switch_reason"))),
            ),
        },
        "switch_self_regulation_memory": {
            "available": bool(switch_self_regulation_memory.get("available")),
            "preferred_switch_bias": text_value(
                switch_self_regulation_memory, "preferred_switch_bias"
            ),
            "switch_effectiveness": text_value(
                switch_self_regulation_memory, "switch_effectiveness"
            ),
            "stay_effectiveness": text_value(
                switch_self_regulation_memory, "stay_effectiveness"
            ),
            "average_switch_quality": round(
                _clamp_ratio(switch_self_regulation_memory.get("average_switch_quality") or 0.0),
                4,
            ),
            "average_stay_quality": round(
                _clamp_ratio(switch_self_regulation_memory.get("average_stay_quality") or 0.0),
                4,
            ),
            "stay_or_switch_count": signal_count(
                switch_self_regulation_memory,
                ("stay_or_switch_count", "stay_or_switch_signal_count"),
            ),
        },
        "post_task_effect_memory": {
            "available": bool(post_task_effect_memory.get("available")),
            "effect_direction": text_value(post_task_effect_memory, "effect_direction"),
            "average_quality_score": round(
                _clamp_ratio(post_task_effect_memory.get("average_quality_score") or 0.0),
                4,
            ),
            "average_cognitive_alignment_score": round(
                _clamp_ratio(
                    post_task_effect_memory.get("average_cognitive_alignment_score")
                    or 0.0
                ),
                4,
            ),
            "average_reference_alignment_score": round(
                _clamp_ratio(
                    post_task_effect_memory.get("average_reference_alignment_score")
                    or 0.0
                ),
                4,
            ),
            "dominant_target_effect": text_value(
                post_task_effect_memory, "dominant_target_effect"
            )
            or None,
        },
    }


def _clamp_ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def build_proposal_cognition_projection(
    *,
    lm_reasoning_state: Dict[str, Any],
    cognitive_control_policy: Dict[str, Any],
    active_cognitive_posture_profile: Dict[str, Any],
    meta_cognition_profile: Dict[str, Any],
    cognitive_assessment_memory: Dict[str, Any],
    compact_memory: Dict[str, Dict[str, Any]],
    current_candidates: Dict[str, Any],
) -> Dict[str, Any]:
    proposal_drift_memory = dict(compact_memory.get("proposal_drift_memory") or {})
    drift_state = str(proposal_drift_memory.get("drift_state") or "").strip() or "unknown"
    posture_name = (
        str(active_cognitive_posture_profile.get("name") or "").strip() or "unknown"
    )
    current_judgement = str(
        cognitive_assessment_memory.get("current_judgement") or ""
    ).strip()
    why_not_improvement_now_count = max(
        0,
        int(cognitive_assessment_memory.get("why_not_improvement_now_count") or 0),
    )
    meta_cognition = {
        "available": bool(meta_cognition_profile.get("available")),
        "current_judgement": str(
            meta_cognition_profile.get("current_judgement") or ""
        ).strip(),
        "dominant_constraint": str(
            meta_cognition_profile.get("dominant_constraint") or ""
        ).strip(),
        "grounding_pressure": str(
            meta_cognition_profile.get("grounding_pressure") or ""
        ).strip(),
        "dominant_failure_mode": str(
            meta_cognition_profile.get("dominant_failure_mode") or ""
        ).strip(),
        "governance_posture": str(
            meta_cognition_profile.get("governance_posture")
            or meta_cognition_profile.get("recommended_task_posture")
            or ""
        ).strip(),
        "priority_signals": [
            str(item).strip()
            for item in list(meta_cognition_profile.get("priority_signals") or [])[:4]
            if str(item).strip()
        ],
        "self_iteration_focus": {
            "domain": str(
                meta_cognition_profile.get("top_self_iteration_domain") or ""
            ).strip()
            or None,
            "hypothesis": str(
                meta_cognition_profile.get("top_self_iteration_hypothesis") or ""
            ).strip()
            or None,
        },
    }
    return {
        "summary": f"posture={posture_name}; drift={drift_state}.",
        "lm_trace": {
            "available": bool(lm_reasoning_state),
            "status": str(lm_reasoning_state.get("status") or "").strip() or None,
            "model_role": str(lm_reasoning_state.get("model_role") or "").strip() or None,
            "charter_core_mission": str(
                dict(lm_reasoning_state.get("charter") or {}).get("core_mission") or ""
            ).strip()
            or None,
            "proposal_count": max(0, int(lm_reasoning_state.get("proposal_count") or 0)),
        },
        "cognitive_control_policy": cognitive_control_policy,
        "active_cognitive_posture_profile": active_cognitive_posture_profile,
        "meta_cognition_profile": meta_cognition,
        "assessment_trace": {
            "available": bool(cognitive_assessment_memory.get("available")),
            "dominant_constraint": str(
                cognitive_assessment_memory.get("dominant_constraint") or ""
            ).strip()
            or None,
            "current_judgement": current_judgement or None,
            "why_not_improvement_now": str(
                cognitive_assessment_memory.get("why_not_improvement_now") or ""
            ).strip()
            or None,
            "why_not_improvement_now_count": why_not_improvement_now_count,
            "self_iteration_target": str(
                cognitive_assessment_memory.get("self_iteration_target") or ""
            ).strip()
            or None,
            "self_iteration_hypothesis": str(
                cognitive_assessment_memory.get("self_iteration_hypothesis") or ""
            ).strip()
            or None,
        },
        "auxiliary_memory": compact_memory,
        "current_candidates": current_candidates,
    }
