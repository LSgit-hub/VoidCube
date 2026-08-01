"""Pure generation-diagnostic projection for endogenous LM proposals."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_lm_task_generation_context_snapshot(
    *,
    evidence_packet: Dict[str, Any],
    cognition_charter: Dict[str, Any],
    role: str,
    max_candidates: int,
    status: str,
    proposal_count: int,
    cognitive_assessment: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    self_model_snapshot = dict(evidence_packet.get("self_model_snapshot") or {})
    evidence_credibility_summary = dict(
        evidence_packet.get("evidence_credibility_summary") or {}
    )
    self_iteration_hypotheses = dict(
        evidence_packet.get("self_iteration_hypotheses") or {}
    )
    meta_cognition_profile = dict(evidence_packet.get("meta_cognition_profile") or {})
    proposal_drift_memory = dict(evidence_packet.get("proposal_drift_memory") or {})
    cognitive_assessment_memory = dict(
        evidence_packet.get("cognitive_assessment_memory") or {}
    )
    self_iteration_trend_memory = dict(
        evidence_packet.get("self_iteration_trend_memory") or {}
    )
    switch_self_regulation_memory = dict(
        evidence_packet.get("switch_self_regulation_memory") or {}
    )
    post_task_effect_memory = dict(
        evidence_packet.get("post_task_effect_memory") or {}
    )
    recent_reference_alignment = dict(
        evidence_packet.get("recent_reference_alignment") or {}
    )
    cognitive_posture = dict(evidence_packet.get("cognitive_posture") or {})
    evidence_channels = _project_evidence_channels(
        evidence_packet.get("evidence_channels")
    )
    summary = (
        f"LM 认知状态={status}；"
        f"提案漂移={str(proposal_drift_memory.get('drift_state') or 'unknown').strip() or 'unknown'}。"
    )
    if error:
        summary += f" 异常={error}。"

    return {
        "status": status,
        "model_role": role,
        "max_candidates": max(0, int(max_candidates)),
        "proposal_count": max(0, int(proposal_count)),
        "cognitive_assessment": dict(cognitive_assessment or {}),
        "error": error,
        "charter": _project_charter(cognition_charter),
        "meta_cognition_profile": _project_meta_cognition_profile(
            meta_cognition_profile
        ),
        "self_iteration_hypotheses": _project_self_iteration_hypotheses(
            self_iteration_hypotheses
        ),
        "self_iteration_trend_memory": _project_self_iteration_trend_memory(
            self_iteration_trend_memory
        ),
        "switch_self_regulation_memory": _project_switch_self_regulation_memory(
            switch_self_regulation_memory
        ),
        "post_task_effect_memory": _project_post_task_effect_memory(
            post_task_effect_memory
        ),
        "cognitive_assessment_memory": _project_cognitive_assessment_memory(
            cognitive_assessment_memory
        ),
        "proposal_drift_memory": _project_proposal_drift_memory(
            proposal_drift_memory
        ),
        "recent_reference_alignment": _project_recent_reference_alignment(
            recent_reference_alignment
        ),
        "cognitive_posture": _project_cognitive_posture(cognitive_posture),
        "evidence_basis": _project_evidence_basis(
            self_model_snapshot=self_model_snapshot,
            evidence_credibility_summary=evidence_credibility_summary,
            evidence_channels=evidence_channels,
        ),
        "summary": summary,
    }


def _project_evidence_channels(value: Any) -> List[Dict[str, Any]]:
    source = dict(value or {}) if isinstance(value, dict) else {}
    return [
        {
            "channel": str(channel.get("channel") or "").strip(),
            "kind": str(channel.get("kind") or "").strip(),
            "confidence": round(_clamp01(channel.get("confidence") or 0.0), 4),
            "evidence_strength": str(
                channel.get("evidence_strength") or ""
            ).strip(),
            "item_count": max(0, int(channel.get("item_count") or 0)),
        }
        for channel in list(source.get("channels") or [])[:6]
        if isinstance(channel, dict) and str(channel.get("channel") or "").strip()
    ]


def _project_charter(cognition_charter: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "core_mission": str(cognition_charter.get("core_mission") or "").strip(),
        "self_model_principles": _texts(
            cognition_charter.get("self_model_principles"),
            limit=8,
        ),
        "evidence_policy": _texts(
            cognition_charter.get("evidence_policy"),
            limit=8,
        ),
        "task_generation_policy": _texts(
            cognition_charter.get("task_generation_policy"),
            limit=8,
        ),
        "self_iteration_guardrails": _texts(
            cognition_charter.get("self_iteration_guardrails"),
            limit=8,
        ),
    }


def _project_meta_cognition_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(profile.get("available")),
        "current_judgement": _text(profile.get("current_judgement")),
        "dominant_constraint": _text(profile.get("dominant_constraint")),
        "grounding_pressure": _text(profile.get("grounding_pressure")),
        "top_self_iteration_domain": _text(
            profile.get("top_self_iteration_domain")
        ),
        "top_self_iteration_hypothesis": _text(
            profile.get("top_self_iteration_hypothesis")
        ),
        "stay_or_switch_bias": _text(profile.get("stay_or_switch_bias")),
        "switch_bias_effectiveness": _text(
            profile.get("switch_bias_effectiveness")
        ),
        "recent_effect_direction": _text(profile.get("recent_effect_direction")),
        "dominant_failure_mode": _text(profile.get("dominant_failure_mode")),
        "governance_posture": _text(
            profile.get("governance_posture")
            or profile.get("recommended_task_posture")
        ),
        "priority_signals": _texts(profile.get("priority_signals"), limit=6),
    }


def _project_self_iteration_hypotheses(memory: Dict[str, Any]) -> Dict[str, Any]:
    rows = [
        dict(item)
        for item in list(memory.get("hypotheses") or [])
        if isinstance(item, dict) and _text(item.get("hypothesis"))
    ]
    dominant = rows[0] if rows else {}
    dominant_hypothesis = _text(
        memory.get("dominant_hypothesis") or dominant.get("hypothesis")
    )
    hypothesis_count = (
        _stored_count(memory, "hypothesis_count")
        if memory.get("hypothesis_count") is not None
        else len(rows)
    )
    return {
        "available": bool(memory.get("available")),
        "dominant_hypothesis": dominant_hypothesis,
        "top_target_domain": _text(
            memory.get("top_target_domain") or dominant.get("target_domain")
        ),
        "hypothesis_count": max(
            hypothesis_count,
            1 if dominant_hypothesis else 0,
        ),
        "top_priority": round(
            _clamp01(memory.get("top_priority") or dominant.get("priority") or 0.0),
            4,
        ),
        "suggested_task_types": _texts(
            memory.get("suggested_task_types")
            or dominant.get("suggested_task_types"),
            limit=3,
        ),
    }


def _project_self_iteration_trend_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(memory.get("available")),
        "dominant_target": _text(memory.get("dominant_target")),
        "trend_state": _text(memory.get("trend_state")),
        "target_stability": _text(memory.get("target_stability")),
        "dominant_hypothesis": _dominant_text(memory, ("dominant_hypothesis",)),
        "dominant_stay_or_switch": _dominant_text(
            memory,
            ("dominant_stay_or_switch", "stay_or_switch"),
        ),
        "dominant_switch_reason": _dominant_text(
            memory,
            ("dominant_switch_reason", "switch_reason"),
        ),
        "target_count": _signal_count(
            memory,
            ("target_count", "target_signal_count"),
        ),
        "hypothesis_count": _signal_count(
            memory,
            ("hypothesis_count", "hypothesis_signal_count"),
        ),
        "stay_or_switch_count": _signal_count(
            memory,
            ("stay_or_switch_count", "stay_or_switch_signal_count"),
        ),
        "switch_reason_count": _signal_count(
            memory,
            ("switch_reason_count", "switch_reason_signal_count"),
        ),
    }


def _project_switch_self_regulation_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(memory.get("available")),
        "preferred_switch_bias": _text(memory.get("preferred_switch_bias")),
        "switch_effectiveness": _text(memory.get("switch_effectiveness")),
        "stay_effectiveness": _text(memory.get("stay_effectiveness")),
        "average_switch_quality": round(
            _clamp01(memory.get("average_switch_quality") or 0.0),
            4,
        ),
        "average_stay_quality": round(
            _clamp01(memory.get("average_stay_quality") or 0.0),
            4,
        ),
    }


def _project_post_task_effect_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(memory.get("available")),
        "effect_direction": _text(memory.get("effect_direction")),
        "average_quality_score": round(
            _clamp01(memory.get("average_quality_score") or 0.0),
            4,
        ),
        "average_cognitive_alignment_score": round(
            _clamp01(memory.get("average_cognitive_alignment_score") or 0.0),
            4,
        ),
        "average_reference_alignment_score": round(
            _clamp01(memory.get("average_reference_alignment_score") or 0.0),
            4,
        ),
        "dominant_target_effect": _text(memory.get("dominant_target_effect")),
    }


def _project_cognitive_assessment_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(memory.get("available")),
        "dominant_constraint": _text(memory.get("dominant_constraint")),
        "current_judgement": _dominant_text(memory, ("current_judgement",)),
        "why_not_improvement_now": _dominant_text(
            memory,
            ("why_not_improvement_now",),
        ),
        "self_iteration_target": _dominant_text(
            memory,
            ("self_iteration_target",),
        ),
        "self_iteration_hypothesis": _dominant_text(
            memory,
            ("self_iteration_hypothesis",),
        ),
        "current_judgement_count": _signal_count(
            memory,
            ("current_judgement_count",),
        ),
        "why_not_improvement_now_count": _signal_count(
            memory,
            ("why_not_improvement_now_count",),
        ),
        "self_iteration_target_count": _signal_count(
            memory,
            ("self_iteration_target_count", "target_count"),
        ),
        "self_iteration_hypothesis_count": _signal_count(
            memory,
            ("self_iteration_hypothesis_count", "hypothesis_count"),
        ),
    }


def _project_proposal_drift_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(memory.get("available")),
        "average_score": round(_clamp01(memory.get("average_score") or 0.0), 4),
        "drift_state": _text(memory.get("drift_state")),
        "quality_counts": dict(memory.get("quality_counts") or {}),
        "posture_alignment_signal_count": _nonnegative_int(
            memory.get("posture_alignment_signal_count")
        ),
        "priority_basis_signal_count": _nonnegative_int(
            memory.get("priority_basis_signal_count")
        ),
        "missing_posture_alignment_count": _nonnegative_int(
            memory.get("missing_posture_alignment_count")
        ),
        "missing_priority_basis_count": _nonnegative_int(
            memory.get("missing_priority_basis_count")
        ),
        "posture_alignment_health": _text(memory.get("posture_alignment_health")),
        "priority_basis_health": _text(memory.get("priority_basis_health")),
        "dominant_posture_conflict_reason": _text(
            memory.get("dominant_posture_conflict_reason")
        ),
    }


def _project_recent_reference_alignment(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": bool(memory.get("available")),
        "average_alignment_score": round(
            _clamp01(memory.get("average_alignment_score") or 0.0),
            4,
        ),
        "weak_or_partial_count": _nonnegative_int(
            memory.get("weak_or_partial_count")
        ),
        "entry_count": _nonnegative_int(memory.get("entry_count")),
        "primary_missing_evidence_node": _text(
            memory.get("primary_missing_evidence_node")
        ),
        "primary_missing_agenda_node": _text(
            memory.get("primary_missing_agenda_node")
        ),
        "missing_evidence_node_count": _nonnegative_int(
            memory.get("missing_evidence_node_count")
        ),
        "missing_agenda_node_count": _nonnegative_int(
            memory.get("missing_agenda_node_count")
        ),
    }


def _project_cognitive_posture(posture: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _text(posture.get("name")),
        "selection_mode": _text(posture.get("selection_mode")),
        "selection_reason": _text(posture.get("selection_reason")),
        "summary": _text(posture.get("summary")),
        "observation_multiplier": round(
            _clamp01(posture.get("observation_multiplier") or 0.0),
            4,
        ),
        "throttle_multiplier": round(
            _clamp01(posture.get("throttle_multiplier") or 0.0),
            4,
        ),
        "truthfulness_multiplier": round(
            _clamp01(posture.get("truthfulness_multiplier") or 0.0),
            4,
        ),
        "learning_suppression_multiplier": round(
            _clamp01(posture.get("learning_suppression_multiplier") or 0.0),
            4,
        ),
    }


def _project_evidence_basis(
    *,
    self_model_snapshot: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
    evidence_channels: List[Dict[str, Any]],
) -> Dict[str, Any]:
    readiness = dict(self_model_snapshot.get("readiness") or {})
    return {
        "self_iteration_readiness_score": round(
            _clamp01(readiness.get("self_iteration_readiness_score") or 0.0),
            4,
        ),
        "autonomy_readiness": round(
            _clamp01(readiness.get("autonomy_readiness") or 0.0),
            4,
        ),
        "self_understanding_gaps": _texts(
            self_model_snapshot.get("self_understanding_gaps"),
            limit=6,
        ),
        "high_credibility_channels": _texts(
            evidence_credibility_summary.get("high_credibility_channels"),
            limit=5,
        ),
        "weak_or_missing_channels": _texts(
            evidence_credibility_summary.get("weak_or_missing_channels"),
            limit=5,
        ),
        "reference_alignment_score": round(
            _clamp01(
                evidence_credibility_summary.get("reference_alignment_score") or 0.0
            ),
            4,
        ),
        "evidence_channels": evidence_channels,
    }


def _texts(values: Any, *, limit: int) -> List[str]:
    raw_values = [values] if isinstance(values, str) else list(values or [])
    return [
        _text(item)
        for item in raw_values[:limit]
        if _text(item)
    ]


def _dominant_text(mapping: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _signal_count(mapping: Dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            continue
    return 0


def _stored_count(mapping: Dict[str, Any], key: str) -> int:
    return _nonnegative_int(mapping.get(key))


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))
