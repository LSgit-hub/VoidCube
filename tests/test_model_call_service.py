from types import SimpleNamespace

import pytest

from voidcube.domain.agent.api_attempt import ApiAttemptState
from voidcube.infrastructure.llm.error_classifier import ClassifiedError, FailoverReason
from voidcube.infrastructure.llm.retry_policy import RetryKind, RetryRecoveryKind

from voidcube.infrastructure.llm.request import ChatRequestConfig
from voidcube.runtime.agent.model_call_service import ModelCallService
from voidcube.runtime.agent.model_attempt_service import (
    ModelAttemptAction,
    ModelAttemptDecision,
    ModelAttemptPorts,
    ModelAttemptService,
)


class _Transport:
    def __init__(self):
        self.calls = []

    def stream(self, request, *, on_update, on_first_delta=None):
        self.calls.append(request)
        return SimpleNamespace(choices=[])


def test_model_call_service_builds_request_and_reports_duration():
    transport = _Transport()
    seen = []
    result = ModelCallService(clock=iter([10.0, 10.25]).__next__).call(
        config=ChatRequestConfig(model="demo"),
        messages=[{"role": "user", "content": "hello"}],
        transport=transport,
        on_update=lambda update: None,
        before_send=lambda request: seen.append(request),
    )
    assert result.response.choices == []
    assert result.duration_seconds == 0.25
    assert transport.calls == [result.request_kwargs]
    assert seen == [result.request_kwargs]


@pytest.mark.parametrize("error", [InterruptedError("stopped"), RuntimeError("offline")])
def test_request_snapshot_precedes_transport_failure_and_exception_is_preserved(error):
    seen = []

    def stream(request, **callbacks):
        assert seen == [request]
        raise error

    with pytest.raises(type(error)) as raised:
        ModelCallService().call(
            config=ChatRequestConfig(model="demo"), messages=[],
            transport=SimpleNamespace(stream=stream), on_update=lambda _: None,
            before_send=seen.append,
        )
    assert raised.value is error
    assert seen[0]["model"] == "demo"


def _recover(service, attempt, calls, *, transport_ok=False, fallback_ok=False,
             reason=FailoverReason.unknown, pool_available=False):
    return service.recover(
        attempt=attempt, classified=ClassifiedError(reason=reason, retryable=True),
        error=RuntimeError("provider down"), fallback_available=True,
        credential_pool_may_recover=pool_available,
        activate_fallback=lambda directive: calls.append(("fallback", directive.kind)) or fallback_ok,
        recover_transport=lambda error, count, maximum: calls.append(("transport", count, maximum)) or transport_ok,
    )


def test_transport_recovery_is_bounded_before_fallback_resets_retry_cycle():
    service = ModelCallService()
    attempt = ApiAttemptState(started_at=0, retry_count=3)
    calls = []
    result = _recover(service, attempt, calls, transport_ok=True, fallback_ok=True)
    assert result.recovery.kind is RetryRecoveryKind.transport
    assert calls == [("transport", 3, 3)]
    assert attempt.retry_count == 0
    assert attempt.primary_recovery_attempted is True

    attempt.retry_count = 3
    result = _recover(service, attempt, calls, transport_ok=True, fallback_ok=True)
    assert result.recovery.kind is RetryRecoveryKind.fallback
    assert calls == [("transport", 3, 3), ("fallback", RetryKind.exhausted)]
    assert attempt.retry_count == 0
    assert attempt.primary_recovery_attempted is False


def test_exhausted_recovery_preserves_failure_budget_when_all_backends_fail():
    attempt = ApiAttemptState(started_at=0, retry_count=3)
    calls = []
    result = _recover(ModelCallService(), attempt, calls)
    assert result.recovery.kind is RetryRecoveryKind.none
    assert result.directive.kind is RetryKind.exhausted
    assert calls == [("transport", 3, 3), ("fallback", RetryKind.exhausted)]
    assert not attempt.can_retry


def test_rate_limit_keeps_credential_recovery_ahead_of_fallback():
    attempt = ApiAttemptState(started_at=0, retry_count=1)
    calls = []
    result = _recover(ModelCallService(), attempt, calls,
                      reason=FailoverReason.rate_limit, pool_available=True)
    assert result.directive.kind is RetryKind.wait
    assert result.directive.is_rate_limited
    assert calls == []
    assert attempt.retry_count == 1


def test_fallback_rebuilds_request_from_current_configuration_and_messages():
    service = ModelCallService()
    transport = _Transport()
    messages = [{"role": "user", "content": "first"}]
    first = service.call(config=ChatRequestConfig(model="primary"), messages=messages,
                         transport=transport, on_update=lambda _: None)
    attempt = ApiAttemptState(started_at=0, retry_count=1)
    result = _recover(service, attempt, [], fallback_ok=True,
                      reason=FailoverReason.rate_limit)
    assert result.recovery.kind is RetryRecoveryKind.fallback
    messages.append({"role": "user", "content": "compacted"})
    second = service.call(config=ChatRequestConfig(model="fallback", max_tokens=256),
                          messages=messages, transport=transport, on_update=lambda _: None)
    assert first.request_kwargs["model"] == "primary"
    assert second.request_kwargs["model"] == "fallback"
    assert second.request_kwargs["messages"][-1]["content"] == "compacted"
    assert second.request_kwargs["max_tokens"] == 256


