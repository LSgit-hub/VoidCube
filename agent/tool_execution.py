"""Scheduling and outcome contracts for one assistant tool-call batch."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.contracts.execution import ExecutionState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Tool content plus structured user-consumable artifacts."""

    content: str
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class PreparedToolCall:
    """One normalized tool call in its original response order."""

    source: Any
    position: int
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCallOutcome:
    """Terminal outcome for one prepared tool call."""

    call: PreparedToolCall
    content: str
    duration: float
    state: ExecutionState
    skip_reason: Literal["before_batch", "after_call"] | None = None
    artifacts: tuple[Artifact, ...] = ()

    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None

    @property
    def failed(self) -> bool:
        return self.state is ExecutionState.FAILED


class ToolExecutionCoordinator:
    """Own tool-call ordering, concurrency, timing, and interrupt completion."""

    def __init__(
        self,
        *,
        invoke: Callable[[PreparedToolCall], Any],
        is_interrupted: Callable[[], bool],
        classify_failure: Callable[[str, str], tuple[bool, str]],
        max_workers: int,
        delay: float = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._invoke = invoke
        self._is_interrupted = is_interrupted
        self._classify_failure = classify_failure
        self._max_workers = max(1, int(max_workers))
        self._delay = max(0.0, float(delay))
        self._clock = clock
        self._sleep = sleep

    @staticmethod
    def prepare(tool_calls: Iterable[Any]) -> tuple[PreparedToolCall, ...]:
        """Normalize SDK tool calls without dropping malformed arguments."""
        prepared: list[PreparedToolCall] = []
        for position, tool_call in enumerate(tool_calls, 1):
            function = getattr(tool_call, "function", None)
            name = str(getattr(function, "name", "") or "")
            raw_arguments = getattr(function, "arguments", "") or ""
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Invalid JSON arguments for tool %s; using an empty object: %s",
                    name,
                    exc,
                )
                arguments = {}
            if not isinstance(arguments, dict):
                logger.warning(
                    "Non-object arguments for tool %s (%s); using an empty object",
                    name,
                    type(arguments).__name__,
                )
                arguments = {}
            prepared.append(
                PreparedToolCall(
                    source=tool_call,
                    position=position,
                    call_id=str(getattr(tool_call, "id", "") or ""),
                    name=name,
                    arguments=arguments,
                )
            )
        return tuple(prepared)

    def execute(
        self,
        calls: Iterable[PreparedToolCall],
        *,
        parallel: bool,
        before_call: Callable[[PreparedToolCall], None] | None = None,
        after_call: Callable[[ToolCallOutcome], None] | None = None,
        batch_started: Callable[[tuple[PreparedToolCall, ...]], None] | None = None,
        batch_completed: Callable[[tuple[ToolCallOutcome, ...]], None] | None = None,
    ) -> tuple[ToolCallOutcome, ...]:
        """Execute a prepared batch and emit exactly one outcome per call."""
        prepared = tuple(calls)
        if not prepared:
            return ()
        if self._is_interrupted():
            outcomes = tuple(
                self._skipped(call, reason="before_batch") for call in prepared
            )
            self._emit_outcomes(outcomes, after_call)
            return outcomes
        if parallel:
            return self._execute_parallel(
                prepared,
                before_call=before_call,
                after_call=after_call,
                batch_started=batch_started,
                batch_completed=batch_completed,
            )
        return self._execute_sequential(
            prepared,
            before_call=before_call,
            after_call=after_call,
        )

    def _execute_parallel(
        self,
        calls: tuple[PreparedToolCall, ...],
        *,
        before_call: Callable[[PreparedToolCall], None] | None,
        after_call: Callable[[ToolCallOutcome], None] | None,
        batch_started: Callable[[tuple[PreparedToolCall, ...]], None] | None,
        batch_completed: Callable[[tuple[ToolCallOutcome, ...]], None] | None,
    ) -> tuple[ToolCallOutcome, ...]:
        for call in calls:
            self._emit_before(call, before_call)
        self._emit_batch_started(calls, batch_started)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(calls), self._max_workers)
        ) as executor:
            outcomes = tuple(executor.map(self._run, calls))
        self._emit_batch_completed(outcomes, batch_completed)
        self._emit_outcomes(outcomes, after_call)
        return outcomes

    def _execute_sequential(
        self,
        calls: tuple[PreparedToolCall, ...],
        *,
        before_call: Callable[[PreparedToolCall], None] | None,
        after_call: Callable[[ToolCallOutcome], None] | None,
    ) -> tuple[ToolCallOutcome, ...]:
        outcomes: list[ToolCallOutcome] = []
        for index, call in enumerate(calls):
            if self._is_interrupted():
                skipped = tuple(
                    self._skipped(item, reason="before_batch")
                    for item in calls[index:]
                )
                outcomes.extend(skipped)
                self._emit_outcomes(skipped, after_call)
                break

            self._emit_before(call, before_call)
            outcome = self._run(call)
            outcomes.append(outcome)
            self._emit_after(outcome, after_call)

            remaining = calls[index + 1 :]
            if remaining and self._is_interrupted():
                skipped = tuple(
                    self._skipped(item, reason="after_call") for item in remaining
                )
                outcomes.extend(skipped)
                self._emit_outcomes(skipped, after_call)
                break
            if remaining and self._delay:
                self._sleep(self._delay)
        return tuple(outcomes)

    def _run(self, call: PreparedToolCall) -> ToolCallOutcome:
        started = self._clock()
        try:
            raw_result = self._invoke(call)
            if isinstance(raw_result, ToolExecutionResult):
                content = str(raw_result.content)
                artifacts = raw_result.artifacts
            else:
                content = raw_result if isinstance(raw_result, str) else str(raw_result)
                artifacts = ()
        except Exception as exc:
            content = f"Error executing tool '{call.name}': {exc}"
            artifacts = ()
            logger.error(
                "Tool invocation raised for %s: %s",
                call.name,
                exc,
                exc_info=True,
            )
        duration = max(0.0, self._clock() - started)
        is_error, _ = self._classify_failure(call.name, content)
        return ToolCallOutcome(
            call=call,
            content=content,
            duration=duration,
            state=(
                ExecutionState.FAILED
                if is_error
                else ExecutionState.SUCCEEDED
            ),
            artifacts=tuple(artifacts),
        )

    @staticmethod
    def _skipped(
        call: PreparedToolCall,
        *,
        reason: Literal["before_batch", "after_call"],
    ) -> ToolCallOutcome:
        if reason == "after_call":
            content = (
                f"[Tool execution skipped - {call.name} was not started. "
                "User sent a new message]"
            )
        else:
            content = (
                f"[Tool execution cancelled - {call.name} was skipped due to "
                "user interrupt]"
            )
        return ToolCallOutcome(
            call=call,
            content=content,
            duration=0.0,
            state=ExecutionState.CANCELLED,
            skip_reason=reason,
            artifacts=(),
        )

    @staticmethod
    def _emit_before(
        call: PreparedToolCall,
        callback: Callable[[PreparedToolCall], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(call)
        except Exception:
            logger.debug("Tool before-call callback failed", exc_info=True)

    @staticmethod
    def _emit_after(
        outcome: ToolCallOutcome,
        callback: Callable[[ToolCallOutcome], None] | None,
    ) -> None:
        if callback is None:
            return
        callback(outcome)

    @classmethod
    def _emit_outcomes(
        cls,
        outcomes: Iterable[ToolCallOutcome],
        callback: Callable[[ToolCallOutcome], None] | None,
    ) -> None:
        for outcome in outcomes:
            cls._emit_after(outcome, callback)

    @staticmethod
    def _emit_batch_started(
        calls: tuple[PreparedToolCall, ...],
        callback: Callable[[tuple[PreparedToolCall, ...]], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(calls)
        except Exception:
            logger.debug("Tool batch-start callback failed", exc_info=True)

    @staticmethod
    def _emit_batch_completed(
        outcomes: tuple[ToolCallOutcome, ...],
        callback: Callable[[tuple[ToolCallOutcome, ...]], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(outcomes)
        except Exception:
            logger.debug("Tool batch-complete callback failed", exc_info=True)
