"""Pure normalization for endogenous strategy-memory snapshots."""

from __future__ import annotations

from typing import Any, Dict


def normalize_endogenous_strategy_memory(raw: Any) -> Dict[str, Any]:
    """Normalize persisted strategy memory without accessing runtime state."""
    focus_stats: Dict[str, Dict[str, int]] = {}
    contextual_focus_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
    agenda_topic_stats: Dict[str, Dict[str, Any]] = {}
    observation_target_stats: Dict[str, Dict[str, Any]] = {}
    meta_governance_stats: Dict[str, Dict[str, Any]] = {}
    source = dict(raw or {}) if isinstance(raw, dict) else {}
    raw_focus_stats = source.get("focus_stats")
    if isinstance(raw_focus_stats, dict):
        for focus, stats in raw_focus_stats.items():
            focus_name = str(focus or "").strip().lower()
            if not focus_name or not isinstance(stats, dict):
                continue
            focus_stats[focus_name] = {
                "judged": max(0, int(stats.get("judged") or 0)),
                "completed": max(0, int(stats.get("completed") or 0)),
                "failed": max(0, int(stats.get("failed") or 0)),
                "dragging": max(0, int(stats.get("dragging") or 0)),
            }
    raw_contextual = source.get("contextual_focus_stats")
    if isinstance(raw_contextual, dict):
        for context_key, focus_map in raw_contextual.items():
            normalized_context = str(context_key or "").strip().lower()
            if not normalized_context or not isinstance(focus_map, dict):
                continue
            context_bucket: Dict[str, Dict[str, int]] = {}
            for focus, stats in focus_map.items():
                focus_name = str(focus or "").strip().lower()
                if not focus_name or not isinstance(stats, dict):
                    continue
                context_bucket[focus_name] = {
                    "judged": max(0, int(stats.get("judged") or 0)),
                    "completed": max(0, int(stats.get("completed") or 0)),
                    "failed": max(0, int(stats.get("failed") or 0)),
                    "dragging": max(0, int(stats.get("dragging") or 0)),
                }
            if context_bucket:
                contextual_focus_stats[normalized_context] = context_bucket
    raw_agenda_topic_stats = source.get("agenda_topic_stats")
    if isinstance(raw_agenda_topic_stats, dict):
        for topic, stats in raw_agenda_topic_stats.items():
            topic_name = str(topic or "").strip().lower()
            if not topic_name or not isinstance(stats, dict):
                continue
            agenda_topic_stats[topic_name] = {
                "seen": max(0, int(stats.get("seen") or 0)),
                "active_cycles": max(0, int(stats.get("active_cycles") or 0)),
                "resolved": max(0, int(stats.get("resolved") or 0)),
                "dragging": max(0, int(stats.get("dragging") or 0)),
                "last_priority": round(_clamp_ratio(stats.get("last_priority") or 0.0), 4),
                "last_confidence": round(_clamp_ratio(stats.get("last_confidence") or 0.0), 4),
                "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                "last_seen_at": stats.get("last_seen_at"),
                "last_resolved_at": stats.get("last_resolved_at"),
                "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
            }
    raw_observation_target_stats = source.get("observation_target_stats")
    if isinstance(raw_observation_target_stats, dict):
        for target, stats in raw_observation_target_stats.items():
            target_name = str(target or "").strip().lower()
            if not target_name or not isinstance(stats, dict):
                continue
            observation_target_stats[target_name] = {
                "seen": max(0, int(stats.get("seen") or 0)),
                "recommended": max(0, int(stats.get("recommended") or 0)),
                "resolved": max(0, int(stats.get("resolved") or 0)),
                "stalled": max(0, int(stats.get("stalled") or 0)),
                "last_priority": round(_clamp_ratio(stats.get("last_priority") or 0.0), 4),
                "last_risk": round(_clamp_ratio(stats.get("last_risk") or 0.0), 4),
                "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                "last_seen_at": stats.get("last_seen_at"),
                "last_resolved_at": stats.get("last_resolved_at"),
                "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
            }
    raw_meta_governance_stats = source.get("meta_governance_stats")
    if isinstance(raw_meta_governance_stats, dict):
        for mode, stats in raw_meta_governance_stats.items():
            mode_name = str(mode or "").strip().lower()
            if not mode_name or not isinstance(stats, dict):
                continue
            meta_governance_stats[mode_name] = {
                "seen": max(0, int(stats.get("seen") or 0)),
                "active_cycles": max(0, int(stats.get("active_cycles") or 0)),
                "resolved": max(0, int(stats.get("resolved") or 0)),
                "stalled": max(0, int(stats.get("stalled") or 0)),
                "last_priority": round(_clamp_ratio(stats.get("last_priority") or 0.0), 4),
                "last_confidence": round(_clamp_ratio(stats.get("last_confidence") or 0.0), 4),
                "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                "last_seen_at": stats.get("last_seen_at"),
                "last_resolved_at": stats.get("last_resolved_at"),
                "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
            }
    return {
        "focus_stats": focus_stats,
        "contextual_focus_stats": contextual_focus_stats,
        "agenda_topic_stats": agenda_topic_stats,
        "observation_target_stats": observation_target_stats,
        "meta_governance_stats": meta_governance_stats,
    }


def _clamp_ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = ["normalize_endogenous_strategy_memory"]
