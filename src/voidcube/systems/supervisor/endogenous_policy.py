"""Pure policy gates shared by endogenous deliberation and planning."""

from __future__ import annotations

from typing import Protocol


TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD = 3
HISTORICAL_OBSERVATION_CARRYOVER_RELEASED = (
    "historical_observation_carryover_released"
)


class PerceptionPolicySignals(Protocol):
    correction_signals: int
    pending_review_count: int
    stale_backlog_count: int
    api_b_judgement_count: int


class ReflectionPolicySignals(Protocol):
    dominant_constraint: str
    api_b_judgement_blockage_pressure: float
    learning_yield_state: str


def has_truthfulness_review_signal(perception: PerceptionPolicySignals) -> bool:
    return perception.correction_signals >= TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD


def has_memory_backlog_recovery_window(
    *,
    perception: PerceptionPolicySignals,
    reflection: ReflectionPolicySignals,
) -> bool:
    return (
        reflection.dominant_constraint == "none"
        and not has_truthfulness_review_signal(perception)
        and perception.pending_review_count > 0
        and perception.stale_backlog_count <= 0
        and perception.api_b_judgement_count <= 1
        and reflection.api_b_judgement_blockage_pressure <= 0.22
        and reflection.learning_yield_state in {"mixed", "strong"}
    )
