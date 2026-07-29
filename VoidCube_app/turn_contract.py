"""UI-independent input and outcome contracts for application turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


Message = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TurnInput:
    user_message: Any
    prior_history: tuple[Message, ...]
    conversation_history: tuple[Message, ...]


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    conversation_history: tuple[Message, ...]
    response: str
    failed: bool
    partial: bool
    interrupted: bool
    error: str
    interrupt_message: Any = None
    response_previewed: bool = False
    last_reasoning: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.response) and not self.failed and not self.partial

    def response_or_error(self) -> str:
        if (self.failed or self.partial) and not self.response:
            return f"Error: {self.error or 'Unknown error'}"
        return self.response

    def observation(self, *, response: str | None = None) -> dict[str, Any]:
        return {
            "failed": self.failed,
            "partial": self.partial,
            "interrupted": self.interrupted,
            "error": self.error,
            "response": self.response if response is None else response,
        }


def begin_turn(
    conversation_history: Sequence[Message],
    user_message: Any,
) -> TurnInput:
    prior = tuple(conversation_history)
    return TurnInput(
        user_message=user_message,
        prior_history=prior,
        conversation_history=(*prior, {"role": "user", "content": user_message}),
    )


def normalize_turn_outcome(
    result: Mapping[str, Any] | None,
    *,
    fallback_history: Sequence[Message],
) -> TurnOutcome:
    if result is None:
        return TurnOutcome(
            conversation_history=tuple(fallback_history),
            response="",
            failed=True,
            partial=False,
            interrupted=False,
            error="No result returned",
        )

    messages = result.get("messages")
    history = tuple(messages) if isinstance(messages, (list, tuple)) else tuple(fallback_history)
    return TurnOutcome(
        conversation_history=history,
        response=str(result.get("final_response") or ""),
        failed=bool(result.get("failed")),
        partial=bool(result.get("partial")),
        interrupted=bool(result.get("interrupted")),
        error=str(result.get("error") or ""),
        interrupt_message=result.get("interrupt_message"),
        response_previewed=bool(result.get("response_previewed")),
        last_reasoning=str(result.get("last_reasoning") or ""),
    )
