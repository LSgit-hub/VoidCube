"""Queue-processing loop mechanics for the CLI terminal adapter."""

from __future__ import annotations

from collections.abc import Callable
from threading import Thread


def run_input_process_loop(
    *,
    stop_requested: Callable[[], bool],
    get_pending_input: Callable[[float], object],
    empty_input: type[Exception],
    perform_idle_maintenance: Callable[[], None],
    execute_input: Callable[[object], None],
    sleep: Callable[[float], None],
    report_error: Callable[[Exception], None],
) -> None:
    """Run the existing queue/gate protocol without owning CLI business state."""
    while not stop_requested():
        try:
            try:
                user_input = get_pending_input(0.1)
            except empty_input:
                perform_idle_maintenance()
                continue
            execute_input(user_input)
        except Exception as exc:
            report_error(exc)


def start_input_process_loop(
    *,
    stop_requested: Callable[[], bool],
    get_pending_input: Callable[[float], object],
    empty_input: type[Exception],
    perform_idle_maintenance: Callable[[], None],
    execute_input: Callable[[object], None],
    sleep: Callable[[float], None],
    report_error: Callable[[Exception], None],
    thread_factory: Callable[..., Thread] = Thread,
) -> Thread:
    """Start the daemon input loop over supplied queue and execution ports."""
    thread = thread_factory(
        target=lambda: run_input_process_loop(
            stop_requested=stop_requested,
            get_pending_input=get_pending_input,
            empty_input=empty_input,
            perform_idle_maintenance=perform_idle_maintenance,
            execute_input=execute_input,
            sleep=sleep,
            report_error=report_error,
        ),
        daemon=True,
    )
    thread.start()
    return thread
