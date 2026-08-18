"""Pure read models for persisted endogenous Supervisor snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def project_drive_history(
    snapshot: Mapping[str, Any],
    *,
    normalize_strategy_memory: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Build the bounded history input consumed by endogenous drive evaluation."""
    return {
        "judgements": [
            dict(item)
            for item in list(snapshot.get("judgements") or [])[:24]
            if isinstance(item, dict)
        ],
        "outcomes": [
            dict(item)
            for item in list(snapshot.get("outcomes") or [])[:36]
            if isinstance(item, dict)
        ],
        "strategy_memory": normalize_strategy_memory(snapshot.get("strategy_memory")),
    }


def project_governance_event_stream(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build the bounded governance-event view exposed to runtime consumers."""
    return {
        "events": [
            dict(item)
            for item in list(snapshot.get("events") or [])[:36]
            if isinstance(item, dict)
        ],
    }


def derive_corrective_mode(self_regulation: Mapping[str, Any]) -> dict[str, Any]:
    """Classify the active self-regulation posture without touching runtime state."""
    throttle = max(0.0, float(self_regulation.get("dynamic_candidate_throttle_boost") or 0.0))
    observation = max(0.0, float(self_regulation.get("dynamic_observation_bias_boost") or 0.0))
    truthfulness = max(0.0, float(self_regulation.get("dynamic_truthfulness_bias_boost") or 0.0))
    learning_suppression = max(
        0.0,
        float(self_regulation.get("dynamic_learning_expansion_suppression") or 0.0),
    )
    active_boosts = {
        "candidate_throttle": round(throttle, 4),
        "observation_bias": round(observation, 4),
        "truthfulness_bias": round(truthfulness, 4),
        "learning_suppression": round(learning_suppression, 4),
    }
    mode = "rest"
    if truthfulness > 0.01 or learning_suppression > 0.01:
        mode = "corrective"
    elif throttle > 0.01 or observation > 0.01:
        mode = "guarded"
    return {
        "mode": mode,
        "active": mode != "rest",
        "last_reason": self_regulation.get("last_reason"),
        "active_boosts": active_boosts,
    }
