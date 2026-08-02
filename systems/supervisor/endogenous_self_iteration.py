"""Pure self-iteration hypothesis projection for endogenous cognition."""

from __future__ import annotations

from typing import Any, Dict, List


def build_self_iteration_hypotheses(
    *,
    self_model_snapshot: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
    task_type_priors: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
    cognitive_assessment_memory: Dict[str, Any],
    self_iteration_trend_memory: Dict[str, Any],
    switch_self_regulation_memory: Dict[str, Any],
    post_task_effect_memory: Dict[str, Any],
    grounding_focus: Dict[str, Any],
) -> Dict[str, Any]:
    readiness = dict(self_model_snapshot.get("readiness") or {})
    self_gaps = _texts(self_model_snapshot.get("self_understanding_gaps"), 6)
    weak_channels = _texts(
        evidence_credibility_summary.get("weak_or_missing_channels"), 6
    )
    grounding_gaps = _texts(grounding_focus.get("grounding_gaps"), 6)
    top_priority_task_type = str(
        task_type_priors.get("top_priority_task_type") or ""
    ).strip()
    top_priority_score = _clamp01(task_type_priors.get("top_priority_score") or 0.0)
    readiness_score = _clamp01(
        readiness.get("self_iteration_readiness_score") or 0.0
    )
    reference_alignment_score = _clamp01(
        recent_reference_alignment.get("average_alignment_score") or 0.0
    )
    drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
    dominant_constraint = str(
        cognitive_assessment_memory.get("dominant_constraint")
        or (self_model_snapshot.get("current_state") or {}).get("dominant_constraint")
        or ""
    ).strip()
    why_not_improvement_now = str(
        cognitive_assessment_memory.get("why_not_improvement_now") or ""
    ).strip()
    why_not_improvement_evidence = [why_not_improvement_now] if why_not_improvement_now else []
    trend_state = str(self_iteration_trend_memory.get("trend_state") or "").strip().lower()
    dominant_trend_target = str(
        self_iteration_trend_memory.get("dominant_target") or ""
    ).strip()
    preferred_switch_bias = str(
        switch_self_regulation_memory.get("preferred_switch_bias") or ""
    ).strip()
    effect_direction = str(post_task_effect_memory.get("effect_direction") or "").strip()
    hypotheses: List[Dict[str, Any]] = []

    if grounding_gaps or reference_alignment_score < 0.65:
        hypotheses.append(
            {
                "target_domain": "grounding",
                "hypothesis": "repair evidence-to-agenda grounding before attempting aggressive self-iteration",
                "priority": _clamp01(
                    0.72
                    + min(len(grounding_gaps), 4) * 0.05
                    + max(0.0, 0.65 - reference_alignment_score) * 0.35
                ),
                "evidence": grounding_gaps[:4]
                + ([f"dominant_constraint:{dominant_constraint}"] if dominant_constraint else []),
                "suggested_task_types": ["observation", "review", "learning"],
            }
        )
    if self_gaps or readiness_score < 0.6:
        hypotheses.append(
            {
                "target_domain": "self_model",
                "hypothesis": "先扩展自我理解，再升级到不可逆的身体或策略变化",
                "priority": _clamp01(
                    0.66
                    + min(len(self_gaps), 4) * 0.05
                    + max(0.0, 0.6 - readiness_score) * 0.4
                ),
                "evidence": self_gaps[:4],
                "suggested_task_types": ["observation", "learning", "review"],
            }
        )
    if any(
        item in {"research", "external_research", "recent_learning", "learning_trace"}
        or "research" in item
        for item in weak_channels
    ):
        hypotheses.append(
            {
                "target_domain": "frontier_research",
                "hypothesis": "refresh external and learning evidence so self-iteration remains tied to current knowledge",
                "priority": _clamp01(0.52 + min(len(weak_channels), 4) * 0.04),
                "evidence": weak_channels[:4],
                "suggested_task_types": ["learning", "observation"],
            }
        )
    if drift_state in {"drifting", "correcting"}:
        hypotheses.append(
            {
                "target_domain": "task_selection",
                "hypothesis": "repair proposal selection logic and explanation quality before broadening autonomous action",
                "priority": _clamp01(
                    0.58
                    + (0.16 if drift_state == "drifting" else 0.08)
                    + (0.08 if top_priority_task_type in {"observation", "review"} else 0.0)
                ),
                "evidence": [
                    f"proposal_drift:{drift_state}",
                    f"posture_alignment_health:{proposal_drift_memory.get('posture_alignment_health') or 'unknown'}",
                    f"priority_basis_health:{proposal_drift_memory.get('priority_basis_health') or 'unknown'}",
                ]
                + (
                    [
                        "dominant_conflict:"
                        + str(proposal_drift_memory.get("dominant_posture_conflict_reason") or "").strip()
                    ]
                    if str(proposal_drift_memory.get("dominant_posture_conflict_reason") or "").strip()
                    else []
                ),
                "suggested_task_types": ["review", "observation"],
            }
        )
    if why_not_improvement_evidence:
        hypotheses.append(
            {
                "target_domain": "improvement_readiness",
                "hypothesis": "clarify why improvement is being deferred so future self-iteration can become more decisive",
                "priority": _clamp01(0.46 + min(len(why_not_improvement_evidence), 4) * 0.04),
                "evidence": why_not_improvement_evidence[:4],
                "suggested_task_types": ["review", "learning"],
            }
        )
    if dominant_trend_target:
        hypotheses.append(
            {
                "target_domain": dominant_trend_target,
                "hypothesis": "respect the recent self-iteration trend unless new evidence strongly justifies a domain switch",
                "priority": _clamp01(
                    0.44
                    + (0.1 if trend_state == "locked" else 0.04)
                    + (0.08 if dominant_trend_target == top_priority_task_type else 0.0)
                ),
                "evidence": [
                    f"trend_state:{trend_state or 'unknown'}",
                    f"dominant_target:{dominant_trend_target}",
                ],
                "suggested_task_types": [top_priority_task_type or "review", "observation"],
            }
        )
    if preferred_switch_bias in {"stay", "switch"}:
        hypotheses.append(
            {
                "target_domain": "switch_regulation",
                "hypothesis": "calibrate stay-versus-switch cadence based on recent outcome quality instead of changing direction reflexively",
                "priority": _clamp01(
                    0.4
                    + abs(
                        float(switch_self_regulation_memory.get("average_switch_quality") or 0.0)
                        - float(switch_self_regulation_memory.get("average_stay_quality") or 0.0)
                    )
                    * 0.3
                ),
                "evidence": [
                    f"preferred_switch_bias:{preferred_switch_bias}",
                    f"switch_effectiveness:{str(switch_self_regulation_memory.get('switch_effectiveness') or 'unknown')}",
                    f"stay_effectiveness:{str(switch_self_regulation_memory.get('stay_effectiveness') or 'unknown')}",
                ],
                "suggested_task_types": ["review", "observation"],
            }
        )
    if effect_direction in {"improving", "degrading", "mixed"}:
        hypotheses.append(
            {
                "target_domain": "task_effectiveness",
                "hypothesis": "prefer tasks that measurably improve reference alignment and cognitive alignment, not just plausible-looking tasks",
                "priority": _clamp01(
                    0.42
                    + (0.16 if effect_direction == "degrading" else 0.08 if effect_direction == "mixed" else 0.02)
                ),
                "evidence": [
                    f"post_task_effect:{effect_direction}",
                    f"dominant_target_effect:{str(post_task_effect_memory.get('dominant_target_effect') or 'unknown')}",
                ],
                "suggested_task_types": ["review", "observation", "learning"],
            }
        )

    normalized_hypotheses = [
        dict(item)
        for item in sorted(
            hypotheses,
            key=lambda row: (
                -float(row.get("priority") or 0.0),
                str(row.get("target_domain") or "").strip(),
            ),
        )
        if isinstance(item, dict) and str(item.get("hypothesis") or "").strip()
    ]
    if not normalized_hypotheses:
        return {
            "available": False,
            "hypotheses": [],
            "summary": "No explicit self-iteration hypotheses are available yet.",
        }
    top_hypothesis = normalized_hypotheses[0]
    return {
        "available": True,
        "dominant_hypothesis": str(top_hypothesis.get("hypothesis") or "").strip(),
        "top_target_domain": str(top_hypothesis.get("target_domain") or "").strip(),
        "hypotheses": normalized_hypotheses[:4],
        "summary": (
            "Current self-iteration should likely focus on "
            f"{str(top_hypothesis.get('target_domain') or 'unknown').strip() or 'unknown'}; "
            f"dominant hypothesis={str(top_hypothesis.get('hypothesis') or '').strip() or 'unknown'}; "
            f"current task-type hint={top_priority_task_type or 'unknown'} ({top_priority_score:.2f})."
        ),
    }


def _texts(value: Any, limit: int) -> List[str]:
    return [str(item).strip() for item in list(value or [])[:limit] if str(item).strip()]


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
