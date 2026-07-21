"""Retry utilities — jittered backoff for decorrelated retries.

Replaces fixed exponential backoff with jittered delays to prevent
thundering-herd retry spikes when multiple sessions hit the same
rate-limited provider concurrently.
"""

import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from agent.error_classifier import ClassifiedError, FailoverReason

# Monotonic counter for jitter seed uniqueness within the same process.
# Protected by a lock to avoid race conditions in concurrent retry paths
# (e.g. multiple gateway sessions retrying simultaneously).
_jitter_counter = 0
_jitter_lock = threading.Lock()


class RetryKind(str, Enum):
    """The next stateful recovery branch the Agent must execute."""

    compress_payload = "compress_payload"
    recover_context = "recover_context"
    abort_client_error = "abort_client_error"
    exhausted = "exhausted"
    wait = "wait"


class RetryRecoveryKind(str, Enum):
    none = "none"
    fallback = "fallback"
    transport = "transport"


@dataclass(frozen=True, slots=True)
class RetryDirective:
    kind: RetryKind
    is_rate_limited: bool = False
    try_eager_fallback: bool = False
    try_transport_recovery: bool = False
    try_fallback: bool = False


@dataclass(frozen=True, slots=True)
class RetryRecoveryResult:
    kind: RetryRecoveryKind

    @property
    def recovered(self) -> bool:
        return self.kind is not RetryRecoveryKind.none


def decide_retry_directive(
    classified: ClassifiedError,
    error: Exception,
    *,
    retry_count: int,
    max_retries: int,
    fallback_available: bool,
    credential_pool_may_recover: bool,
    primary_recovery_attempted: bool,
) -> RetryDirective:
    """Choose the post-credential recovery branch without changing Agent state."""
    is_rate_limited = classified.reason in (
        FailoverReason.rate_limit,
        FailoverReason.billing,
    )
    eager_fallback = (
        is_rate_limited
        and fallback_available
        and not credential_pool_may_recover
    )

    if classified.reason == FailoverReason.payload_too_large:
        return RetryDirective(
            RetryKind.compress_payload,
            is_rate_limited=is_rate_limited,
            try_eager_fallback=eager_fallback,
        )
    if classified.reason == FailoverReason.context_overflow:
        return RetryDirective(
            RetryKind.recover_context,
            is_rate_limited=is_rate_limited,
            try_eager_fallback=eager_fallback,
        )

    is_local_validation_error = (
        isinstance(error, (ValueError, TypeError))
        and not isinstance(error, UnicodeEncodeError)
    )
    if is_local_validation_error or (
        not classified.retryable and not classified.should_compress
    ):
        return RetryDirective(
            RetryKind.abort_client_error,
            is_rate_limited=is_rate_limited,
            try_eager_fallback=eager_fallback,
            try_fallback=fallback_available,
        )

    if retry_count >= max_retries:
        return RetryDirective(
            RetryKind.exhausted,
            is_rate_limited=is_rate_limited,
            try_eager_fallback=eager_fallback,
            try_transport_recovery=not primary_recovery_attempted,
            try_fallback=fallback_available,
        )

    return RetryDirective(
        RetryKind.wait,
        is_rate_limited=is_rate_limited,
        try_eager_fallback=eager_fallback,
    )


def execute_retry_recovery(
    directive: RetryDirective,
    error: Exception,
    *,
    retry_count: int,
    max_retries: int,
    activate_fallback: Callable[[], bool],
    recover_transport: Callable[[Exception, int, int], bool],
) -> RetryRecoveryResult:
    """Execute recovery hooks in the order required by a retry directive."""
    fallback_attempted = False
    if directive.try_eager_fallback:
        fallback_attempted = True
        if activate_fallback():
            return RetryRecoveryResult(RetryRecoveryKind.fallback)

    if (
        directive.kind is RetryKind.exhausted
        and directive.try_transport_recovery
        and recover_transport(error, retry_count, max_retries)
    ):
        return RetryRecoveryResult(RetryRecoveryKind.transport)

    if (
        directive.kind in {RetryKind.abort_client_error, RetryKind.exhausted}
        and directive.try_fallback
        and not fallback_attempted
        and activate_fallback()
    ):
        return RetryRecoveryResult(RetryRecoveryKind.fallback)

    return RetryRecoveryResult(RetryRecoveryKind.none)


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """Compute a jittered exponential backoff delay.

    Args:
        attempt: 1-based retry attempt number.
        base_delay: Base delay in seconds for attempt 1.
        max_delay: Maximum delay cap in seconds.
        jitter_ratio: Fraction of computed delay to use as random jitter
            range.  0.5 means jitter is uniform in [0, 0.5 * delay].

    Returns:
        Delay in seconds: min(base * 2^(attempt-1), max_delay) + jitter.

    The jitter decorrelates concurrent retries so multiple sessions
    hitting the same provider don't all retry at the same instant.
    """
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2 ** exponent), max_delay)

    # Seed from time + counter for decorrelation even with coarse clocks.
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)

    return delay + jitter


def wait_for_retry(
    delay: float,
    *,
    interrupted: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    poll_interval: float = 0.2,
) -> bool:
    """Wait in interruptible slices and report whether the delay completed."""
    deadline = clock() + max(0.0, delay)
    while clock() < deadline:
        if interrupted():
            return False
        remaining = deadline - clock()
        sleep(min(max(0.001, poll_interval), remaining))
    return not interrupted()
