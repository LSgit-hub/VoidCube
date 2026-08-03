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


def build_judgement_core_projection(
    *,
    deliberation: Dict[str, Any],
    governance_channels: Dict[str, Any],
    attention_agenda: Dict[str, Any],
    uncertainty_ledger: Dict[str, Any],
    observation_program: Dict[str, Any],
    meta_governance: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the primary need/intent judgement from immutable projections."""
    reflection = dict(deliberation.get("reflection") or {})
    adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
    needs = [
        dict(item)
        for item in list(deliberation.get("needs") or [])[:6]
        if isinstance(item, dict)
    ]
    intents = [
        dict(item)
        for item in list(deliberation.get("intents") or [])[:6]
        if isinstance(item, dict)
    ]

    primary_need = dict(needs[0]) if needs else {}
    primary_intent: Dict[str, Any] = {}
    if primary_need:
        primary_need_type = str(primary_need.get("need_type") or "").strip()
        if primary_need_type:
            for intent in intents:
                source_needs = [
                    str(item).strip()
                    for item in list(intent.get("source_needs") or [])
                    if str(item).strip()
                ]
                if primary_need_type in source_needs:
                    primary_intent = dict(intent)
                    break
    if not primary_intent and intents:
        primary_intent = dict(intents[0])
    governance_summary = {
        "preferred_focus": str(adaptive_policy.get("preferred_focus") or "").strip() or None,
        "dominant_constraint": str(reflection.get("dominant_constraint") or "").strip() or None,
        "posture_signal_type": str(
            dict(governance_channels.get("posture") or {}).get("signal_type") or ""
        ).strip()
        or None,
        "observation_request_count": len(
            list(governance_channels.get("observation_requests") or [])
        ),
        "governance_review_request_count": len(
            list(governance_channels.get("governance_review_requests") or [])
        ),
        "truthfulness_alert_count": len(
            list(governance_channels.get("truthfulness_alerts") or [])
        ),
        "autonomy_alignment_request_count": len(
            list(governance_channels.get("autonomy_alignment_requests") or [])
        ),
    }

    summary_parts = [
        (
            f"primary_need={str(primary_need.get('need_type') or '').strip()}"
            if str(primary_need.get("need_type") or "").strip()
            else ""
        ),
        (
            f"primary_intent={str(primary_intent.get('intent_type') or '').strip()}"
            if str(primary_intent.get("intent_type") or "").strip()
            else ""
        ),
        (
            f"focus={str(adaptive_policy.get('preferred_focus') or '').strip()}"
            if str(adaptive_policy.get("preferred_focus") or "").strip()
            else ""
        ),
        (
            f"constraint={str(reflection.get('dominant_constraint') or '').strip()}"
            if str(reflection.get("dominant_constraint") or "").strip()
            else ""
        ),
    ]
    summary = "Judgement core: " + "; ".join(
        [item for item in summary_parts if item]
    )
    if summary == "Judgement core: ":
        summary = "Judgement core is not available yet."

    return {
        "summary": summary,
        "primary_need": primary_need or None,
        "primary_intent": primary_intent or None,
        "governance_outputs": governance_summary,
        "active_needs": needs,
        "active_intents": intents,
    }
