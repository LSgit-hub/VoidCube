from __future__ import annotations

import pytest

from agent.error_classifier import ClassifiedError, FailoverReason
from agent.retry_utils import RetryKind, decide_retry_directive


pytestmark = pytest.mark.unit


def _classified(
    reason: FailoverReason,
    *,
    retryable: bool = True,
    should_compress: bool = False,
) -> ClassifiedError:
    return ClassifiedError(
        reason=reason,
        retryable=retryable,
        should_compress=should_compress,
    )


def _decide(classified, error=RuntimeError("failure"), **overrides):
    options = {
        "retry_count": 1,
        "max_retries": 3,
        "fallback_available": False,
        "credential_pool_may_recover": False,
        "primary_recovery_attempted": False,
    }
    options.update(overrides)
    return decide_retry_directive(classified, error, **options)


def test_rate_limit_eagerly_falls_back_when_no_credential_can_recover():
    directive = _decide(
        _classified(FailoverReason.rate_limit),
        fallback_available=True,
    )

    assert directive.kind is RetryKind.wait
    assert directive.is_rate_limited is True
    assert directive.try_eager_fallback is True


def test_rate_limit_preserves_credential_pool_retry_before_fallback():
    directive = _decide(
        _classified(FailoverReason.rate_limit),
        fallback_available=True,
        credential_pool_may_recover=True,
    )

    assert directive.try_eager_fallback is False


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (FailoverReason.payload_too_large, RetryKind.compress_payload),
        (FailoverReason.context_overflow, RetryKind.recover_context),
    ],
)
def test_compression_reasons_select_their_dedicated_branch(reason, expected):
    directive = _decide(_classified(reason, should_compress=True))

    assert directive.kind is expected


@pytest.mark.parametrize("error", [ValueError("bad value"), TypeError("bad type")])
def test_local_validation_errors_abort_even_when_classification_is_unknown(error):
    directive = _decide(_classified(FailoverReason.unknown), error)

    assert directive.kind is RetryKind.abort_client_error


def test_unicode_encode_error_stays_on_recovery_path():
    error = UnicodeEncodeError("ascii", "é", 0, 1, "unsupported")

    directive = _decide(_classified(FailoverReason.unknown), error)

    assert directive.kind is RetryKind.wait


@pytest.mark.parametrize(
    "reason",
    [FailoverReason.auth, FailoverReason.billing],
)
def test_non_retryable_api_error_attempts_fallback_before_abort(reason):
    directive = _decide(
        _classified(reason, retryable=False),
        fallback_available=True,
    )

    assert directive.kind is RetryKind.abort_client_error
    assert directive.try_fallback is True


def test_exhausted_retry_attempts_transport_then_fallback():
    directive = _decide(
        _classified(FailoverReason.timeout),
        retry_count=3,
        fallback_available=True,
    )

    assert directive.kind is RetryKind.exhausted
    assert directive.try_transport_recovery is True
    assert directive.try_fallback is True


def test_exhausted_retry_does_not_repeat_transport_recovery():
    directive = _decide(
        _classified(FailoverReason.timeout),
        retry_count=3,
        primary_recovery_attempted=True,
    )

    assert directive.kind is RetryKind.exhausted
    assert directive.try_transport_recovery is False
