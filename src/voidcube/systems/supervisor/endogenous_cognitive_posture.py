"""Pure cognitive posture selection for endogenous drive."""

from __future__ import annotations

from typing import Any, Dict

from .endogenous_policy import TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD


_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"


def resolve_cognitive_posture_from_policy(
    *,
    policy: Dict[str, Any],
    deliberation_dict: Dict[str, Any],
    self_model_snapshot: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
    recent_cognitive_alignment: Dict[str, Any],
) -> Dict[str, Any]:
    profiles = dict(policy.get("posture_profiles") or {})
    selection_mode = str(policy.get("posture_selection_mode") or "auto").strip().lower()
    manual_profile = str(policy.get("active_posture_profile") or "balanced").strip().lower()
    profile_name = manual_profile or "balanced"
    selection_reason = "manual_selection"
    perception = dict(deliberation_dict.get("perception") or {})
    reflection = dict(deliberation_dict.get("reflection") or {})
    weak_channels = [
        str(item).strip()
        for item in list(evidence_credibility_summary.get("weak_or_missing_channels") or [])[:6]
        if str(item).strip()
    ]
    self_gaps = [
        str(item).strip()
        for item in list(self_model_snapshot.get("self_understanding_gaps") or [])[:6]
        if str(item).strip()
    ]
    weak_reference_count = max(0, int(recent_reference_alignment.get("weak_or_partial_count") or 0))
    correction_signals = max(0, int(perception.get("correction_signals") or 0))
    active_sessions = max(0, int(perception.get("active_sessions") or 0))
    readiness_score = _clamp01(
        (self_model_snapshot.get("readiness") or {}).get("self_iteration_readiness_score") or 0.0
    )
    drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
    posture_alignment_health = str(
        proposal_drift_memory.get("posture_alignment_health") or ""
    ).strip().lower()
    priority_basis_health = str(
        proposal_drift_memory.get("priority_basis_health") or ""
    ).strip().lower()
    missing_posture_alignment_count = max(
        0, int(proposal_drift_memory.get("missing_posture_alignment_count") or 0)
    )
    missing_priority_basis_count = max(
        0, int(proposal_drift_memory.get("missing_priority_basis_count") or 0)
    )
    dominant_posture_conflict_reason = str(
        proposal_drift_memory.get("dominant_posture_conflict_reason") or ""
    ).strip().lower()
    alignment_average_score = _clamp01(recent_cognitive_alignment.get("average_score") or 0.0)
    dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()

    if selection_mode != "manual":
        service_threshold = max(0, int(policy.get("auto_service_active_sessions_threshold") or 1))
        truthfulness_threshold = max(
            1,
            int(
                policy.get("auto_truthfulness_correction_signal_threshold")
                or TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
            ),
        )
        evidence_threshold = max(1, int(policy.get("auto_evidence_repair_signal_threshold") or 3))
        explanation_missing_threshold = max(
            1, int(policy.get("auto_explanation_repair_missing_threshold") or 2)
        )
        explanation_inconsistent_threshold = max(
            1, int(policy.get("auto_explanation_repair_inconsistent_threshold") or 1)
        )
        explanation_missing_pressure = max(
            missing_posture_alignment_count,
            missing_priority_basis_count,
        )
        explanation_inconsistent_pressure = int(posture_alignment_health == "inconsistent")
        explanation_inconsistent_pressure += int(priority_basis_health == "inconsistent")
        if active_sessions >= service_threshold:
            profile_name = "conservative"
            selection_reason = "service_pressure_requires_conservative_posture"
        elif correction_signals >= truthfulness_threshold:
            profile_name = "truthfulness_first"
            selection_reason = "truthfulness_signals_are_elevated"
        elif (
            explanation_missing_pressure >= explanation_missing_threshold
            and explanation_inconsistent_pressure >= explanation_inconsistent_threshold
        ):
            profile_name = "evidence_repair_first"
            selection_reason = "explanation_quality_requires_evidence_repair"
        elif explanation_missing_pressure >= explanation_missing_threshold:
            profile_name = "observe_first"
            selection_reason = "missing_explanation_memory_requires_observation"
        elif explanation_inconsistent_pressure >= explanation_inconsistent_threshold:
            if "truthfulness" in dominant_posture_conflict_reason or "reference_alignment" in dominant_posture_conflict_reason:
                profile_name = "truthfulness_first"
                selection_reason = "explanation_conflict_requires_truthfulness_repair"
            else:
                profile_name = "observe_first"
                selection_reason = "explanation_conflict_requires_observation"
        elif (
            weak_reference_count >= evidence_threshold
            or len(weak_channels) >= evidence_threshold
            or "reference_alignment_is_unstable" in self_gaps
        ):
            profile_name = "evidence_repair_first"
            selection_reason = "evidence_repair_pressure_is_elevated"
        elif (
            drift_state in {"drifting", "correcting"}
            or alignment_average_score < _clamp01(policy.get("drift_observe_trigger_score") or 0.5)
            or readiness_score < _clamp01(policy.get("readiness_min_score") or 0.52)
            or dominant_constraint in {_API_B_JUDGEMENT_BLOCKAGE, "historical_underdelivery"}
        ):
            profile_name = "observe_first"
            selection_reason = "drift_or_readiness_requires_observation"
        else:
            profile_name = "balanced"
            selection_reason = "balanced_posture_is_sufficient"

    selected = profiles.get(profile_name)
    if not isinstance(selected, dict):
        profile_name = "balanced"
        selected = profiles.get(profile_name) or {}
    return {
        "name": profile_name,
        "selection_mode": selection_mode,
        "selection_reason": selection_reason,
        "summary": str(selected.get("summary") or "").strip(),
        "observation_multiplier": round(_clamp01(selected.get("observation_multiplier") or 1.0), 4),
        "throttle_multiplier": round(_clamp01(selected.get("throttle_multiplier") or 1.0), 4),
        "truthfulness_multiplier": round(_clamp01(selected.get("truthfulness_multiplier") or 1.0), 4),
        "learning_suppression_multiplier": round(
            _clamp01(selected.get("learning_suppression_multiplier") or 1.0),
            4,
        ),
    }


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
