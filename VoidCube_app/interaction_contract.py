"""UI-independent approval and clarification interaction contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    command: str
    description: str


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    status: ApprovalStatus
    reason: str = ""

    @property
    def approved(self) -> bool:
        return self.status is ApprovalStatus.APPROVED


class ApprovalSink(Protocol):
    def __call__(self, request: ApprovalRequest) -> ApprovalDecision: ...


def resolve_approval(
    request: ApprovalRequest,
    sink: ApprovalSink | None,
) -> ApprovalDecision:
    """Resolve an approval request, failing closed for missing or invalid sinks."""
    if sink is None:
        return ApprovalDecision(ApprovalStatus.UNAVAILABLE, "Approval UI is unavailable")
    try:
        decision = sink(request)
    except Exception as exc:
        return ApprovalDecision(
            ApprovalStatus.UNAVAILABLE,
            f"Approval UI failed: {exc}",
        )
    if (
        not isinstance(decision, ApprovalDecision)
        or not isinstance(decision.status, ApprovalStatus)
    ):
        return ApprovalDecision(
            ApprovalStatus.UNAVAILABLE,
            "Approval UI returned an invalid decision",
        )
    return decision


class ClarificationStatus(str, Enum):
    ANSWERED = "answered"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    question: str
    options: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        question: str,
        options: Sequence[str] | None = None,
    ) -> "ClarificationRequest":
        normalized_options = (options,) if isinstance(options, str) else (options or ())
        return cls(
            question=str(question or "").strip(),
            options=tuple(str(option) for option in normalized_options),
        )


@dataclass(frozen=True, slots=True)
class ClarificationDecision:
    status: ClarificationStatus
    answer: str = ""
    reason: str = ""

    def response_for_agent(self) -> str:
        if self.status is ClarificationStatus.ANSWERED:
            return self.answer
        if self.status is ClarificationStatus.CANCELLED:
            return "The user cancelled. Use your best judgement to proceed."
        if self.status is ClarificationStatus.TIMED_OUT:
            return (
                "The user did not provide a response within the time limit. "
                "Use your best judgement to make the choice and proceed."
            )
        return self.reason or "Interactive clarification is unavailable."


class ClarificationSink(Protocol):
    def __call__(
        self,
        request: ClarificationRequest,
    ) -> ClarificationDecision: ...


def resolve_clarification(
    request: ClarificationRequest,
    sink: ClarificationSink | None,
) -> ClarificationDecision:
    """Resolve a clarification request without exposing adapter internals."""
    if not request.question:
        return ClarificationDecision(
            ClarificationStatus.UNAVAILABLE,
            reason="Clarification question is empty.",
        )
    if sink is None:
        return ClarificationDecision(
            ClarificationStatus.UNAVAILABLE,
            reason="Interactive clarification is unavailable.",
        )
    try:
        decision = sink(request)
    except Exception as exc:
        return ClarificationDecision(
            ClarificationStatus.UNAVAILABLE,
            reason=f"Interactive clarification failed: {exc}",
        )
    if (
        not isinstance(decision, ClarificationDecision)
        or not isinstance(decision.status, ClarificationStatus)
    ):
        return ClarificationDecision(
            ClarificationStatus.UNAVAILABLE,
            reason="Interactive clarification returned an invalid decision.",
        )
    if decision.status is ClarificationStatus.ANSWERED and (
        not isinstance(decision.answer, str) or not decision.answer.strip()
    ):
        return ClarificationDecision(
            ClarificationStatus.UNAVAILABLE,
            reason="Interactive clarification returned an empty answer.",
        )
    return decision
