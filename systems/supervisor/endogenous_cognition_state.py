"""Pure read-model assembly for the endogenous cognition state."""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_cognition_state_projection(
    *,
    enabled: bool,
    deliberation: Dict[str, Any],
    governance_channels: Dict[str, Any],
    governance_event_stream: Dict[str, Any],
    self_regulation: Dict[str, Any],
    drive_posture: Dict[str, Any],
    context_key: str,
    strategy_memory: Dict[str, Any],
    corrective_mode: Dict[str, Any],
    attention_agenda: Dict[str, Any],
    uncertainty_ledger: Dict[str, Any],
    observation_program: Dict[str, Any],
    meta_governance: Dict[str, Any],
    judgement_core: Dict[str, Any],
    proposal_cognition: Dict[str, Any],
) -> Dict[str, Any]:
    perception = dict(deliberation.get("perception") or {})
    world_model = dict(deliberation.get("world_model") or {})
    reflection = dict(deliberation.get("reflection") or {})
    adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
    recent_events = [
        dict(item)
        for item in list(governance_event_stream.get("events") or [])[:12]
        if isinstance(item, dict)
    ]
    channel_counts = {
        "task_candidates": len(list(governance_channels.get("task_candidates") or [])),
        "observation_requests": len(
            list(governance_channels.get("observation_requests") or [])
        ),
        "governance_review_requests": len(
            list(governance_channels.get("governance_review_requests") or [])
        ),
        "truthfulness_alerts": len(
            list(governance_channels.get("truthfulness_alerts") or [])
        ),
        "autonomy_alignment_requests": len(
            list(governance_channels.get("autonomy_alignment_requests") or [])
        ),
    }

    return {
        "status": "evaluated",
        "enabled": enabled,
        "identity": {
            "role": "endogenous_supervisory_core",
            "responsibility": (
                "Perceive user, system, and self state; then govern autonomous "
                "direction before execution."
            ),
            "execution_scope": "governance_only",
            "execution_chain_coupled": False,
        },
        "perception": perception,
        "world_model": world_model,
        "self_model": {
            "reflection": reflection,
            "adaptive_policy": adaptive_policy,
            "self_regulation": dict(self_regulation),
            "corrective_mode": corrective_mode,
        },
        "judgement_core": judgement_core,
        "governance": {
            "posture": drive_posture,
            "preferred_focus": adaptive_policy.get("preferred_focus"),
            "dominant_constraint": reflection.get("dominant_constraint"),
            "channel_counts": channel_counts,
            "channels": dict(governance_channels),
        },
        "proposal_cognition": proposal_cognition,
        "attention_agenda": attention_agenda,
        "uncertainty_ledger": uncertainty_ledger,
        "observation_program": observation_program,
        "meta_governance": meta_governance,
        "strategy_memory": {
            "focus_stats": dict(strategy_memory.get("focus_stats") or {}),
            "agenda_topic_stats": dict(
                strategy_memory.get("agenda_topic_stats") or {}
            ),
            "observation_target_stats": dict(
                strategy_memory.get("observation_target_stats") or {}
            ),
            "meta_governance_stats": dict(
                strategy_memory.get("meta_governance_stats") or {}
            ),
            "context_key": context_key,
            "current_context_focus_stats": dict(
                (strategy_memory.get("contextual_focus_stats") or {}).get(context_key)
                or {}
            ),
            "current_agenda_topic_stats": {
                str(entry.get("topic") or "").strip().lower(): dict(
                    dict(strategy_memory.get("agenda_topic_stats") or {}).get(
                        str(entry.get("topic") or "").strip().lower()
                    )
                    or {}
                )
                for entry in list(attention_agenda.get("entries") or [])
                if isinstance(entry, dict) and str(entry.get("topic") or "").strip()
            },
            "current_observation_target_stats": {
                str(entry.get("target") or "").strip().lower(): dict(
                    dict(strategy_memory.get("observation_target_stats") or {}).get(
                        str(entry.get("target") or "").strip().lower()
                    )
                    or {}
                )
                for entry in list(observation_program.get("entries") or [])
                if isinstance(entry, dict) and str(entry.get("target") or "").strip()
            },
            "current_meta_governance_stats": {
                str(meta_governance.get("mode") or "").strip().lower(): dict(
                    dict(strategy_memory.get("meta_governance_stats") or {}).get(
                        str(meta_governance.get("mode") or "").strip().lower()
                    )
                    or {}
                )
                if str(meta_governance.get("mode") or "").strip()
                else {}
            },
        },
        "recent_events": recent_events,
    }
