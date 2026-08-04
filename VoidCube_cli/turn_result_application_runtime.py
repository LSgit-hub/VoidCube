"""Apply a completed model-turn result to CLI conversation state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Mapping

from VoidCube_app.turn_contract import TurnOutcome, normalize_turn_outcome


@dataclass(frozen=True, slots=True)
class TurnResultApplicationPorts:
    """Conversation-history and autonomous state operations supplied by the host."""

    conversation_history: Callable[[], Sequence[dict[str, Any]]]
    set_conversation_history: Callable[[list[dict[str, Any]]], None]
    record_autonomous_result: Callable[..., None]
    record_autonomous_finished: Callable[..., None]
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
        *,
        autonomous_task_run_id: str,
        autonomous_timeout_reported: bool,
        autonomous_timeout_writeback_succeeded: bool,
    ) -> AppliedTurnResult:
        outcome = normalize_turn_outcome(
            result,
            fallback_history=self.ports.conversation_history(),
        )
        self.ports.set_conversation_history(list(outcome.conversation_history))
        turn_result = outcome.observation()
        if autonomous_timeout_reported:
            turn_result.update(
                {
                    "failed": True,
                    "interrupted": True,
                    "error": "Autonomous task timed out after 30 minutes.",
                }
            )
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
        self.ports.record_autonomous_result(
            turn_result,
            autonomous_task_run_id=autonomous_task_run_id,
            timeout_writeback_succeeded=autonomous_timeout_writeback_succeeded,
        )
        self.ports.record_autonomous_finished(
            turn_result,
            autonomous_task_run_id=autonomous_task_run_id,
            timeout_writeback_succeeded=autonomous_timeout_writeback_succeeded,
        )
        return AppliedTurnResult(outcome=outcome, turn_result=turn_result)
