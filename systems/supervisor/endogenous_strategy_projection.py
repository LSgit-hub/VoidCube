"""Pure strategy-memory projections for endogenous planning."""

from __future__ import annotations

from typing import Any, Dict, Optional


def derive_agenda_persistence_state(topic_stats: Dict[str, Any]) -> str:
    """Classify one agenda topic from its normalized history counters."""
    seen = max(0, int(topic_stats.get("seen") or 0))
    dragging = max(0, int(topic_stats.get("dragging") or 0))
    resolved = max(0, int(topic_stats.get("resolved") or 0))
    active_cycles = max(0, int(topic_stats.get("active_cycles") or 0))
    last_status = str(topic_stats.get("last_status") or "").strip().lower()

    if dragging >= 2 or (dragging >= 1 and active_cycles >= 3):
        return "dragging"
    if resolved >= 2 and resolved >= active_cycles:
        return "stabilizing"
    if seen >= 3 or active_cycles >= 3:
        return "persistent"
    if last_status == "resolved":
        return "cooling"
    return "emerging"


def build_attention_agenda_projection(
    *,
    deliberation: Dict[str, Any],
    governance_channels: Dict[str, Any],
    strategy_memory: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the agenda read model from deliberation and normalized memory."""
    needs = [
        dict(item)
        for item in list(deliberation.get("needs") or [])
        if isinstance(item, dict)
    ]
    intents = [
        dict(item)
        for item in list(deliberation.get("intents") or [])
        if isinstance(item, dict)
    ]
    signals = [
        dict(item)
        for item in list(deliberation.get("signals") or [])
        if isinstance(item, dict)
    ]
    reflection = dict(deliberation.get("reflection") or {})
    adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
    preferred_focus = str(adaptive_policy.get("preferred_focus") or "").strip().lower()
    agenda_topic_stats = dict(
        dict(strategy_memory or {}).get("agenda_topic_stats") or {}
    )

    perspective_map = {
        "stabilize_memory_continuity": "system_continuity",
        "repair_truthfulness": "user_alignment",
        "expand_learning_frontier": "self_growth",
        "prepare_body_growth": "self_growth",
        "observe_before_acting": "self_regulation",
    }
    channel_counts = {
        name: len(list(governance_channels.get(name) or []))
        for name in (
            "task_candidates",
            "observation_requests",
            "governance_review_requests",
            "truthfulness_alerts",
            "autonomy_alignment_requests",
        )
    }
    entries: list[Dict[str, Any]] = []

    for need in needs:
        need_type = str(need.get("need_type") or "").strip()
        if not need_type:
            continue
        matching_intent = next(
            (
                intent
                for intent in intents
                if need_type in set(intent.get("source_needs") or [])
            ),
            None,
        )
        matching_signal = next(
            (
                signal
                for signal in signals
                if need_type in set(signal.get("source_needs") or [])
            ),
            None,
        )
        agenda_priority = _clamp_ratio(
            float(need.get("severity") or 0.0) * 0.45
            + float(need.get("urgency") or 0.0) * 0.35
            + float(need.get("confidence") or 0.0) * 0.20
        )
        if need_type == "observe_before_acting":
            agenda_priority = _clamp_ratio(
                agenda_priority
                + float(adaptive_policy.get("observation_bias") or 0.0) * 0.18
                + (0.12 if preferred_focus == "observation" else 0.0)
                + (
                    0.08
                    if reflection.get("dominant_constraint") not in {None, "", "none"}
                    else 0.0
                )
            )
        observation_required = (
            need_type == "observe_before_acting"
            or str((matching_intent or {}).get("output_channel") or "").strip()
            == "drive_signal"
        )
        blocked_by = None
        if need_type == "observe_before_acting":
            blocked_by = reflection.get("dominant_constraint")
        elif need_type == "prepare_body_growth" and reflection.get("body_growth_blocked"):
            blocked_by = "body_growth_cooldown"
        topic_memory = dict(agenda_topic_stats.get(need_type) or {})
        persistence_state = derive_agenda_persistence_state(topic_memory)
        trending = "steady"
        if persistence_state in {"persistent", "dragging"}:
            trending = "warming"
        elif persistence_state in {"stabilizing", "cooling"}:
            trending = "cooling"
        entries.append(
            {
                "agenda_id": f"agenda:{need_type}",
                "topic": need_type,
                "perspective": perspective_map.get(need_type, "governance"),
                "objective": (matching_intent or {}).get("intent_type") or need_type,
                "priority": round(agenda_priority, 4),
                "urgency": round(float(need.get("urgency") or 0.0), 4),
                "confidence": round(float(need.get("confidence") or 0.0), 4),
                "target_horizon": (matching_intent or {}).get("target_horizon"),
                "recommended_channel": (matching_intent or {}).get("output_channel"),
                "supporting_signal": (matching_signal or {}).get("signal_type"),
                "observation_required": observation_required,
                "blocked_by": blocked_by,
                "persistence_state": persistence_state,
                "trend": trending,
                "seen_count": max(0, int(topic_memory.get("seen") or 0)),
                "active_cycles": max(0, int(topic_memory.get("active_cycles") or 0)),
                "resolved_count": max(0, int(topic_memory.get("resolved") or 0)),
                "dragging_count": max(0, int(topic_memory.get("dragging") or 0)),
                "last_status": topic_memory.get("last_status"),
                "why_now": need.get("rationale"),
            }
        )

    entries.sort(key=lambda item: item.get("priority") or 0.0, reverse=True)
    top_focus = str(adaptive_policy.get("preferred_focus") or "unknown").strip().lower() or "unknown"
    if entries:
        summary = (
            f"The endogenous core is prioritizing {entries[0]['topic']} while "
            f"holding {len(entries)} active agenda item(s) under {top_focus} focus; "
            f"top agenda persistence is {entries[0]['persistence_state']}."
        )
    else:
        summary = "The endogenous core has no active agenda items for the current cycle."
    return {
        "summary": summary,
        "active_count": len(entries),
        "preferred_focus": top_focus,
        "channel_counts": channel_counts,
        "entries": entries[:6],
    }


def _clamp_ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "build_attention_agenda_projection",
    "derive_agenda_persistence_state",
]
