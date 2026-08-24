from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from voidcube.application.scheduling.scheduled_executor import (
    ScheduledTaskExecutorPorts,
    ScheduledTaskExecutorRuntime,
)


def test_poll_recovers_gateway_before_abandoning_employee_claim():
    post_supervisor = Mock(
        side_effect=[OSError("gateway unavailable"), {"status": "idle", "claim": None}]
    )
    recover_executor = Mock(return_value=True)
    outbox = SimpleNamespace(
        pending_count=Mock(return_value=0),
        next_due=Mock(return_value=None),
    )
    runtime = ScheduledTaskExecutorRuntime(
        ScheduledTaskExecutorPorts(
            autonomous_mode_active=lambda: True,
            autonomous_mode_lock=None,
            execution_gate=None,
            get_session_id=lambda: "cli-session",
            set_execution_active=Mock(),
            set_companion_active=Mock(),
            start_background_task=Mock(return_value=False),
            post_supervisor=post_supervisor,
            rate_limit_metadata=lambda _error: {},
            writeback_outbox=outbox,
            recover_executor=recover_executor,
        ),
        poll_interval_seconds=0.5,
    )

    runtime.poll_workflow()

    assert post_supervisor.call_count == 2
    recover_executor.assert_called_once_with()

