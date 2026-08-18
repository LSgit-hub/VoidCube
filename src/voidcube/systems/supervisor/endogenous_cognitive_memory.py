"""Pure projections for endogenous cognitive history memory."""

from __future__ import annotations

from typing import Any, Dict, List

from .endogenous_proposals import normalize_lm_cognitive_assessment


def build_cognitive_assessment_memory(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = _history_outcomes(drive_context)
    current_judgement_counts: Dict[str, int] = {}
    dominant_constraint_counts: Dict[str, int] = {}
    why_not_improvement_counts: Dict[str, int] = {}
    self_iteration_target_counts: Dict[str, int] = {}
    self_iteration_hypothesis_counts: Dict[str, int] = {}
    gap_counts: Dict[str, int] = {}
    entry_count = 0

    for outcome in outcomes[:12]:
        normalized = _normalized_assessment(outcome)
        if not normalized:
            continue
        current_judgement = str(normalized.get("current_judgement") or "").strip()
        dominant_constraint = str(normalized.get("dominant_constraint") or "").strip()
        why_not_improvement_now = _text_items(
            normalized.get("why_not_improvement_now"), limit=3
        )
        self_iteration_target = str(
            normalized.get("self_iteration_target") or ""
        ).strip()
        self_iteration_hypothesis = str(
            normalized.get("self_iteration_hypothesis") or ""
        ).strip()
        primary_grounding_gaps = _text_items(
            normalized.get("primary_grounding_gaps"), limit=3
        )
        _increment(current_judgement_counts, current_judgement)
        _increment(dominant_constraint_counts, dominant_constraint)
        _increment(self_iteration_target_counts, self_iteration_target)
        _increment(self_iteration_hypothesis_counts, self_iteration_hypothesis)
        for item in why_not_improvement_now:
            _increment(why_not_improvement_counts, item)
        for item in primary_grounding_gaps:
            _increment(gap_counts, item)
        entry_count += 1
        if entry_count >= 4:
            break

    if not entry_count:
        return {
            "available": False,
            "summary": "当前还没有可用的近期 LM 认知评估记忆。",
        }

    current_judgement = _dominant(current_judgement_counts)
    why_not_improvement_now = _dominant(why_not_improvement_counts)
    self_iteration_target = _dominant(self_iteration_target_counts)
    self_iteration_hypothesis = _dominant(self_iteration_hypothesis_counts)
    dominant_constraint = _dominant(dominant_constraint_counts)
    primary_grounding_gap = _dominant(gap_counts)
    return {
        "available": True,
        "dominant_constraint": dominant_constraint or None,
        "current_judgement": current_judgement or None,
        "current_judgement_count": len(current_judgement_counts),
        "why_not_improvement_now": why_not_improvement_now or None,
        "why_not_improvement_now_count": len(why_not_improvement_counts),
        "self_iteration_target": self_iteration_target or None,
        "self_iteration_target_count": len(self_iteration_target_counts),
        "self_iteration_hypothesis": self_iteration_hypothesis or None,
        "self_iteration_hypothesis_count": len(self_iteration_hypothesis_counts),
        "primary_grounding_gap": primary_grounding_gap or None,
        "grounding_gap_count": len(gap_counts),
        "entry_count": entry_count,
        "summary": (
            "近期 LM 认知评估反复指向 "
            f"{current_judgement or '当前状态仍未稳定'}；"
            f"主约束={dominant_constraint or '未知'}。"
        ),
    }


def build_self_iteration_trend_memory(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = _history_outcomes(drive_context)
    target_counts: Dict[str, int] = {}
    hypothesis_counts: Dict[str, int] = {}
    stay_switch_counts: Dict[str, int] = {}
    switch_reason_counts: Dict[str, int] = {}
    ordered_targets: List[str] = []
    entry_count = 0

    for outcome in outcomes[:16]:
        normalized = _normalized_assessment(outcome)
        if not normalized:
            continue
        target = str(normalized.get("self_iteration_target") or "").strip()
        hypothesis = str(normalized.get("self_iteration_hypothesis") or "").strip()
        stay_or_switch = str(normalized.get("stay_or_switch") or "").strip().lower()
        switch_reason = str(normalized.get("switch_reason") or "").strip()
        if not target and not hypothesis:
            continue
        _increment(target_counts, target)
        _increment(hypothesis_counts, hypothesis)
        if target:
            ordered_targets.append(target)
        if stay_or_switch in {"stay", "switch"}:
            _increment(stay_switch_counts, stay_or_switch)
        _increment(switch_reason_counts, switch_reason)
        entry_count += 1
        if entry_count >= 6:
            break

    if not entry_count:
        return {
            "available": False,
            "summary": "No long-horizon self-iteration trend memory is available yet.",
        }

    ranked_targets = _ranked_keys(target_counts, limit=4)
    ranked_hypotheses = _ranked_keys(hypothesis_counts, limit=4)
    ranked_stay_or_switch = _ranked_keys(stay_switch_counts, limit=2)
    ranked_switch_reasons = _ranked_keys(switch_reason_counts, limit=4)
    dominant_target = ranked_targets[0] if ranked_targets else ""
    recent_targets = [target for target in ordered_targets[:4] if target]
    unique_recent_targets = set(recent_targets)
    target_stability = "mixed"
    if len(unique_recent_targets) <= 1 and dominant_target:
        target_stability = "stable"
    elif len(unique_recent_targets) >= 3:
        target_stability = "volatile"
    trend_state = "exploring"
    dominant_count = target_counts.get(dominant_target, 0) if dominant_target else 0
    if dominant_target and dominant_count >= 3 and target_stability == "stable":
        trend_state = "locked"
    elif dominant_target and dominant_count >= 2:
        trend_state = "consolidating"
    elif target_stability == "volatile":
        trend_state = "searching"
    return {
        "available": True,
        "dominant_target": dominant_target or None,
        "dominant_hypothesis": ranked_hypotheses[0] if ranked_hypotheses else None,
        "trend_state": trend_state,
        "target_stability": target_stability,
        "target_count": len(target_counts),
        "hypothesis_count": len(hypothesis_counts),
        "dominant_stay_or_switch": (
            ranked_stay_or_switch[0] if ranked_stay_or_switch else None
        ),
        "stay_or_switch_count": len(stay_switch_counts),
        "dominant_switch_reason": (
            ranked_switch_reasons[0] if ranked_switch_reasons else None
        ),
        "switch_reason_count": len(switch_reason_counts),
        "entry_count": entry_count,
        "summary": (
            "Recent self-iteration reasoning trends toward "
            f"{dominant_target or 'unknown'}; trend_state={trend_state}; "
            f"target_stability={target_stability}."
        ),
    }


def build_switch_self_regulation_memory(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = _history_outcomes(drive_context)
    switch_quality_scores: List[float] = []
    stay_quality_scores: List[float] = []
    switch_alignment_scores: List[float] = []
    stay_alignment_scores: List[float] = []
    switch_reference_scores: List[float] = []
    stay_reference_scores: List[float] = []
    switch_result_statuses: List[str] = []
    stay_result_statuses: List[str] = []

    for outcome in outcomes[:16]:
        normalized = _normalized_assessment(outcome)
        if not normalized:
            continue
        decision = str(normalized.get("stay_or_switch") or "").strip().lower()
        if decision not in {"stay", "switch"}:
            continue
        quality_score = _clamp01(float(outcome.get("quality_score") or 0.0))
        alignment_score = _alignment_score(outcome)
        reference_score = _reference_score(outcome)
        result_status = str(outcome.get("result_status") or "").strip().lower()
        if decision == "switch":
            switch_quality_scores.append(quality_score)
            switch_alignment_scores.append(alignment_score)
            switch_reference_scores.append(reference_score)
            if result_status:
                switch_result_statuses.append(result_status)
        else:
            stay_quality_scores.append(quality_score)
            stay_alignment_scores.append(alignment_score)
            stay_reference_scores.append(reference_score)
            if result_status:
                stay_result_statuses.append(result_status)

    if not switch_quality_scores and not stay_quality_scores:
        return {
            "available": False,
            "summary": "No switch self-regulation memory is available yet.",
        }

    average_switch_quality = _average(switch_quality_scores)
    average_stay_quality = _average(stay_quality_scores)
    average_switch_alignment = _average(switch_alignment_scores)
    average_stay_alignment = _average(stay_alignment_scores)
    average_switch_reference = _average(switch_reference_scores)
    average_stay_reference = _average(stay_reference_scores)
    switch_effectiveness = "unknown"
    stay_effectiveness = "unknown"
    if switch_quality_scores:
        switch_effectiveness = "strong" if average_switch_quality >= 0.65 else "weak"
    if stay_quality_scores:
        stay_effectiveness = "strong" if average_stay_quality >= 0.65 else "weak"
    preferred_switch_bias = "balanced"
    if switch_quality_scores and stay_quality_scores:
        if average_switch_quality >= average_stay_quality + 0.12:
            preferred_switch_bias = "switch"
        elif average_stay_quality >= average_switch_quality + 0.12:
            preferred_switch_bias = "stay"
    elif switch_quality_scores:
        preferred_switch_bias = "switch"
    elif stay_quality_scores:
        preferred_switch_bias = "stay"
    return {
        "available": True,
        "preferred_switch_bias": preferred_switch_bias,
        "switch_effectiveness": switch_effectiveness,
        "stay_effectiveness": stay_effectiveness,
        "average_switch_quality": round(average_switch_quality, 4),
        "average_stay_quality": round(average_stay_quality, 4),
        "average_switch_alignment": round(average_switch_alignment, 4),
        "average_stay_alignment": round(average_stay_alignment, 4),
        "average_switch_reference": round(average_switch_reference, 4),
        "average_stay_reference": round(average_stay_reference, 4),
        "switch_result_statuses": switch_result_statuses[:6],
        "stay_result_statuses": stay_result_statuses[:6],
        "summary": (
            "Recent stay/switch outcomes suggest "
            f"preferred_bias={preferred_switch_bias}; "
            f"switch_quality={average_switch_quality:.2f}; "
            f"stay_quality={average_stay_quality:.2f}."
        ),
    }


def build_post_task_effect_memory(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = _history_outcomes(drive_context)
    quality_scores: List[float] = []
    cognitive_scores: List[float] = []
    reference_scores: List[float] = []
    target_effect_counts: Dict[str, int] = {}
    entry_count = 0

    for outcome in outcomes[:16]:
        event_type = str(outcome.get("event_type") or "").strip().lower()
        if event_type in {"", "planned"}:
            continue
        normalized = _normalized_assessment(outcome)
        quality_score = _clamp01(float(outcome.get("quality_score") or 0.0))
        cognitive_score = _alignment_score(outcome)
        reference_score = _reference_score(outcome)
        target = str(normalized.get("self_iteration_target") or "").strip()
        if not quality_score and not cognitive_score and not reference_score:
            continue
        quality_scores.append(quality_score)
        cognitive_scores.append(cognitive_score)
        reference_scores.append(reference_score)
        if target:
            effect_label = (
                "helped"
                if quality_score >= 0.65 and cognitive_score >= 0.55
                else "unclear"
            )
            if quality_score < 0.4 or reference_score < 0.4:
                effect_label = "hurt"
            effect_key = f"{target}:{effect_label}"
            _increment(target_effect_counts, effect_key)
        entry_count += 1
        if entry_count >= 6:
            break

    if not entry_count:
        return {
            "available": False,
            "summary": "当前还没有可用的任务后效记忆。",
        }

    average_quality_score = _average(quality_scores)
    average_cognitive_alignment_score = _average(cognitive_scores)
    average_reference_alignment_score = _average(reference_scores)
    effect_direction = "mixed"
    if (
        average_quality_score >= 0.65
        and average_cognitive_alignment_score >= 0.55
        and average_reference_alignment_score >= 0.55
    ):
        effect_direction = "improving"
    elif (
        average_quality_score < 0.4
        or average_cognitive_alignment_score < 0.4
        or average_reference_alignment_score < 0.4
    ):
        effect_direction = "degrading"
    dominant_target_effect = ""
    if target_effect_counts:
        dominant_target_effect = sorted(
            target_effect_counts.items(),
            key=lambda pair: (-pair[1], pair[0]),
        )[0][0]
    return {
        "available": True,
        "effect_direction": effect_direction,
        "average_quality_score": round(average_quality_score, 4),
        "average_cognitive_alignment_score": round(
            average_cognitive_alignment_score,
            4,
        ),
        "average_reference_alignment_score": round(
            average_reference_alignment_score,
            4,
        ),
        "dominant_target_effect": dominant_target_effect or None,
        "entry_count": entry_count,
        "summary": (
            "Recent post-task effects appear "
            f"{effect_direction}; avg_quality={average_quality_score:.2f}; "
            f"avg_cognitive_alignment={average_cognitive_alignment_score:.2f}; "
            f"avg_reference_alignment={average_reference_alignment_score:.2f}."
        ),
    }


def _history_outcomes(drive_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    drive_history = dict(drive_context.get("drive_history") or {})
    return [
        dict(item)
        for item in list(drive_history.get("outcomes") or [])
        if isinstance(item, dict)
    ]


def _normalized_assessment(outcome: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(outcome.get("metadata") or {})
    evidence = dict(outcome.get("evidence") or {})
    assessment = outcome.get("llm_cognitive_assessment")
    if not isinstance(assessment, dict):
        assessment = metadata.get("llm_cognitive_assessment")
    if not isinstance(assessment, dict):
        assessment = evidence.get("llm_cognitive_assessment")
    return normalize_lm_cognitive_assessment(assessment)


def _alignment_score(outcome: Dict[str, Any]) -> float:
    alignment = _nested_outcome_value(outcome, "cognitive_alignment")
    return _clamp01((alignment or {}).get("score") or 0.0)


def _reference_score(outcome: Dict[str, Any]) -> float:
    alignment = _nested_outcome_value(outcome, "reference_alignment")
    return _clamp01((alignment or {}).get("alignment_score") or 0.0)


def _nested_outcome_value(
    outcome: Dict[str, Any],
    key: str,
) -> Dict[str, Any]:
    metadata = dict(outcome.get("metadata") or {})
    evidence = dict(outcome.get("evidence") or {})
    value = outcome.get(key)
    if not isinstance(value, dict):
        value = metadata.get(key)
    if not isinstance(value, dict):
        value = evidence.get(key)
    return value if isinstance(value, dict) else {}


def _text_items(value: Any, *, limit: int) -> List[str]:
    return [
        str(item).strip()
        for item in list(value or [])[:limit]
        if str(item).strip()
    ]


def _increment(counts: Dict[str, int], value: str) -> None:
    if value:
        counts[value] = counts.get(value, 0) + 1


def _dominant(counts: Dict[str, int]) -> str:
    return _ranked_keys(counts, limit=1)[0] if counts else ""


def _ranked_keys(counts: Dict[str, int], *, limit: int) -> List[str]:
    return [
        item
        for item, _count in sorted(
            counts.items(),
            key=lambda pair: (-pair[1], pair[0]),
        )[:limit]
    ]


def _average(values: List[float]) -> float:
    if not values:
        return 0.0
    return _clamp01(sum(values) / len(values))


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
