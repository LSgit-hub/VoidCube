"""Single model-call boundary used by the turn orchestrator."""

from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from ...infrastructure.llm.request import ChatRequestConfig, build_chat_completion_kwargs
from ...infrastructure.llm.stream_response import StreamChunkUpdate
from ...domain.agent.api_attempt import ApiAttemptState
from ...domain.agent.response import inspect_chat_response, strip_thinking_blocks
from ...infrastructure.llm.error_classifier import ClassifiedError
from ...infrastructure.llm.retry_policy import (
    RetryDirective,
    RetryRecoveryKind,
    RetryRecoveryResult,
    decide_retry_directive,
    execute_retry_recovery,
)


class ModelTransport(Protocol):
    def complete(self, request: dict[str, Any]) -> Any: ...

    def stream(
        self, request: dict[str, Any], *,
        on_update: Callable[[StreamChunkUpdate], None],
        on_first_delta: Callable[[], None] | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """Response and request metadata for one transport attempt."""

    request_kwargs: dict[str, Any]
    response: Any
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ModelRecoveryResult:
    directive: RetryDirective
    recovery: RetryRecoveryResult


class ModelCallService:
    """Execute requests and apply the canonical post-credential retry policy."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def call(
        self,
        *,
        config: ChatRequestConfig,
        messages: Sequence[dict[str, Any]],
        transport: ModelTransport,
        on_update: Callable[[StreamChunkUpdate], None],
        on_first_delta: Callable[[], None] | None = None,
        before_send: Callable[[dict[str, Any]], None] | None = None,
    ) -> ModelCallResult:
        request_kwargs = build_chat_completion_kwargs(config, messages)
        if before_send is not None:
            before_send(request_kwargs)
        started = self._clock()
        response = transport.stream(
            request_kwargs,
            on_update=on_update,
            on_first_delta=on_first_delta,
        )
        return ModelCallResult(
            request_kwargs=request_kwargs,
            response=response,
            duration_seconds=max(0.0, self._clock() - started),
        )

    def recover(
        self,
        *,
        attempt: ApiAttemptState,
        classified: ClassifiedError,
        error: Exception,
        fallback_available: bool,
        credential_pool_may_recover: bool,
        activate_fallback: Callable[[RetryDirective], bool],
        recover_transport: Callable[[Exception, int, int], bool],
    ) -> ModelRecoveryResult:
        """Choose recovery, execute it once, then update attempt counters.

        The caller records the failed attempt after credential recovery has
        been exhausted. Context/turn restarts remain explicit caller actions;
        transport recovery must not refund a turn or reset compression limits.
        """
        directive = decide_retry_directive(
            classified, error,
            retry_count=attempt.retry_count,
            max_retries=attempt.max_retries,
            fallback_available=fallback_available,
            credential_pool_may_recover=credential_pool_may_recover,
            primary_recovery_attempted=attempt.primary_recovery_attempted,
        )
        recovery = execute_retry_recovery(
            directive, error,
            retry_count=attempt.retry_count,
            max_retries=attempt.max_retries,
            activate_fallback=lambda: activate_fallback(directive),
            recover_transport=recover_transport,
        )
        if recovery.kind is RetryRecoveryKind.fallback:
            attempt.reset_retry_cycle()
        elif recovery.kind is RetryRecoveryKind.transport:
            attempt.primary_recovery_attempted = True
            attempt.retry_count = 0
        return ModelRecoveryResult(directive=directive, recovery=recovery)

    def summarize(
        self, *, config: ChatRequestConfig,
        messages: Sequence[dict[str, Any]], transport: ModelTransport,
    ) -> str:
        """Request an iteration-limit summary, retrying an empty response once.

        Tools and custom request overrides are excluded so this final request
        cannot schedule another tool call after the turn budget is exhausted.
        Transport exceptions retain their identity for the caller's diagnostics.
        """
        request = build_chat_completion_kwargs(
            config, messages, include_tools=False, include_request_overrides=False,
        )
        for _ in range(2):
            inspection = inspect_chat_response(transport.complete(request))
            if inspection.valid and inspection.message.content:
                return strip_thinking_blocks(inspection.message.content).strip()
        return ""


__all__ = ["ModelCallResult", "ModelCallService", "ModelRecoveryResult"]
