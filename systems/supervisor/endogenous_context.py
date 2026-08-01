"""Pure context-layer projection for endogenous LM task generation."""

from __future__ import annotations

from typing import Any, Dict, List


def build_lm_context_layers(
    *,
    cognition_charter: Dict[str, Any],
    cognitive_posture: Dict[str, Any],
    grounding_focus: Dict[str, Any],
    self_iteration_hypotheses: Dict[str, Any],
    meta_cognition_profile: Dict[str, Any],
    self_model_snapshot: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
    task_type_priors: Dict[str, Any],
    cognitive_assessment_memory: Dict[str, Any],
    self_iteration_trend_memory: Dict[str, Any],
    switch_self_regulation_memory: Dict[str, Any],
    post_task_effect_memory: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    api_b_judgement_snapshot: Dict[str, Any],
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    evidence_channels: Dict[str, Any],
    recent_learning_titles: List[str],
) -> Dict[str, Dict[str, Any]]:
    layering_policy = _resolve_cognitive_context_layering_policy(
        cognition_charter
    )

    decision_core = {
        "current_judgement": str(
            meta_cognition_profile.get("current_judgement")
            or cognitive_assessment_memory.get("current_judgement")
            or ""
        ).strip(),
        "dominant_constraint": str(
            meta_cognition_profile.get("dominant_constraint")
            or cognitive_assessment_memory.get("dominant_constraint")
            or ""
        ).strip(),
        "grounding_pressure": str(
            meta_cognition_profile.get("grounding_pressure") or ""
        ).strip(),
        "governance_posture": str(
            meta_cognition_profile.get("governance_posture")
            or meta_cognition_profile.get("recommended_task_posture")
            or task_type_priors.get("top_priority_task_type")
            or ""
        ).strip(),
        "secondary_task_shape_hint": str(
            task_type_priors.get("top_priority_task_type")
            or ""
        ).strip(),
        "secondary_task_shape_score": round(
            _clamp01(task_type_priors.get("top_priority_score") or 0.0),
            4,
        ),
        "top_self_iteration_domain": str(
            meta_cognition_profile.get("top_self_iteration_domain")
            or self_iteration_hypotheses.get("top_target_domain")
            or ""
        ).strip(),
        "top_self_iteration_hypothesis": str(
            meta_cognition_profile.get("top_self_iteration_hypothesis")
            or self_iteration_hypotheses.get("dominant_hypothesis")
            or ""
        ).strip(),
        "primary_evidence_nodes": [
            str(item).strip()
            for item in list(grounding_focus.get("primary_evidence_nodes") or [])[:3]
            if str(item).strip()
        ],
        "primary_agenda_nodes": [
            str(item).strip()
            for item in list(grounding_focus.get("primary_agenda_nodes") or [])[:3]
            if str(item).strip()
        ],
        "api_b_judgement_summary": str(api_b_judgement_snapshot.get("summary") or "").strip(),
        "cognitive_posture": {
            "name": str(cognitive_posture.get("name") or "").strip(),
            "selection_reason": str(
                cognitive_posture.get("selection_reason") or ""
            ).strip(),
        },
        "summary": (
            "判断核心："
            f"当前判断={str(meta_cognition_profile.get('current_judgement') or 'unknown').strip() or 'unknown'}；"
            f"主约束={str(meta_cognition_profile.get('dominant_constraint') or 'unknown').strip() or 'unknown'}；"
            f"治理姿态={str(meta_cognition_profile.get('governance_posture') or meta_cognition_profile.get('recommended_task_posture') or 'unknown').strip() or 'unknown'}；"
            f"任务形态提示={str(task_type_priors.get('top_priority_task_type') or 'unknown').strip() or 'unknown'}；"
            f"首要自我迭代域={str(meta_cognition_profile.get('top_self_iteration_domain') or 'unknown').strip() or 'unknown'}。"
        ),
    }

    readiness = dict(self_model_snapshot.get("readiness") or {})
    supporting_detail = {
        "grounding_gaps": [
            str(item).strip()
            for item in list(grounding_focus.get("grounding_gaps") or [])[:4]
            if str(item).strip()
        ],
        "contradictory_topics": [
            str(item).strip()
            for item in list(grounding_focus.get("contradictory_topics") or [])[:3]
            if str(item).strip()
        ],
        "weak_or_missing_channels": [
            str(item).strip()
            for item in list(
                evidence_credibility_summary.get("weak_or_missing_channels") or []
            )[:4]
            if str(item).strip()
        ],
        "self_understanding_gaps": [
            str(item).strip()
            for item in list(self_model_snapshot.get("self_understanding_gaps") or [])[:4]
            if str(item).strip()
        ],
        "why_not_improvement_now": [
            item
            for item in [
                str(cognitive_assessment_memory.get("why_not_improvement_now") or "").strip()
            ]
            if item
        ][:4],
        "trend_state": str(self_iteration_trend_memory.get("trend_state") or "").strip(),
        "stay_or_switch_bias": str(
            meta_cognition_profile.get("stay_or_switch_bias")
            or switch_self_regulation_memory.get("preferred_switch_bias")
            or ""
        ).strip(),
        "recent_effect_direction": str(
            meta_cognition_profile.get("recent_effect_direction")
            or post_task_effect_memory.get("effect_direction")
            or ""
        ).strip(),
        "reference_alignment_score": round(
            _clamp01(
                recent_reference_alignment.get("average_alignment_score") or 0.0
            ),
            4,
        ),
        "self_iteration_readiness_score": round(
            _clamp01(readiness.get("self_iteration_readiness_score") or 0.0),
            4,
        ),
        "summary": (
            "Supporting detail: "
            f"grounding_gaps={len(list(grounding_focus.get('grounding_gaps') or []))}; "
            f"weak_channels={len(list(evidence_credibility_summary.get('weak_or_missing_channels') or []))}; "
            f"trend_state={str(self_iteration_trend_memory.get('trend_state') or 'unknown').strip() or 'unknown'}."
        ),
    }

    channel_rows = [
        {
            "channel": str(item.get("channel") or "").strip(),
            "evidence_strength": str(item.get("evidence_strength") or "").strip(),
            "item_count": max(0, int(item.get("item_count") or 0)),
        }
        for item in list(evidence_channels.get("channels") or [])[:4]
        if isinstance(item, dict) and str(item.get("channel") or "").strip()
    ]
    long_tail_context = {
        "recent_learning_titles": [
            str(item).strip()
            for item in list(recent_learning_titles or [])[:5]
            if str(item).strip()
        ],
        "recent_learning_evidence": [
            {
                "title": str(item.get("title") or "").strip(),
                "quality_score": item.get("quality_score"),
            }
            for item in list(recent_learning_evidence or [])[:2]
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ],
        "external_research_titles": [
            str(item.get("title") or "").strip()
            for item in list(external_research_evidence or [])[:3]
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ],
        "evidence_channels": channel_rows,
        "summary": (
            "Long-tail context: "
            f"learning_titles={len(list(recent_learning_titles or []))}; "
            f"research_entries={len(list(external_research_evidence or []))}; "
            f"channels={len(channel_rows)}."
        ),
    }
    layer_sources = {
        "current_judgement": decision_core.get("current_judgement"),
        "dominant_constraint": decision_core.get("dominant_constraint"),
        "grounding_pressure": decision_core.get("grounding_pressure"),
        "governance_posture": decision_core.get("governance_posture"),
        "secondary_task_shape_hint": decision_core.get("secondary_task_shape_hint"),
        "secondary_task_shape_score": decision_core.get("secondary_task_shape_score"),
        "top_self_iteration_domain": decision_core.get("top_self_iteration_domain"),
        "top_self_iteration_hypothesis": decision_core.get("top_self_iteration_hypothesis"),
        "primary_evidence_nodes": decision_core.get("primary_evidence_nodes"),
        "primary_agenda_nodes": decision_core.get("primary_agenda_nodes"),
        "api_b_judgement_summary": decision_core.get("api_b_judgement_summary"),
        "cognitive_posture": decision_core.get("cognitive_posture"),
        "decision_summary": decision_core.get("summary"),
        "grounding_gaps": supporting_detail.get("grounding_gaps"),
        "contradictory_topics": supporting_detail.get("contradictory_topics"),
        "weak_or_missing_channels": supporting_detail.get("weak_or_missing_channels"),
        "self_understanding_gaps": supporting_detail.get("self_understanding_gaps"),
        "why_not_improvement_now": supporting_detail.get("why_not_improvement_now"),
        "trend_state": supporting_detail.get("trend_state"),
        "stay_or_switch_bias": supporting_detail.get("stay_or_switch_bias"),
        "recent_effect_direction": supporting_detail.get("recent_effect_direction"),
        "reference_alignment_score": supporting_detail.get("reference_alignment_score"),
        "self_iteration_readiness_score": supporting_detail.get("self_iteration_readiness_score"),
        "supporting_summary": supporting_detail.get("summary"),
        "recent_learning_titles": long_tail_context.get("recent_learning_titles"),
        "recent_learning_evidence": long_tail_context.get("recent_learning_evidence"),
        "external_research_titles": long_tail_context.get("external_research_titles"),
        "evidence_channels": long_tail_context.get("evidence_channels"),
        "long_tail_summary": long_tail_context.get("summary"),
    }
    return {
        "decision_core": _select_context_layer_fields(
            layer_sources,
            layering_policy.get("decision_core_fields") or [],
            summary_alias="decision_summary",
            summary_output_key="summary",
        ),
        "supporting_detail": _select_context_layer_fields(
            layer_sources,
            layering_policy.get("supporting_detail_fields") or [],
            summary_alias="supporting_summary",
            summary_output_key="summary",
        ),
        "long_tail_context": _select_context_layer_fields(
            layer_sources,
            layering_policy.get("long_tail_context_fields") or [],
            summary_alias="long_tail_summary",
            summary_output_key="summary",
        ),
    }


