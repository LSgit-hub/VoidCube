"""State machine for one model-backed conversation iteration.

The turn runner owns conversation and tool semantics.  This service owns the
API-attempt lifecycle: request rebuilding, response validation, retry policy,
credential recovery, cancellation, and the hand-off to context/truncation
recovery.  It deliberately receives factories and ports instead of an Agent
instance so a changed provider or token budget is observed on every attempt.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...domain.agent.api_attempt import ApiAttemptState
from ...domain.agent.message_sanitizer import (
    sanitize_messages_non_ascii,
    sanitize_messages_surrogates,
)
from ...domain.agent.response import inspect_chat_response
from ...infrastructure.llm.error_classifier import (
    ClassifiedError,
    classify_api_error,
    summarize_api_error,
)
from ...infrastructure.llm.request import ChatRequestConfig
from ...infrastructure.llm.retry_policy import (
    RetryDirective,
    RetryKind,
    RetryRecoveryKind,
    jittered_backoff,
    wait_for_retry,
)
from ...infrastructure.llm.stream_response import StreamChunkUpdate
from .model_call_service import ModelCallService, ModelTransport


class ModelAttemptAction(str, Enum):
    """What the turn orchestrator should do after the attempt service returns."""

    success = "success"
    retry = "retry"
    restart_context = "restart_context"
    restart_continuation = "restart_continuation"
    interrupted = "interrupted"
    failed = "failed"
    partial = "partial"


@dataclass(frozen=True, slots=True)
class ModelAttemptDecision:
    """Decision returned by a context or response recovery port."""

    action: ModelAttemptAction
    error: str | None = None
    final_response: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ModelAttemptOutcome:
    """Structured result for one outer-loop model iteration."""

    action: ModelAttemptAction
    attempt: ApiAttemptState
    response: Any = None
    inspection: Any = None
    request_kwargs: dict[str, Any] | None = None
    duration_seconds: float = 0.0
    error: str | None = None
    final_response: str | None = None
    classified: ClassifiedError | None = None
    directive: RetryDirective | None = None
    is_rate_limited: bool = False
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ModelAttemptPorts:
    """Runtime adapters used by :class:`ModelAttemptService`.

    ``request_factory`` is called before every transport attempt.  It must
    return the current request configuration and API-only messages so provider
    fallback, credential rotation, and output-cap changes cannot reuse stale
    request state.
    """

    request_factory: Callable[
        [], tuple[ChatRequestConfig, Sequence[dict[str, Any]]]
    ]
    transport: ModelTransport
    on_update: Callable[[StreamChunkUpdate], None]
    on_first_delta: Callable[[], None] | None = None
    before_send: Callable[[dict[str, Any]], None] | None = None
    provider: Callable[[], str] = lambda: ""
    context_length: Callable[[], int] = lambda: 200_000
    approx_tokens: Callable[[], int] = lambda: 0
    interrupted: Callable[[], bool] = lambda: False
    sanitize_messages: Callable[[list[dict[str, Any]], Exception], bool] | None = None
    recover_credentials: Callable[
        [ClassifiedError, Exception, ApiAttemptState], bool
    ] = lambda _classified, _error, _attempt: False
    refresh_subscription: Callable[
        [ClassifiedError, Exception, ApiAttemptState], bool
    ] = lambda _classified, _error, _attempt: False
    fallback_available: Callable[[], bool] = lambda: False
    credential_pool_may_recover: Callable[[ClassifiedError], bool] = (
        lambda _classified: False
    )
    activate_fallback: Callable[[RetryDirective], bool] = (
        lambda _directive: False
    )
    activate_fallback_for_invalid: Callable[[], bool] = lambda: False
    recover_transport: Callable[[Exception, int, int], bool] = (
        lambda _error, _retry_count, _max_retries: False
    )
    recover_context: Callable[
        [RetryDirective, ClassifiedError, Exception, ApiAttemptState],
        ModelAttemptDecision,
    ] = lambda _directive, _classified, _error, _attempt: ModelAttemptDecision(
        ModelAttemptAction.failed,
        error="Context recovery is not configured",
    )
    recover_truncation: Callable[
        [Any, str, ApiAttemptState], ModelAttemptDecision
    ] = lambda _message, _finish_reason, _attempt: ModelAttemptDecision(
        ModelAttemptAction.failed,
        error="Response truncation recovery is not configured",
    )
    on_invalid_response: Callable[[Any, ApiAttemptState], None] | None = None
    on_error: Callable[
        [Exception, ClassifiedError, ApiAttemptState, ChatRequestConfig], None
    ] | None = None
    on_retry_wait: Callable[[float, bool], None] | None = None
    wait: Callable[..., bool] = wait_for_retry


class ModelAttemptService:
    """Execute the bounded API-attempt state machine for one turn iteration."""

    def __init__(
        self,
        *,
        call_service: ModelCallService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._call_service = call_service or ModelCallService()
        self._clock = clock

    def execute(
        self,
        *,
        ports: ModelAttemptPorts,
        attempt: ApiAttemptState | None = None,
    ) -> ModelAttemptOutcome:
        state = attempt or ApiAttemptState(started_at=self._clock())

        while state.can_retry:
            config, request_messages = ports.request_factory()
            request_list = (
                request_messages
                if isinstance(request_messages, list)
                else list(request_messages)
            )
            try:
                result = self._call_service.call(
                    config=config,
                    messages=request_list,
                    transport=ports.transport,
                    on_update=ports.on_update,
                    on_first_delta=ports.on_first_delta,
                    before_send=lambda request: self._record_request(
                        state, request, ports.before_send
                    ),
                )
                state.response = result.response
                state.request_kwargs = result.request_kwargs
                state.response_inspection = inspect_chat_response(
                    result.response,
                    duration_seconds=result.duration_seconds,
                )

                if not state.response_inspection.valid:
                    state.record_failure()
                    if ports.on_invalid_response is not None:
                        ports.on_invalid_response(state.response_inspection, state)
                    if ports.activate_fallback_for_invalid():
                        state.reset_retry_cycle()
                        continue
                    if not state.can_retry:
                        return self._outcome(
                            ModelAttemptAction.failed,
                            state,
                            result=result,
                            error=(
                                "Invalid API response after "
                                f"{state.max_retries} retries: "
                                f"{state.response_inspection.failure_hint}"
                            ),
                        )
                    if not self._wait_for_retry(
                        ports,
                        jittered_backoff(
                            state.retry_count, base_delay=5.0, max_delay=120.0
                        ),
                    ):
                        return self._interrupted_outcome(
                            state,
                            result=result,
                            final_response=(
                                "Operation interrupted during retry "
                                f"({state.response_inspection.failure_hint}, attempt "
                                f"{state.retry_count}/{state.max_retries})."
                            ),
                        )
                    continue

                state.finish_reason = state.response_inspection.finish_reason
                if state.finish_reason == "length":
                    decision = ports.recover_truncation(
                        state.response_inspection.message,
                        state.finish_reason,
                        state,
                    )
                    if decision.action is ModelAttemptAction.retry:
                        continue
                    if decision.action in {
                        ModelAttemptAction.restart_continuation,
                        ModelAttemptAction.restart_context,
                        ModelAttemptAction.failed,
                        ModelAttemptAction.partial,
                    }:
                        return self._outcome(
                            decision.action,
                            state,
                            result=result,
                            error=decision.error,
                            final_response=decision.final_response,
                            details=decision.details,
                        )

                state.rate_limit_retry_attempted = False
                return self._outcome(
                    ModelAttemptAction.success,
                    state,
                    result=result,
                )

            except InterruptedError as error:
                elapsed = max(0.0, self._clock() - state.started_at)
                return self._interrupted_outcome(
                    state,
                    error=error,
                    final_response=(
                        "Operation interrupted: waiting for model response "
                        f"({elapsed:.1f}s elapsed)."
                    ),
                )
            except Exception as error:
                # Sanitization is an attempt-local recovery.  It does not
                # consume the provider retry budget because no valid request
                # reached the provider yet.
                if isinstance(error, UnicodeEncodeError) and state.unicode_sanitization_passes < 2:
                    sanitized = False
                    if ports.sanitize_messages is not None:
                        sanitized = ports.sanitize_messages(request_list, error)
                    else:
                        sanitized = sanitize_messages_surrogates(request_list)
                        if not sanitized and "ascii" in str(error).lower():
                            sanitized = sanitize_messages_non_ascii(request_list)
                    if sanitized:
                        state.unicode_sanitization_passes += 1
                        continue

                classified = classify_api_error(
                    error,
                    provider=ports.provider(),
                    model=config.model,
                    approx_tokens=max(0, int(ports.approx_tokens() or 0)),
                    context_length=max(1, int(ports.context_length() or 200_000)),
                    num_messages=len(request_list),
                )
                if ports.recover_credentials(classified, error, state):
                    continue
                if ports.refresh_subscription(classified, error, state):
                    continue

                state.record_failure()
                if ports.on_error is not None:
                    ports.on_error(error, classified, state, config)
                if ports.interrupted():
                    return self._interrupted_outcome(
                        state,
                        error=error,
                        final_response=(
                            "Operation interrupted while handling API error "
                            f"({type(error).__name__}: {summarize_api_error(error)})."
                        ),
                    )

                recovery = self._call_service.recover(
                    attempt=state,
                    classified=classified,
                    error=error,
                    fallback_available=ports.fallback_available(),
                    credential_pool_may_recover=ports.credential_pool_may_recover(
                        classified
                    ),
                    activate_fallback=ports.activate_fallback,
                    recover_transport=ports.recover_transport,
                )
                if recovery.recovery.kind in {
                    RetryRecoveryKind.fallback,
                    RetryRecoveryKind.transport,
                }:
                    continue

                directive = recovery.directive
                if directive.kind in {
                    RetryKind.compress_payload,
                    RetryKind.recover_context,
                }:
                    decision = ports.recover_context(
                        directive, classified, error, state
                    )
                    if decision.action is ModelAttemptAction.restart_context:
                        return self._outcome(
                            decision.action,
                            state,
                            error=decision.error,
                            details=decision.details,
                            directive=directive,
                            classified=classified,
                        )
                    if decision.action in {
                        ModelAttemptAction.failed,
                        ModelAttemptAction.partial,
                    }:
                        return self._outcome(
                            decision.action,
                            state,
                            error=decision.error,
                            final_response=decision.final_response,
                            details=decision.details,
                            directive=directive,
                            classified=classified,
                        )
                    continue

                if directive.kind in {RetryKind.abort_client_error, RetryKind.exhausted}:
                    return self._outcome(
                        ModelAttemptAction.failed,
                        state,
                        error=str(error),
                        directive=directive,
                        classified=classified,
                        is_rate_limited=directive.is_rate_limited,
                    )

                retry_after = None
                if directive.is_rate_limited:
                    from ...infrastructure.llm.error_classifier import retry_after_seconds

                    retry_after = retry_after_seconds(error)
                wait_time = retry_after or jittered_backoff(
                    state.retry_count, base_delay=2.0, max_delay=60.0
                )
                if not self._wait_for_retry(
                    ports,
                    wait_time,
                    rate_limited=directive.is_rate_limited,
                ):
                    return self._interrupted_outcome(
                        state,
                        error=error,
                        final_response=(
                            "Operation interrupted: retrying API call after error "
                            f"(retry {state.retry_count}/{state.max_retries})."
                        ),
                    )

        return self._outcome(
            ModelAttemptAction.failed,
            state,
            error="All API retries exhausted with no successful response.",
        )

    @staticmethod
    def _record_request(
        state: ApiAttemptState,
        request: dict[str, Any],
        before_send: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        state.request_kwargs = request
        if before_send is not None:
            before_send(request)

    @staticmethod
    def _outcome(
        action: ModelAttemptAction,
        state: ApiAttemptState,
        *,
        result: Any = None,
        error: str | None = None,
        final_response: str | None = None,
        directive: RetryDirective | None = None,
        classified: ClassifiedError | None = None,
        is_rate_limited: bool = False,
        details: dict[str, Any] | None = None,
    ) -> ModelAttemptOutcome:
        if result is None:
            return ModelAttemptOutcome(
                action=action,
                attempt=state,
                response=state.response,
                inspection=state.response_inspection,
                request_kwargs=state.request_kwargs,
                error=error,
                final_response=final_response,
                directive=directive,
                classified=classified,
                is_rate_limited=is_rate_limited,
                details=details,
            )
        return ModelAttemptOutcome(
            action=action,
            attempt=state,
            response=result.response,
            inspection=state.response_inspection,
            request_kwargs=result.request_kwargs,
            duration_seconds=result.duration_seconds,
            error=error,
            final_response=final_response,
            directive=directive,
            classified=classified,
            is_rate_limited=is_rate_limited,
            details=details,
        )

    @classmethod
    def _interrupted_outcome(
        cls,
        state: ApiAttemptState,
        *,
        error: Exception | None = None,
        result: Any = None,
        final_response: str,
    ) -> ModelAttemptOutcome:
        return cls._outcome(
            ModelAttemptAction.interrupted,
            state,
            result=result,
            error=str(error) if error is not None else None,
            final_response=final_response,
        )

    @staticmethod
    def _wait_for_retry(
        ports: ModelAttemptPorts,
        delay: float,
        *,
        rate_limited: bool = False,
    ) -> bool:
        if ports.on_retry_wait is not None:
            ports.on_retry_wait(delay, rate_limited)
        return ports.wait(
            delay,
            interrupted=ports.interrupted,
        )


__all__ = [
    "ModelAttemptAction",
    "ModelAttemptDecision",
    "ModelAttemptOutcome",
    "ModelAttemptPorts",
    "ModelAttemptService",
]
