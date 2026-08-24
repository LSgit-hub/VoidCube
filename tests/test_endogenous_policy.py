from dataclasses import dataclass

from voidcube.systems.supervisor.endogenous_policy import (
    TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD,
    has_memory_backlog_recovery_window,
    has_truthfulness_review_signal,
)


@dataclass
class Perception:
    correction_signals: int = 0
    pending_review_count: int = 0
    stale_backlog_count: int = 0
    api_b_judgement_count: int = 0


@dataclass
class Reflection:
    dominant_constraint: str = "none"
    api_b_judgement_blockage_pressure: float = 0.0
    learning_yield_state: str = "mixed"


def test_truthfulness_signal_uses_the_canonical_threshold():
    assert TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD == 3
    assert has_truthfulness_review_signal(Perception(correction_signals=2)) is False
    assert has_truthfulness_review_signal(Perception(correction_signals=3)) is True


def test_memory_recovery_window_requires_clear_and_productive_state():
    assert has_memory_backlog_recovery_window(
        perception=Perception(pending_review_count=1),
        reflection=Reflection(),
    ) is True
    assert has_memory_backlog_recovery_window(
        perception=Perception(pending_review_count=1, correction_signals=3),
        reflection=Reflection(),
    ) is False
    assert has_memory_backlog_recovery_window(
        perception=Perception(pending_review_count=1),
        reflection=Reflection(dominant_constraint="historical_underdelivery"),
    ) is False
