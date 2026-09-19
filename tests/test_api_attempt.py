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


def test_unicode_sanitization_budget_is_scoped_to_each_attempt():
    first = ApiAttemptState(started_at=10.0)
    first.unicode_sanitization_passes = 2

    second = ApiAttemptState(started_at=20.0)

    assert second.unicode_sanitization_passes == 0
