"""Apply a completed model-turn result to CLI conversation state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Mapping

from ....domain.contracts.turn import TurnOutcome, normalize_turn_outcome


@dataclass(frozen=True, slots=True)
class TurnResultApplicationPorts:
    """Conversation-history and autonomous state operations supplied by the host."""

    conversation_history: Callable[[], Sequence[dict[str, Any]]]
    set_conversation_history: Callable[[list[dict[str, Any]]], None]
    publish_usage: Callable[[Mapping[str, Any]], None] | None = None


@dataclass(frozen=True, slots=True)
class AppliedTurnResult:
    outcome: TurnOutcome
    turn_result: dict[str, Any]


class TurnResultApplicationRuntime:
    """Own result normalization and writeback into the CLI turn state."""

    def __init__(self, ports: TurnResultApplicationPorts) -> None:
        self.ports = ports

    def apply(
        self,
        result: Mapping[str, Any] | None,
    ) -> AppliedTurnResult:
        prior_history = tuple(self.ports.conversation_history())
        outcome = normalize_turn_outcome(
            result,
            fallback_history=prior_history,
        )
        self.ports.set_conversation_history(list(outcome.conversation_history))
        if outcome.conversation_history[: len(prior_history)] == prior_history:
            evidence_messages = outcome.conversation_history[len(prior_history) :]
        else:
            evidence_messages = outcome.conversation_history
        turn_result = outcome.observation(evidence_messages=evidence_messages)
        if self.ports.publish_usage is not None:
            raw_result = result if isinstance(result, Mapping) else {}
            usage = {
                key: raw_result[key]
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "estimated_cost_usd",
                    "cost_status",
                    "cost_source",
                    "api_calls",
                )
                if key in raw_result
            }
            if usage:
                self.ports.publish_usage(usage)
        return AppliedTurnResult(outcome=outcome, turn_result=turn_result)
