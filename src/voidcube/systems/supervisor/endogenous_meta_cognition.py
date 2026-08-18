"""Pure projections for endogenous proposal drift and meta-cognition memory."""

from __future__ import annotations

from typing import Any, Dict


def build_recent_cognitive_alignment_summary(
    history_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    drive_history = dict(history_snapshot or {})
    outcomes = [
        dict(item)
        for item in list(drive_history.get("outcomes") or [])
        if isinstance(item, dict)
    ]
    entry_count = 0
    score_total = 0.0
    quality_counts = {"strong": 0, "partial": 0, "weak": 0}
    for outcome in outcomes[:12]:
        cognitive_alignment = outcome.get("cognitive_alignment")
        metadata = dict(outcome.get("metadata") or {})
        evidence = dict(outcome.get("evidence") or {})
        if not isinstance(cognitive_alignment, dict):
            cognitive_alignment = metadata.get("cognitive_alignment")
        if not isinstance(cognitive_alignment, dict):
            cognitive_alignment = evidence.get("cognitive_alignment")
        if not isinstance(cognitive_alignment, dict) or not cognitive_alignment:
            continue
        quality = str(cognitive_alignment.get("quality") or "partial").strip().lower()
        if quality not in quality_counts:
            quality = "partial"
        quality_counts[quality] += 1
        score_total += float(cognitive_alignment.get("score") or 0.0)
        entry_count += 1
        if entry_count >= 4:
            break
    if not entry_count:
        return {
            "available": False,
            "average_score": 0.0,
            "quality_counts": {},
        }
    average_score = score_total / entry_count
    return {
        "available": True,
        "average_score": round(_clamp01(average_score), 4),
        "quality_counts": quality_counts,
    }


def build_proposal_drift_memory(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    drive_history = dict(drive_context.get("drive_history") or {})
    outcomes = [
        dict(item)
        for item in list(drive_history.get("outcomes") or [])
        if isinstance(item, dict)
    ]
    entry_count = 0
    score_total = 0.0
    quality_counts = {"strong": 0, "partial": 0, "weak": 0}
    posture_alignment_counts: Dict[str, int] = {}
    priority_basis_counts: Dict[str, int] = {}
    posture_conflict_reason_counts: Dict[str, int] = {}
    missing_posture_alignment_count = 0
    missing_priority_basis_count = 0
    for outcome in outcomes[:12]:
        metadata = dict(outcome.get("metadata") or {})
        evidence = dict(outcome.get("evidence") or {})
        cognitive_alignment = outcome.get("cognitive_alignment")
        if not isinstance(cognitive_alignment, dict):
            cognitive_alignment = metadata.get("cognitive_alignment")
        if not isinstance(cognitive_alignment, dict):
            cognitive_alignment = evidence.get("cognitive_alignment")
        if not isinstance(cognitive_alignment, dict):
            continue
        quality = str(cognitive_alignment.get("quality") or "partial").strip().lower() or "partial"
        if quality not in quality_counts:
            quality = "partial"
        quality_counts[quality] += 1
        posture_alignment = outcome.get("llm_posture_alignment")
        if not isinstance(posture_alignment, list):
            posture_alignment = metadata.get("llm_posture_alignment")
        if not isinstance(posture_alignment, list):
            posture_alignment = evidence.get("llm_posture_alignment")
        normalized_posture_alignment = [
            str(item).strip()
            for item in list(posture_alignment or [])[:3]
            if str(item).strip()
        ]
        if normalized_posture_alignment:
            for item in normalized_posture_alignment:
                posture_alignment_counts[item] = posture_alignment_counts.get(item, 0) + 1
        else:
            missing_posture_alignment_count += 1
        priority_basis = outcome.get("llm_priority_basis")
        if not isinstance(priority_basis, list):
            priority_basis = metadata.get("llm_priority_basis")
        if not isinstance(priority_basis, list):
            priority_basis = evidence.get("llm_priority_basis")
        normalized_priority_basis = [
            str(item).strip()
            for item in list(priority_basis or [])[:3]
            if str(item).strip()
        ]
        if normalized_priority_basis:
            for item in normalized_priority_basis:
                priority_basis_counts[item] = priority_basis_counts.get(item, 0) + 1
        else:
            missing_priority_basis_count += 1
        conflict_reasons = [
            str(item).strip()
            for item in list(cognitive_alignment.get("reasons") or [])[:4]
            if str(item).strip()
            and (
                "posture" in str(item).strip().lower()
                or "priority" in str(item).strip().lower()
                or "task_type" in str(item).strip().lower()
            )
        ]
        for item in conflict_reasons:
            posture_conflict_reason_counts[item] = posture_conflict_reason_counts.get(item, 0) + 1
        score_total += float(cognitive_alignment.get("score") or 0.0)
        entry_count += 1
        if entry_count >= 4:
            break

    if not entry_count:
        return {
            "available": False,
            "average_score": 0.0,
            "quality_counts": {},
            "drift_state": "unknown",
            "posture_alignment_signal_count": 0,
            "priority_basis_signal_count": 0,
            "missing_posture_alignment_count": 0,
            "missing_priority_basis_count": 0,
            "posture_alignment_health": "unknown",
            "priority_basis_health": "unknown",
            "dominant_posture_conflict_reason": None,
            "summary": "No recent proposal-drift memory is available yet.",
        }

    avg_score = score_total / entry_count
    weak_or_partial = quality_counts["weak"] + quality_counts["partial"]
    drift_state = "stable"
    if quality_counts["weak"] >= 2 or avg_score < 0.45:
        drift_state = "drifting"
    elif (quality_counts["weak"] >= 1 and quality_counts["strong"] >= 1) or weak_or_partial >= 2:
        drift_state = "correcting"
    posture_alignment_signals = [
        item
        for item, _count in sorted(
            posture_alignment_counts.items(),
            key=lambda pair: (-pair[1], pair[0]),
        )[:4]
    ]
    priority_basis_signals = [
        item
        for item, _count in sorted(
            priority_basis_counts.items(),
            key=lambda pair: (-pair[1], pair[0]),
        )[:4]
    ]
    dominant_posture_conflict_reason = None
    if posture_conflict_reason_counts:
        dominant_posture_conflict_reason = max(
            posture_conflict_reason_counts.items(),
            key=lambda pair: (pair[1], pair[0]),
        )[0]
    posture_alignment_health = "strong"
    if missing_posture_alignment_count >= 2:
        posture_alignment_health = "missing"
    elif quality_counts["weak"] >= 1 or dominant_posture_conflict_reason:
        posture_alignment_health = "inconsistent"
    priority_basis_health = "strong"
    if missing_priority_basis_count >= 2:
        priority_basis_health = "missing"
    elif quality_counts["weak"] >= 1 or not priority_basis_signals:
        priority_basis_health = "inconsistent"
    return {
        "available": True,
        "average_score": round(_clamp01(avg_score), 4),
        "quality_counts": quality_counts,
        "drift_state": drift_state,
        "posture_alignment_signal_count": len(posture_alignment_signals),
        "priority_basis_signal_count": len(priority_basis_signals),
        "missing_posture_alignment_count": missing_posture_alignment_count,
        "missing_priority_basis_count": missing_priority_basis_count,
        "dominant_posture_conflict_reason": dominant_posture_conflict_reason,
        "posture_alignment_health": posture_alignment_health,
        "priority_basis_health": priority_basis_health,
        "summary": (
            f"Recent proposal alignment is {drift_state}; average cognitive alignment "
            f"score is {_clamp01(avg_score):.2f}."
        ),
    }


def build_meta_cognition_profile(
    *,
    grounding_focus: Dict[str, Any],
    self_iteration_hypotheses: Dict[str, Any],
    cognitive_assessment_memory: Dict[str, Any],
    self_iteration_trend_memory: Dict[str, Any],
    switch_self_regulation_memory: Dict[str, Any],
    post_task_effect_memory: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
    task_type_priors: Dict[str, Any],
) -> Dict[str, Any]:
    current_judgement = str(
        cognitive_assessment_memory.get("current_judgement") or ""
    ).strip()
    dominant_constraint = str(
        cognitive_assessment_memory.get("dominant_constraint") or ""
    ).strip()
    lm_self_iteration_target = str(
        cognitive_assessment_memory.get("self_iteration_target") or ""
    ).strip()
    lm_self_iteration_hypothesis = str(
        cognitive_assessment_memory.get("self_iteration_hypothesis") or ""
    ).strip()
    top_self_iteration_domain = str(
        lm_self_iteration_target
        or self_iteration_trend_memory.get("dominant_target")
        or self_iteration_hypotheses.get("top_target_domain")
        or ""
    ).strip()
    hypotheses = list(self_iteration_hypotheses.get("hypotheses") or [])
    top_self_iteration_hypothesis = str(
        lm_self_iteration_hypothesis
        or self_iteration_trend_memory.get("dominant_hypothesis")
        or self_iteration_hypotheses.get("dominant_hypothesis")
        or (
            hypotheses[0].get("hypothesis")
            if hypotheses and isinstance(hypotheses[0], dict)
            else ""
        )
        or ""
    ).strip()
    stay_or_switch_bias = str(
        self_iteration_trend_memory.get("dominant_stay_or_switch")
        or switch_self_regulation_memory.get("preferred_switch_bias")
        or ""
    ).strip()
    if stay_or_switch_bias == "balanced":
        stay_or_switch_bias = ""
    switch_bias_effectiveness = str(
        switch_self_regulation_memory.get("switch_effectiveness") or ""
    ).strip()
    if stay_or_switch_bias == "stay":
        switch_bias_effectiveness = str(
            switch_self_regulation_memory.get("stay_effectiveness") or ""
        ).strip()
    recent_effect_direction = str(
        post_task_effect_memory.get("effect_direction") or ""
    ).strip()
    why_not_improvement_now = str(
        cognitive_assessment_memory.get("why_not_improvement_now") or ""
    ).strip()
    grounding_gap_count = len(
        [
            str(item).strip()
            for item in list(grounding_focus.get("grounding_gaps") or [])
            if str(item).strip()
        ]
    )
    contradictory_count = len(
        [
            str(item).strip()
            for item in list(grounding_focus.get("contradictory_topics") or [])
            if str(item).strip()
        ]
    )
    grounding_pressure = "low"
    if grounding_gap_count >= 4 or contradictory_count >= 2:
        grounding_pressure = "high"
    elif grounding_gap_count >= 1 or contradictory_count >= 1:
        grounding_pressure = "medium"

    top_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
    drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
    dominant_failure_mode = ""
    if grounding_pressure == "high":
        dominant_failure_mode = "grounding_instability"
    elif drift_state in {"drifting", "correcting"}:
        dominant_failure_mode = "proposal_selection_drift"
    elif recent_effect_direction == "degrading":
        dominant_failure_mode = "self_iteration_not_improving_outcomes"
    elif dominant_constraint:
        dominant_failure_mode = dominant_constraint

    governance_posture = "review"
    if grounding_pressure == "high":
        governance_posture = "observation_or_review"
    elif recent_effect_direction == "degrading":
        governance_posture = "review"
    elif why_not_improvement_now:
        governance_posture = "review"
    elif current_judgement and any(
        token in current_judgement.lower()
        for token in ("review", "observe", "observation", "grounding")
    ):
        governance_posture = "review"
    elif top_task_type in {"observation", "review"}:
        governance_posture = top_task_type

    has_substantive_profile = any(
        [
            current_judgement,
            dominant_constraint,
            top_self_iteration_domain,
            top_self_iteration_hypothesis,
            stay_or_switch_bias,
            switch_bias_effectiveness,
            recent_effect_direction,
            dominant_failure_mode,
            grounding_pressure != "low",
        ]
    )
    priority_signals = [
        f"grounding_pressure:{grounding_pressure}" if grounding_pressure != "low" else "",
        f"top_self_iteration_domain:{top_self_iteration_domain}" if top_self_iteration_domain else "",
        f"stay_or_switch_bias:{stay_or_switch_bias}" if stay_or_switch_bias else "",
        f"recent_effect_direction:{recent_effect_direction}" if recent_effect_direction else "",
        f"dominant_failure_mode:{dominant_failure_mode}" if dominant_failure_mode else "",
    ]
    priority_signals = [item for item in priority_signals if item]

    if not has_substantive_profile:
        return {
            "available": False,
            "summary": "当前还没有可用的统一元认知画像。",
        }

    return {
        "available": True,
        "current_judgement": current_judgement or None,
        "dominant_constraint": dominant_constraint or None,
        "grounding_pressure": grounding_pressure,
        "top_self_iteration_domain": top_self_iteration_domain or None,
        "top_self_iteration_hypothesis": top_self_iteration_hypothesis or None,
        "stay_or_switch_bias": stay_or_switch_bias or None,
        "switch_bias_effectiveness": switch_bias_effectiveness or None,
        "recent_effect_direction": recent_effect_direction or None,
        "dominant_failure_mode": dominant_failure_mode or None,
        "governance_posture": governance_posture,
        "priority_signals": priority_signals[:6],
        "summary": (
            "Unified meta-cognition indicates "
            f"judgement={current_judgement or 'unknown'}; "
            f"constraint={dominant_constraint or 'unknown'}; "
            f"grounding_pressure={grounding_pressure}; "
            f"self_iteration_domain={top_self_iteration_domain or 'unknown'}; "
            f"recent_effect_direction={recent_effect_direction or 'unknown'}."
        ),
    }


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
