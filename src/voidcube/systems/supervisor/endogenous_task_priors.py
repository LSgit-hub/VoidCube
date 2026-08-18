"""Pure task-type prior projection for endogenous cognition."""

from __future__ import annotations

from typing import Any, Dict, List


_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"


def build_task_type_priors(
    *,
    reflection: Dict[str, Any],
    adaptive_policy: Dict[str, Any],
    self_model_snapshot: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
    agenda_graph: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
) -> Dict[str, Any]:
    preferred_focus = str(adaptive_policy.get("preferred_focus") or "observation").strip()
    dominant_constraint = str(reflection.get("dominant_constraint") or "unknown").strip()
    self_gaps = list(self_model_snapshot.get("self_understanding_gaps") or [])
    weak_channels = list(evidence_credibility_summary.get("weak_or_missing_channels") or [])
    high_channels = list(evidence_credibility_summary.get("high_credibility_channels") or [])
    unresolved_gaps = [
        str(item.get("gap") or "").strip()
        for item in list(agenda_graph.get("unresolved_gaps") or [])[:4]
        if isinstance(item, dict) and str(item.get("gap") or "").strip()
    ]
    alignment_score = _clamp01(recent_reference_alignment.get("average_alignment_score") or 0.0)
    drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
    drift_average_score = _clamp01(proposal_drift_memory.get("average_score") or 0.0)

    observation_score, review_score = 0.22, 0.2
    learning_score, maintenance_score, improvement_score = 0.24, 0.12, 0.1
    if preferred_focus == "observation":
        observation_score += 0.22
        review_score += 0.08
    if preferred_focus == "truthfulness":
        review_score += 0.2
        observation_score += 0.06
    if preferred_focus == "learning_expansion":
        learning_score += 0.18
    if preferred_focus == "memory_continuity":
        maintenance_score += 0.18
    if preferred_focus == "body_growth":
        improvement_score += 0.16
    if dominant_constraint in {"weak_learning_yield", "historical_underdelivery"}:
        observation_score += 0.12
        review_score += 0.1
    if dominant_constraint == _API_B_JUDGEMENT_BLOCKAGE:
        review_score += 0.14
        maintenance_score += 0.08
    if self_gaps:
        observation_score += min(len(self_gaps), 4) * 0.06
        learning_score += 0.08
        improvement_score -= 0.05
    if weak_channels:
        observation_score += min(len(weak_channels), 3) * 0.05
        review_score += 0.07
        improvement_score -= 0.06
    if high_channels:
        learning_score += 0.06
        improvement_score += 0.04
    if alignment_score < 0.7:
        observation_score += 0.08
        review_score += 0.08
        improvement_score -= 0.05
    if drift_state == "drifting":
        observation_score += 0.14
        review_score += 0.14
        learning_score -= 0.05
        improvement_score -= 0.08
    elif drift_state == "correcting":
        observation_score += 0.08
        review_score += 0.06
        improvement_score -= 0.04
    if drift_average_score < 0.5 and proposal_drift_memory.get("available"):
        observation_score += 0.06
        review_score += 0.05
        improvement_score -= 0.04
    if "prepare_body_growth" in unresolved_gaps and not self_gaps and alignment_score >= 0.7:
        improvement_score += 0.1

    rows = [
        ("observation", observation_score),
        ("review", review_score),
        ("learning", learning_score),
        ("maintenance", maintenance_score),
        ("improvement", improvement_score),
    ]
    prior_rows = [
        {
            "task_type": task_type,
            "score": round(_clamp01(score), 4),
            "reasons": _task_type_prior_reasons(
                task_type=task_type,
                preferred_focus=preferred_focus,
                dominant_constraint=dominant_constraint,
                self_gaps=self_gaps,
                weak_channels=weak_channels,
                unresolved_gaps=unresolved_gaps,
                alignment_score=alignment_score,
                drift_state=drift_state,
            ),
        }
        for task_type, score in rows
    ]
    prior_rows.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    top = prior_rows[0] if prior_rows else {"task_type": "observation", "score": 0.0}
    return {
        "top_priority_task_type": top["task_type"],
        "top_priority_score": top["score"],
        "priors": prior_rows,
        "drift_state": drift_state or "stable",
        "summary": (
            f"Program-side governance priors currently lean toward {top['task_type']} "
            f"as the safest task-type hint (score={float(top['score']):.2f}); "
            f"proposal drift state is {drift_state or 'stable'}."
        ),
    }


def _task_type_prior_reasons(
    *,
    task_type: str,
    preferred_focus: str,
    dominant_constraint: str,
    self_gaps: List[str],
    weak_channels: List[str],
    unresolved_gaps: List[str],
    alignment_score: float,
    drift_state: str,
) -> List[str]:
    reasons: List[str] = []
    if task_type == "observation":
        if self_gaps:
            reasons.append("self_understanding_gaps_require_more_observation")
        if weak_channels:
            reasons.append("weak_channels_make_direct_action_less_reliable")
        if alignment_score < 0.7:
            reasons.append("reference_alignment_is_not_yet_stable")
        if drift_state == "drifting":
            reasons.append("proposal_drift_requires_more_observation")
    elif task_type == "review":
        if dominant_constraint in {_API_B_JUDGEMENT_BLOCKAGE, "historical_underdelivery"}:
            reasons.append("dominant_constraint_calls_for_review")
        if preferred_focus == "truthfulness":
            reasons.append("preferred_focus_is_truthfulness")
        if alignment_score < 0.7:
            reasons.append("review_can_repair_reference_drift")
        if drift_state in {"drifting", "correcting"}:
            reasons.append("review_can_help_correct_recent_proposal_drift")
    elif task_type == "learning":
        if preferred_focus == "learning_expansion":
            reasons.append("preferred_focus_is_learning_expansion")
        if "expand_learning_frontier" in unresolved_gaps:
            reasons.append("agenda_still_contains_learning_frontier_gap")
        if self_gaps:
            reasons.append("learning_can_reduce_self_model_gaps")
        if drift_state == "drifting":
            reasons.append("learning_should_wait_until_drift_stabilizes")
    elif task_type == "maintenance":
        if preferred_focus == "memory_continuity":
            reasons.append("preferred_focus_is_memory_continuity")
        if dominant_constraint == _API_B_JUDGEMENT_BLOCKAGE:
            reasons.append("maintenance_can_reduce_api_b_judgement_pressure")
    elif task_type == "improvement":
        if "prepare_body_growth" in unresolved_gaps:
            reasons.append("agenda_contains_body_growth_gap")
        if alignment_score >= 0.7 and not self_gaps:
            reasons.append("evidence_is_stable_enough_for_guarded_improvement")
        if weak_channels:
            reasons.append("improvement_should_remain_guarded_under_weak_channels")
        if drift_state in {"drifting", "correcting"}:
            reasons.append("recent_proposal_drift_discourages_direct_improvement")
    if not reasons:
        reasons.append("baseline_program_prior")
    return reasons[:4]


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
