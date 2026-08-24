from __future__ import annotations

import pytest

from voidcube.domain.agent.api_attempt import ApiAttemptState


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_attempt_state_owns_retry_cycle():
    state = ApiAttemptState(started_at=10.0, max_retries=2)

    assert state.can_retry
    assert state.record_failure() == 1
    assert state.can_retry
    assert state.record_failure() == 2
    assert not state.can_retry


def test_attempt_retry_reset_clears_primary_recovery_only():
    state = ApiAttemptState(
        started_at=10.0,
        retry_count=2,
        primary_recovery_attempted=True,
        rate_limit_retry_attempted=True,
    )

    state.reset_retry_cycle()

    assert state.retry_count == 0
    assert state.primary_recovery_attempted is False
    assert state.rate_limit_retry_attempted is True


def test_attempt_restart_transitions_are_explicit():
    state = ApiAttemptState(started_at=10.0)

    state.request_compressed_restart()
    state.request_length_continuation()

    assert state.restart_with_compressed_messages is True
    assert state.restart_with_length_continuation is True
