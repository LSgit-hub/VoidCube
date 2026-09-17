"""Application service for one assistant tool-call batch.

The service owns only loop mechanics.  Presentation, checkpoints and tool
routing remain callbacks supplied by the runtime composition root.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ...domain.agent.tool_scheduler import should_parallelize_tool_batch
from .tool_execution import (
    PreparedToolCall,
    ToolCallOutcome,
    ToolExecutionCoordinator,
)


class ToolLoopService:
    """Execute a prepared tool batch through one stable orchestration boundary."""

    def __init__(
        self,
        *,
        coordinator_factory: Callable[..., ToolExecutionCoordinator],
        invoke: Callable[[PreparedToolCall, bool], Any],
        is_interrupted: Callable[[], bool],
        classify_failure: Callable[[str, str], tuple[bool, str]],
        max_workers: int,
        delay: float = 0,
    ) -> None:
        self._coordinator_factory = coordinator_factory
        self._invoke = invoke
        self._is_interrupted = is_interrupted
        self._classify_failure = classify_failure
        self._max_workers = max_workers
        self._delay = delay

    def execute(
        self,
        tool_calls: Iterable[Any],
        *,
        before_call: Callable[[PreparedToolCall, bool], None] | None = None,
        after_call: Callable[[ToolCallOutcome, bool], None] | None = None,
        batch_started: Callable[[tuple[PreparedToolCall, ...]], None] | None = None,
        batch_completed: Callable[[tuple[ToolCallOutcome, ...]], None] | None = None,
    ) -> tuple[ToolCallOutcome, ...]:
        raw_calls = tuple(tool_calls)
        prepared = ToolExecutionCoordinator.prepare(raw_calls)
        if not prepared:
            return ()
        parallel = should_parallelize_tool_batch(raw_calls)
        coordinator = self._coordinator_factory(
            invoke=lambda call: self._invoke(call, parallel),
            is_interrupted=self._is_interrupted,
            classify_failure=self._classify_failure,
            max_workers=self._max_workers,
            delay=self._delay,
        )
        return coordinator.execute(
            prepared,
            parallel=parallel,
            before_call=(
                None
                if before_call is None
                else lambda call: before_call(call, parallel)
            ),
            after_call=(
                None
                if after_call is None
                else lambda outcome: after_call(outcome, parallel)
            ),
            batch_started=batch_started,
            batch_completed=batch_completed,
        )


__all__ = ["ToolLoopService"]