def reference_alignment_gap_labels(
    recent_reference_alignment: Dict[str, Any],
) -> List[str]:
    labels: List[str] = []
    primary_evidence = str(
        recent_reference_alignment.get("primary_missing_evidence_node") or ""
    ).strip()
    primary_agenda = str(
        recent_reference_alignment.get("primary_missing_agenda_node") or ""
    ).strip()
    if primary_evidence:
        labels.append(f"missing_evidence:{primary_evidence}")
    if primary_agenda:
        labels.append(f"missing_agenda:{primary_agenda}")
    for entry in list(recent_reference_alignment.get("recent_entries") or [])[:3]:
        if not isinstance(entry, dict):
            continue
        for node in list(entry.get("missing_evidence_nodes") or [])[:2]:
            value = str(node).strip()
            label = f"missing_evidence:{value}" if value else ""
            if label and label not in labels:
                labels.append(label)
        for node in list(entry.get("missing_agenda_nodes") or [])[:2]:
            value = str(node).strip()
            label = f"missing_agenda:{value}" if value else ""
            if label and label not in labels:
                labels.append(label)
    return labels


def _resolve_cognitive_context_layering_policy(
    cognition_charter: Dict[str, Any],
) -> Dict[str, List[str]]:
    default_policy = {
        "decision_core_fields": [
            "current_judgement",
            "dominant_constraint",
            "grounding_pressure",
            "governance_posture",
            "secondary_task_shape_hint",
            "secondary_task_shape_score",
            "top_self_iteration_domain",
            "top_self_iteration_hypothesis",
            "primary_evidence_nodes",
            "primary_agenda_nodes",
            "api_b_judgement_summary",
            "cognitive_posture",
            "decision_summary",
        ],
        "supporting_detail_fields": [
            "grounding_gaps",
            "contradictory_topics",
            "weak_or_missing_channels",
            "self_understanding_gaps",
            "why_not_improvement_now",
            "trend_state",
            "stay_or_switch_bias",
            "recent_effect_direction",
            "reference_alignment_score",
            "self_iteration_readiness_score",
            "supporting_summary",
        ],
        "long_tail_context_fields": [
            "recent_learning_titles",
            "recent_learning_evidence",
            "external_research_titles",
            "evidence_channels",
            "long_tail_summary",
        ],
    }
    raw_policy = dict(cognition_charter.get("context_layering_policy") or {})
    resolved: Dict[str, List[str]] = {}
    for key, fallback in default_policy.items():
        items = [
            str(item).strip()
            for item in list(raw_policy.get(key) or [])
            if str(item).strip()
        ]
        resolved[key] = items or list(fallback)
    return resolved


def _select_context_layer_fields(
    layer_sources: Dict[str, Any],
    field_names: List[str],
    *,
    summary_alias: str,
    summary_output_key: str,
) -> Dict[str, Any]:
    layer: Dict[str, Any] = {}
    for field_name in field_names:
        source_key = str(field_name or "").strip()
        if not source_key:
            continue
        if source_key == summary_alias:
            value = layer_sources.get(summary_alias)
            if value not in ("", [], {}, None):
                layer[summary_output_key] = value
            continue
        if source_key not in layer_sources:
            continue
        value = layer_sources.get(source_key)
        if value in ("", None):
            continue
        layer[source_key] = value
    return layer


def _clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))
