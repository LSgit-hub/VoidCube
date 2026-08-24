from __future__ import annotations

from voidcube.application.scheduling.scheduled_task_polling import (
    run_scheduled_task_poll_loop,
    start_scheduled_task_polling,
)


def test_poll_loop_uses_only_supplied_stop_poll_sleep_and_failure_ports() -> None:
    polls: list[None] = []
    failures: list[None] = []
    sleeps: list[float] = []

    def poll_workflow() -> None:
        polls.append(None)
        if len(polls) == 1:
            raise RuntimeError("temporary failure")

    run_scheduled_task_poll_loop(
        stop_requested=lambda: len(polls) >= 2,
        poll_workflow=poll_workflow,
        sleep=sleeps.append,
        report_failure=lambda: failures.append(None),
    )

    assert len(polls) == 2
    assert failures == [None]
    assert sleeps == [1.0, 1.0]


def test_start_polling_creates_and_starts_named_daemon_thread() -> None:
    captured: dict[str, object] = {}

    class _Thread:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.started = False

        def start(self) -> None:
            self.started = True

    thread = start_scheduled_task_polling(
        stop_requested=lambda: True,
        poll_workflow=lambda: None,
        sleep=lambda _seconds: None,
        report_failure=lambda: None,
        thread_factory=_Thread,  # type: ignore[arg-type]
    )

    assert thread.started is True
    assert captured["daemon"] is True
    assert captured["name"] == "scheduled-task-executor"
