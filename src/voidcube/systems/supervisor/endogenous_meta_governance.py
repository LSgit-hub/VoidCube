"""Pure meta-governance mode selection for endogenous planning."""

from __future__ import annotations

from typing import Any, Dict, Optional

from systems.supervisor.endogenous_state_projection import derive_corrective_mode
from systems.supervisor.endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)


def derive_meta_governance_mode(
    *,
    attention_agenda: Dict[str, Any],
    uncertainty_ledger: Dict[str, Any],
    observation_program: Dict[str, Any],
    self_regulation: Dict[str, Any],
    reflection: Dict[str, Any],
    adaptive_policy: Dict[str, Any],
    strategy_memory: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Select a governance mode from explicit cognition projections."""
    agenda_entries = [
        dict(item)
        for item in list(attention_agenda.get("entries") or [])
        if isinstance(item, dict)
    ]
    ledger_entries = [
        dict(item)
        for item in list(uncertainty_ledger.get("entries") or [])
        if isinstance(item, dict)
    ]
    observation_entries = [
        dict(item)
        for item in list(observation_program.get("entries") or [])
        if isinstance(item, dict)
    ]

    dominant_agenda = agenda_entries[0] if agenda_entries else {}
    dominant_uncertainty = ledger_entries[0] if ledger_entries else {}
    dominant_observation = observation_entries[0] if observation_entries else {}
    current_focus = str(adaptive_policy.get("preferred_focus") or "").strip().lower()
    dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()
    corrective_mode = derive_corrective_mode(self_regulation)
    normalized_strategy_memory = normalize_endogenous_strategy_memory(strategy_memory)
    meta_governance_stats = dict(
        normalized_strategy_memory.get("meta_governance_stats") or {}
    )

    observation_priority = float(dominant_observation.get("priority") or 0.0)
    uncertainty_risk = float(dominant_uncertainty.get("risk") or 0.0)
    agenda_priority = float(dominant_agenda.get("priority") or 0.0)
    candidate_throttle = float(adaptive_policy.get("candidate_throttle") or 0.0)
    observation_bias = float(adaptive_policy.get("observation_bias") or 0.0)
    autonomy_readiness = float(reflection.get("autonomy_readiness") or 0.0)
    last_mode = None
    last_mode_stats: Dict[str, Any] = {}
    if meta_governance_stats:
        last_mode, last_mode_stats = max(
            meta_governance_stats.items(),
            key=lambda item: (
                int(item[1].get("seen") or 0),
                int(item[1].get("active_cycles") or 0),
                float(item[1].get("last_confidence") or 0.0),
            ),
        )
        last_mode = str(last_mode or "").strip().lower() or None
        last_mode_stats = dict(last_mode_stats or {})

    mode_scores = {
        "observe": (
            observation_priority * 0.42
            + observation_bias * 0.22
            + uncertainty_risk * 0.2
            + (0.1 if current_focus == "observation" else 0.0)
            + (
                0.08
                if dominant_constraint
                in {"weak_learning_yield", "historical_underdelivery", "api_b_judgement_blockage"}
                else 0.0
            )
            + (0.06 if last_mode == "observe" else 0.0)
            - (0.04 if last_mode == "expand" and uncertainty_risk < 0.3 else 0.0)
        ),
        "correct": (
            float(self_regulation.get("dynamic_truthfulness_bias_boost") or 0.0) * 0.38
            + float(self_regulation.get("dynamic_learning_expansion_suppression") or 0.0)
            * 0.24
            + uncertainty_risk * 0.18
            + (0.08 if corrective_mode.get("mode") == "corrective" else 0.0)
            + (0.05 if last_mode == "correct" else 0.0)
        ),
        "expand": (
            agenda_priority * 0.34
            + float(adaptive_policy.get("learning_expansion_bias") or 0.0) * 0.26
            + max(0.0, 0.58 - candidate_throttle) * 0.2
            + max(0.0, autonomy_readiness - 0.35) * 0.1
            - uncertainty_risk * 0.12
            + (0.05 if last_mode == "expand" else 0.0)
            - (0.03 if last_mode == "observe" and uncertainty_risk > 0.45 else 0.0)
        ),
        "conserve": (
            candidate_throttle * 0.35
            + max(0.0, 0.52 - autonomy_readiness) * 0.22
            + float(self_regulation.get("dynamic_candidate_throttle_boost") or 0.0) * 0.18
            + (0.06 if current_focus == "governance_hygiene" else 0.0)
            + (0.04 if last_mode == "conserve" else 0.0)
        ),
    }
    mode = max(mode_scores.items(), key=lambda item: item[1])[0]
    confidence = _clamp_ratio(max(mode_scores.values()))
    if confidence < 0.2:
        mode = "observe" if uncertainty_risk >= agenda_priority else "conserve"
    elif last_mode and last_mode == mode and last_mode_stats:
        confidence = _clamp_ratio(
            confidence + min(0.08, float(last_mode_stats.get("active_cycles") or 0) * 0.01)
        )

    guardrails = []
    if mode in {"observe", "correct"}:
        guardrails.append("prioritize evidence collection before expansion")
    if mode == "expand":
        guardrails.append("avoid expanding when uncertainty remains unresolved")
    if mode == "conserve":
        guardrails.append("limit new candidate volume until pressure decays")
    if corrective_mode.get("active"):
        guardrails.append("respect active self-regulation boosts")

    stability = "stable"
    if confidence >= 0.72:
        stability = "strong"
    elif confidence >= 0.45:
        stability = "moderate"
    elif confidence > 0.0:
        stability = "fragile"

    return {
        "mode": mode,
        "confidence": round(confidence, 4),
        "drivers": [
            f"agenda={dominant_agenda.get('topic') or 'none'}",
            f"uncertainty={dominant_uncertainty.get('domain') or 'none'}",
            f"observation={dominant_observation.get('target') or 'none'}",
            f"current_focus={current_focus or 'unknown'}",
            f"dominant_constraint={dominant_constraint or 'none'}",
            f"last_mode={last_mode or 'none'}",
        ],
        "guardrails": guardrails,
        "stability": stability,
    }


def _clamp_ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = ["derive_meta_governance_mode"]
