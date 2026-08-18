"""Turn-scoped state for the Agent conversation loop."""

from __future__ import annotations

from dataclasses import dataclass

from .iteration_control import IterationBudget


@dataclass(slots=True)
class ConversationTurnState:
    """Mutable state owned by one ``run_conversation`` invocation."""

    api_call_count: int = 0
    final_response: str | None = None
    interrupted: bool = False
    exit_reason: str = "unknown"
    length_continue_retries: int = 0
    truncated_tool_call_retries: int = 0
    truncated_response_prefix: str = ""
    compression_attempts: int = 0

    def can_continue(
        self,
        *,
        max_iterations: int,
        iteration_budget: IterationBudget,
    ) -> bool:
        return (
            self.api_call_count < max_iterations
            and iteration_budget.remaining > 0
            and self.final_response is None
            and not self.interrupted
        )

    def begin_iteration(self, iteration_budget: IterationBudget) -> bool:
        """Count and consume one model iteration."""
        self.api_call_count += 1
        return iteration_budget.consume()

    def exhausted(
        self,
        *,
        max_iterations: int,
        iteration_budget: IterationBudget,
    ) -> bool:
        return (
            self.api_call_count >= max_iterations
            or iteration_budget.remaining <= 0
        )

    def completed(self) -> bool:
        return (
            self.final_response is not None
            and not self.interrupted
            and not self.exit_reason.startswith("max_iterations_reached(")
        )

    def clear_text_continuation(self) -> None:
        self.length_continue_retries = 0
        self.truncated_response_prefix = ""
