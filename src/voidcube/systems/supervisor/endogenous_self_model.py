"""Pure self-model and evidence feedback projections for endogenous drive."""

from __future__ import annotations

from typing import Any, Dict, List

from .endogenous_evidence import (
    channel_confidence_from_body,
    channel_confidence_from_learning,
    channel_confidence_from_research,
    channel_strength_from_learning,
    channel_strength_from_research,
    research_freshness_hint,
)


def build_recent_reference_alignment(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    drive_history = dict(drive_context.get("drive_history") or {})
    outcomes = [
        dict(item)
        for item in list(drive_history.get("outcomes") or [])
        if isinstance(item, dict)
    ]
    entry_count = 0
    score_total = 0.0
    weak_count = 0
    missing_evidence_counts: Dict[str, int] = {}
    missing_agenda_counts: Dict[str, int] = {}
    for outcome in outcomes[:12]:
        metadata = dict(outcome.get("metadata") or {})
        evidence = dict(outcome.get("evidence") or {})
        alignment = outcome.get("reference_alignment")
        if not isinstance(alignment, dict):
            alignment = metadata.get("reference_alignment")
        if not isinstance(alignment, dict):
            alignment = evidence.get("reference_alignment")
        if not isinstance(alignment, dict):
            continue
        entry_count += 1
        score_total += _clamp01(alignment.get("alignment_score") or 0.0)
        quality = str(alignment.get("alignment_quality") or "").strip().lower()
        if quality in {"weak", "partial", "drifted"}:
            weak_count += 1
        for node in list(alignment.get("missing_evidence_nodes") or [])[:4]:
            node_name = str(node).strip()
            if node_name:
                missing_evidence_counts[node_name] = missing_evidence_counts.get(node_name, 0) + 1
        for node in list(alignment.get("missing_agenda_nodes") or [])[:4]:
            node_name = str(node).strip()
            if node_name:
                missing_agenda_counts[node_name] = missing_agenda_counts.get(node_name, 0) + 1
        if entry_count >= 4:
            break

    if entry_count <= 0:
        return {
            "available": False,
            "summary": "No recent reference-alignment feedback is available yet.",
        }

    avg_score = score_total / entry_count
    missing_evidence_node_count = sum(missing_evidence_counts.values())
    missing_agenda_node_count = sum(missing_agenda_counts.values())
    return {
        "available": True,
        "entry_count": entry_count,
        "average_alignment_score": round(_clamp01(avg_score), 4),
        "weak_or_partial_count": weak_count,
        "primary_missing_evidence_node": _dominant_key(missing_evidence_counts) or None,
        "primary_missing_agenda_node": _dominant_key(missing_agenda_counts) or None,
        "missing_evidence_node_count": missing_evidence_node_count,
        "missing_agenda_node_count": missing_agenda_node_count,
        "summary": (
            f"Recent proposals show average reference alignment {_clamp01(avg_score):.2f}; "
            f"{weak_count} entries were weak/partial/drifted; "
            f"missing_evidence={missing_evidence_node_count}; "
            f"missing_agenda={missing_agenda_node_count}."
        ),
    }


def build_self_model_snapshot(
    *,
    perception: Dict[str, Any],
    world_model: Dict[str, Any],
    reflection: Dict[str, Any],
    adaptive_policy: Dict[str, Any],
    shell_body_profile: Dict[str, Any],
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    recent_reference_alignment: Dict[str, Any],
    evidence_graph: Dict[str, Any],
    agenda_graph: Dict[str, Any],
) -> Dict[str, Any]:
    body_profile_status = str(shell_body_profile.get("profile_status") or "unknown").strip()
    learning_state = str(reflection.get("learning_yield_state") or "unknown").strip()
    dominant_constraint = str(reflection.get("dominant_constraint") or "unknown").strip()
    preferred_focus = str(adaptive_policy.get("preferred_focus") or "observation").strip()
    governance_load_state = str(world_model.get("governance_load_state") or "unknown").strip()
    alignment_available = bool(recent_reference_alignment.get("available"))
    alignment_score = _clamp01(
        recent_reference_alignment.get("average_alignment_score") or 0.0
    )
    weak_alignment_count = max(
        0,
        int(recent_reference_alignment.get("weak_or_partial_count") or 0),
    )
    research_freshness = research_freshness_hint(external_research_evidence)
    top_topics = [
        str(item.get("topic") or "").strip()
        for item in list(evidence_graph.get("nodes") or [])[:4]
        if isinstance(item, dict) and str(item.get("topic") or "").strip()
    ]
    unresolved_gaps = [
        str(item.get("gap") or "").strip()
        for item in list(agenda_graph.get("unresolved_gaps") or [])[:4]
        if isinstance(item, dict) and str(item.get("gap") or "").strip()
    ]
    current_directions = [
        str(item.get("direction") or "").strip()
        for item in list(agenda_graph.get("recommended_directions") or [])[:4]
        if isinstance(item, dict) and str(item.get("direction") or "").strip()
    ]
    self_understanding_gaps: List[str] = []
    if body_profile_status != "ready":
        self_understanding_gaps.append("body_profile_incomplete")
    if not recent_learning_evidence:
        self_understanding_gaps.append("missing_recent_learning_trace")
    elif learning_state in {"weak", "low_yield", "unknown"}:
        self_understanding_gaps.append("recent_learning_not_yet_reliable")
    if not external_research_evidence:
        self_understanding_gaps.append("missing_external_research_support")
    elif research_freshness == "stale":
        self_understanding_gaps.append("external_research_is_stale")
    if alignment_available and weak_alignment_count > 0:
        self_understanding_gaps.append("reference_alignment_is_unstable")

    readiness_factors = {
        "body_structure": body_profile_status == "ready",
        "recent_learning": bool(recent_learning_evidence),
        "external_research": bool(external_research_evidence),
        "reference_alignment_feedback": alignment_available,
    }
    readiness_score = (
        (1.0 if readiness_factors["body_structure"] else 0.0) * 0.28
        + (1.0 if readiness_factors["recent_learning"] else 0.0) * 0.24
        + (1.0 if readiness_factors["external_research"] else 0.0) * 0.16
        + alignment_score * 0.16
        + _clamp01(world_model.get("self_confidence") or 0.0) * 0.16
    )
    summary = (
        f"当前自我模型看到：主约束={dominant_constraint}，"
        f"偏好焦点={preferred_focus}，身体状态={body_profile_status}，"
        f"学习状态={learning_state}，治理健康={governance_load_state}。"
    )
    if self_understanding_gaps:
        summary += " 当前自我理解缺口包括：" + "，".join(self_understanding_gaps[:4]) + "。"

    return {
        "identity_view": {
            "role": "endogenous_supervisory_core",
            "responsibility": "先自我理解，再推进自我迭代",
            "execution_scope": "governance_only",
        },
        "current_state": {
            "user_mode": perception.get("user_mode"),
            "system_posture": perception.get("system_posture"),
            "dominant_constraint": dominant_constraint,
            "preferred_focus": preferred_focus,
            "governance_load_state": governance_load_state,
            "learning_yield_state": learning_state,
            "body_profile_status": body_profile_status,
            "research_freshness": research_freshness,
        },
        "readiness": {
            "self_iteration_readiness_score": round(_clamp01(readiness_score), 4),
            "autonomy_readiness": round(
                _clamp01(reflection.get("autonomy_readiness") or 0.0),
                4,
            ),
            "readiness_factors": readiness_factors,
        },
        "self_understanding_gaps": self_understanding_gaps,
        "reference_alignment_feedback": {
            "available": alignment_available,
            "average_alignment_score": round(alignment_score, 4),
            "weak_or_partial_count": weak_alignment_count,
            "summary": recent_reference_alignment.get("summary"),
        },
        "current_topics": top_topics,
        "unresolved_gaps": unresolved_gaps,
        "current_directions": current_directions,
        "summary": summary,
    }


def build_evidence_credibility_summary(
    *,
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    shell_body_profile: Dict[str, Any],
    evidence_channels: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
) -> Dict[str, Any]:
    learning_confidence = channel_confidence_from_learning(recent_learning_evidence)
    research_confidence = channel_confidence_from_research(external_research_evidence)
    body_confidence = channel_confidence_from_body(shell_body_profile)
    channel_rows = [
        {
            "channel": "recent_learning",
            "confidence": round(_clamp01(learning_confidence), 4),
            "evidence_strength": channel_strength_from_learning(recent_learning_evidence),
            "item_count": len(recent_learning_evidence),
        },
        {
            "channel": "external_research",
            "confidence": round(_clamp01(research_confidence), 4),
            "evidence_strength": channel_strength_from_research(external_research_evidence),
            "item_count": len(external_research_evidence),
        },
        {
            "channel": "shell_body_profile",
            "confidence": round(_clamp01(body_confidence), 4),
            "evidence_strength": (
                "strong" if str(shell_body_profile.get("profile_status") or "") == "ready" else "weak"
            ),
            "item_count": 1 if shell_body_profile else 0,
        },
    ]
    high_credibility_channels = [
        row["channel"]
        for row in channel_rows
        if row["confidence"] >= 0.72 and row["evidence_strength"] in {"moderate", "strong"}
    ]
    weak_or_missing_channels = [
        row["channel"]
        for row in channel_rows
        if row["confidence"] < 0.45
        or row["item_count"] <= 0
        or row["evidence_strength"] == "weak"
    ]
    conflict_flags: List[str] = []
    for channel in list(evidence_channels.get("channels") or []):
        if not isinstance(channel, dict):
            continue
        for flag in list(channel.get("conflict_flags") or []):
            text = str(flag).strip()
            if text and text not in conflict_flags:
                conflict_flags.append(text)
    alignment_score = _clamp01(
        recent_reference_alignment.get("average_alignment_score") or 0.0
    )
    summary = (
        f"High-credibility channels: {', '.join(high_credibility_channels) if high_credibility_channels else 'none'}. "
        f"Weak or missing channels: {', '.join(weak_or_missing_channels) if weak_or_missing_channels else 'none'}. "
        f"Reference alignment score={alignment_score:.2f}."
    )
    return {
        "channels": channel_rows,
        "high_credibility_channels": high_credibility_channels,
        "weak_or_missing_channels": weak_or_missing_channels,
        "conflict_flags": conflict_flags[:8],
        "reference_alignment_score": round(alignment_score, 4),
        "summary": summary,
    }


def _dominant_key(counts: Dict[str, int]) -> str:
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
