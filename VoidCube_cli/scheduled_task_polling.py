"""Lifecycle helper for the CLI scheduled-task polling thread."""

from __future__ import annotations

from collections.abc import Callable
from threading import Thread


def run_scheduled_task_poll_loop(
    *,
    stop_requested: Callable[[], bool],
    poll_workflow: Callable[[], None],
    sleep: Callable[[float], None],
    report_failure: Callable[[], None],
    interval_seconds: float = 1.0,
) -> None:
    """Poll the existing scheduled-task executor until the CLI requests stop."""
    while not stop_requested():
        try:
            poll_workflow()
        except Exception:
            report_failure()
        sleep(interval_seconds)


def start_scheduled_task_polling(
    *,
    stop_requested: Callable[[], bool],
    poll_workflow: Callable[[], None],
    sleep: Callable[[float], None],
    report_failure: Callable[[], None],
    thread_factory: Callable[..., Thread] = Thread,
) -> Thread:
    """Start the daemon that invokes the supplied scheduled-task operation."""
    thread = thread_factory(
        target=lambda: run_scheduled_task_poll_loop(
            stop_requested=stop_requested,
            poll_workflow=poll_workflow,
            sleep=sleep,
            report_failure=report_failure,
        ),
        daemon=True,
        name="scheduled-task-executor",
    )
    thread.start()
    return thread