@pytest.mark.parametrize("empty_count", [0, 1, 2])
def test_summary_excludes_tools_and_overrides_and_bounds_empty_retries(empty_count):
    calls = []

    def complete(request):
        calls.append(request)
        content = "" if len(calls) <= empty_count else "<think>private</think>summary"
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=None), finish_reason="stop",
        )])

    summary = ModelCallService().summarize(
        config=ChatRequestConfig(
            model="demo",
            tools=({"type": "function", "function": {"name": "terminal"}},),
            request_overrides={"tool_choice": "required", "temperature": 1},
        ),
        messages=[{"role": "user", "content": "summarize"}],
        transport=SimpleNamespace(complete=complete),
    )
    assert summary == ("" if empty_count == 2 else "summary")
    assert len(calls) == min(2, empty_count + 1)
    assert all("tools" not in request and "tool_choice" not in request for request in calls)


def test_stream_callbacks_are_forwarded_without_replaying_delivery():
    seen = []
    update = object()

    def stream(request, *, on_update, on_first_delta):
        on_first_delta()
        on_update(update)
        return "answer"

    result = ModelCallService().call(
        config=ChatRequestConfig(model="demo"), messages=[],
        transport=SimpleNamespace(stream=stream),
        on_update=lambda value: seen.append(value),
        on_first_delta=lambda: seen.append("start"),
    )
    assert seen == ["start", update]
    assert result.response == "answer"


class _AttemptTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def stream(self, request, *, on_update, on_first_delta=None):
        self.requests.append(dict(request))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _StatusError(RuntimeError):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _valid_response(content="answer", finish_reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason=finish_reason,
            )
        ]
    )


def test_model_attempt_service_rebuilds_request_after_fallback_and_returns_success():
    transport = _AttemptTransport([_StatusError("rate limited", 429), _valid_response()])
    configs = iter(
        [
            (ChatRequestConfig(model="primary"), [{"role": "user", "content": "a"}]),
            (ChatRequestConfig(model="fallback"), [{"role": "user", "content": "b"}]),
        ]
    )
    switched = []

    def activate(_directive):
        switched.append(True)
        return True

    outcome = ModelAttemptService().execute(
        ports=ModelAttemptPorts(
            request_factory=lambda: next(configs),
            transport=transport,
            on_update=lambda _update: None,
            fallback_available=lambda: True,
            activate_fallback=activate,
            recover_transport=lambda *_args: False,
            wait=lambda *_args, **_kwargs: True,
        )
    )

    assert outcome.action is ModelAttemptAction.success
    assert switched == [True]
    assert [request["model"] for request in transport.requests] == [
        "primary",
        "fallback",
    ]


def test_model_attempt_service_returns_context_restart_without_mutating_turn_budget():
    error = RuntimeError("context length exceeded")
    transport = _AttemptTransport([error])
    decisions = []
    outcome = ModelAttemptService().execute(
        ports=ModelAttemptPorts(
            request_factory=lambda: (
                ChatRequestConfig(model="demo"),
                [{"role": "user", "content": "long"}],
            ),
            transport=transport,
            on_update=lambda _update: None,
            recover_context=lambda directive, classified, exc, attempt: (
                decisions.append((directive.kind, classified.reason, exc))
                or ModelAttemptDecision(ModelAttemptAction.restart_context)
            ),
            wait=lambda *_args, **_kwargs: True,
        )
    )

    assert outcome.action is ModelAttemptAction.restart_context
    assert decisions and decisions[0][1].value == "context_overflow"
    assert outcome.attempt.retry_count == 1


def test_model_attempt_service_honors_interrupt_during_retry_wait():
    transport = _AttemptTransport([RuntimeError("temporary")])
    waits = []

    def wait(delay, *, interrupted):
        waits.append(delay)
        assert not interrupted()
        return False

    outcome = ModelAttemptService().execute(
        ports=ModelAttemptPorts(
            request_factory=lambda: (
                ChatRequestConfig(model="demo"),
                [{"role": "user", "content": "hello"}],
            ),
            transport=transport,
            on_update=lambda _update: None,
            interrupted=lambda: False,
            wait=wait,
        )
    )

    assert outcome.action is ModelAttemptAction.interrupted
    assert outcome.final_response and "interrupted" in outcome.final_response.lower()
    assert len(waits) == 1
    assert len(transport.requests) == 1


def test_model_attempt_service_preserves_partial_context_recovery_result():
    transport = _AttemptTransport([RuntimeError("context length exceeded")])
    outcome = ModelAttemptService().execute(
        ports=ModelAttemptPorts(
            request_factory=lambda: (ChatRequestConfig(model="demo"), []),
            transport=transport,
            on_update=lambda _update: None,
            recover_context=lambda *_args: ModelAttemptDecision(
                ModelAttemptAction.partial,
                error="recovery exhausted",
                final_response="partial answer",
                details={"attempts": 3},
            ),
        )
    )
    assert outcome.action is ModelAttemptAction.partial
    assert outcome.error == "recovery exhausted"
    assert outcome.final_response == "partial answer"
    assert outcome.details == {"attempts": 3}
