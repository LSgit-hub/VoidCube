"""Structured outcomes for non-transactional Agent side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


EffectStatus = Literal["succeeded", "degraded", "failed", "skipped", "queued"]


@dataclass(frozen=True, slots=True)
class EffectOutcome:
    """One observable side effect without changing the turn's completion state."""

    status: EffectStatus
    error: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        if self.error:
            payload["error"] = self.error
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def failed_effect(exc: BaseException) -> EffectOutcome:
    return EffectOutcome(
        status="failed",
        error=f"{type(exc).__name__}: {exc}",
    )


def require_effect_outcome(value: object, *, effect: str) -> EffectOutcome:
    if not isinstance(value, EffectOutcome):
        raise TypeError(
            f"{effect} must return EffectOutcome, got {type(value).__name__}"
        )
    return value


def finalization_status(*outcomes: EffectOutcome) -> Literal["succeeded", "degraded"]:
    if any(outcome.status in {"failed", "degraded"} for outcome in outcomes):
        return "degraded"
    return "succeeded"


__all__ = [
    "EffectOutcome",
    "EffectStatus",
    "failed_effect",
    "finalization_status",
    "require_effect_outcome",
]
