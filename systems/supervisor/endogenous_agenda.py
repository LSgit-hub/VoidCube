"""Pure agenda graph projection for endogenous drive deliberation."""

from __future__ import annotations

from typing import Any, Dict, List

from systems.supervisor.endogenous_proposals import task_type_for_candidate_kind


_REVIEW_API_B_JUDGEMENT_NEED = "review_api_b_judgement"


def build_agenda_graph(
    *,
    deliberation_dict: Dict[str, Any],
    evidence_graph: Dict[str, Any],
) -> Dict[str, Any]:
    policy = dict(deliberation_dict.get("adaptive_policy") or {})
    needs = _dict_rows(deliberation_dict.get("needs"))
    intents = _dict_rows(deliberation_dict.get("intents"))
    signals = _dict_rows(deliberation_dict.get("signals"))
    evidence_nodes = _dict_rows(evidence_graph.get("nodes"))
    focus = str(policy.get("preferred_focus") or "").strip() or "observation"

    current_topics = [
        {
            "topic": str(node.get("topic") or "").strip(),
            "priority": round(
                _clamp01(
                    0.28
                    + max(0, float(node.get("net_signal") or 0.0)) * 0.18
                    + float(node.get("avg_confidence") or 0.0) * 0.42
                ),
                4,
            ),
            "status": (
                "supported"
                if float(node.get("net_signal") or 0.0) > 0
                else "contested"
                if float(node.get("contradict_count") or 0.0) > 0
                else "emerging"
            ),
        }
        for node in evidence_nodes[:8]
        if str(node.get("topic") or "").strip()
    ]
    unresolved_gaps = [
        {
            "gap": str(need.get("need_type") or "").strip(),
            "priority": round(
                _clamp01(
                    float(need.get("urgency") or 0.0) * 0.6
                    + float(need.get("severity") or 0.0) * 0.4
                ),
                4,
            ),
            "rationale": str(need.get("rationale") or "").strip(),
        }
        for need in needs[:6]
        if str(need.get("need_type") or "").strip()
    ]
    recommended_directions = [
        {
            "direction": str(intent.get("intent_type") or "").strip(),
            "priority": round(_clamp01(float(intent.get("priority") or 0.0)), 4),
            "candidate_kind": intent.get("candidate_kind"),
            "task_type": task_type_for_candidate_kind(intent.get("candidate_kind")),
            "target_horizon": intent.get("target_horizon"),
        }
        for intent in intents[:6]
        if str(intent.get("intent_type") or "").strip()
    ]
    active_signals = [
        {
            "signal": str(signal.get("signal_type") or "").strip(),
            "priority": round(_clamp01(float(signal.get("priority") or 0.0)), 4),
            "message": str(signal.get("message") or "").strip(),
        }
        for signal in signals[:6]
        if str(signal.get("signal_type") or "").strip()
    ]

    gaps = {
        str(item.get("gap") or "").strip(): item
        for item in unresolved_gaps
        if str(item.get("gap") or "").strip()
    }
    directions = {
        str(item.get("direction") or "").strip(): item
        for item in recommended_directions
        if str(item.get("direction") or "").strip()
    }
    relation_edges: List[Dict[str, Any]] = []
    for intent in intents[:8]:
        direction = str(intent.get("intent_type") or "").strip()
        direction_meta = directions.get(direction)
        if not direction_meta:
            continue
        for source_need in list(intent.get("source_needs") or []):
            gap_name = str(source_need or "").strip()
            gap_meta = gaps.get(gap_name)
            if gap_meta:
                relation_edges.append(
                    {
                        "from": gap_name,
                        "to": direction,
                        "relation": "elevates_direction",
                        "weight": round(
                            _clamp01(
                                float(gap_meta.get("priority") or 0.0) * 0.55
                                + float(direction_meta.get("priority") or 0.0) * 0.45
                            ),
                            4,
                        ),
                    }
                )
    for signal in signals[:8]:
        signal_name = str(signal.get("signal_type") or "").strip()
        if not signal_name:
            continue
        priority = round(_clamp01(float(signal.get("priority") or 0.0)), 4)
        related_intent = str(signal.get("related_intent") or "").strip()
        if related_intent and related_intent in directions:
            relation_edges.append(
                {
                    "from": signal_name,
                    "to": related_intent,
                    "relation": "amplifies_direction",
                    "weight": priority,
                }
            )
        relation_edges.append(
            {
                "from": signal_name,
                "to": focus,
                "relation": "shapes_focus",
                "weight": round(_clamp01(float(signal.get("priority") or 0.0) * 0.82), 4),
            }
        )

    evidence_topics = {
        str(node.get("topic") or "").strip(): node
        for node in evidence_nodes
        if str(node.get("topic") or "").strip()
    }
    need_topic_map = {
        "stabilize_memory_continuity": "self_understanding",
        "repair_truthfulness": "external_research",
        "expand_learning_frontier": "external_research",
        "prepare_body_growth": "body_state",
        _REVIEW_API_B_JUDGEMENT_NEED: "learning_trace",
        "observe_before_acting": "body_state",
    }
    evidence_to_gap_edges = []
    for gap in unresolved_gaps:
        gap_name = str(gap.get("gap") or "").strip()
        topic_name = need_topic_map.get(gap_name)
        topic_meta = evidence_topics.get(topic_name)
        if not topic_meta:
            continue
        evidence_to_gap_edges.append(
            {
                "from": topic_name,
                "to": gap_name,
                "relation": "supports_gap_assessment",
                "weight": round(
                    _clamp01(
                        float(topic_meta.get("avg_confidence") or 0.0) * 0.55
                        + float(gap.get("priority") or 0.0) * 0.45
                    ),
                    4,
                ),
            }
        )
    direction_task_links = [
        {
            "from": direction,
            "to_candidate_kind": candidate_kind,
            "to_task_type": task_type,
            "relation": "maps_to_task_shape",
            "weight": round(_clamp01(float(item.get("priority") or 0.0)), 4),
        }
        for item in recommended_directions[:8]
        for direction in [str(item.get("direction") or "").strip()]
        for candidate_kind in [str(item.get("candidate_kind") or "").strip()]
        for task_type in [str(item.get("task_type") or "").strip()]
        if direction and candidate_kind and task_type
    ]
    return {
        "focus": focus,
        "focus_confidence": round(
            _clamp01(
                0.35
                + float(policy.get("candidate_throttle") or 0.0) * 0.18
                + float(policy.get("observation_bias") or 0.0) * 0.12
            ),
            4,
        ),
        "current_topics": current_topics,
        "unresolved_gaps": sorted(unresolved_gaps, key=_priority, reverse=True)[:8],
        "recommended_directions": sorted(
            recommended_directions, key=_priority, reverse=True
        )[:8],
        "active_signals": sorted(active_signals, key=_priority, reverse=True)[:8],
        "evidence_to_gap_edges": evidence_to_gap_edges[:12],
        "relation_edges": relation_edges[:16],
        "direction_task_links": direction_task_links[:12],
    }


def _dict_rows(value: Any) -> List[Dict[str, Any]]:
    return [dict(item) for item in list(value or []) if isinstance(item, dict)]


def _priority(item: Dict[str, Any]) -> float:
    return float(item.get("priority") or 0.0)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
