"""Coordinate the long-lived worker loops owned by the interactive CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import Any

from VoidCube_cli.input_process_loop import start_input_process_loop
from VoidCube_cli.scheduled_task_polling import start_scheduled_task_polling
from VoidCube_cli.tui_refresh_loop import start_tui_refresh_loop


@dataclass(frozen=True, slots=True)
class CliRunRuntimePorts:
    """Lifecycle and queue operations supplied by the CLI host."""

    stop_requested: Callable[[], bool]
    application_ready: Callable[[], bool]
    presence_refresh_needed: Callable[[], bool]
    refresh_presence: Callable[[], None]
    command_running: Callable[[], bool]
    invalidate: Callable[[float], None]
    poll_scheduled_workflow: Callable[[], None]
    perform_idle_maintenance: Callable[[], None]
    get_pending_input: Callable[[float], object]
    empty_input: type[Exception]
    execute_input: Callable[[object], None]
    report_input_error: Callable[[Exception], None]
    sleep: Callable[[float], None]
    monotonic_time: Callable[[], float]
    thread_factory: Callable[..., Thread]


class CliRunRuntime:
    """Start the refresh, scheduled-task and input-processing loops together."""

    def __init__(self, ports: CliRunRuntimePorts) -> None:
        self.ports = ports

    def start(self) -> None:
        ports = self.ports
        start_tui_refresh_loop(
            stop_requested=ports.stop_requested,
            application_ready=ports.application_ready,
            presence_refresh_needed=ports.presence_refresh_needed,
            refresh_presence=ports.refresh_presence,
            command_running=ports.command_running,
            invalidate=ports.invalidate,
            monotonic_time=ports.monotonic_time,
            sleep=ports.sleep,
            thread_factory=ports.thread_factory,
        )
        start_scheduled_task_polling(
            stop_requested=ports.stop_requested,
            poll_workflow=ports.poll_scheduled_workflow,
            sleep=ports.sleep,
            report_failure=ports.report_input_error,
            thread_factory=ports.thread_factory,
        )
        start_input_process_loop(
            stop_requested=ports.stop_requested,
            get_pending_input=ports.get_pending_input,
            empty_input=ports.empty_input,
            perform_idle_maintenance=ports.perform_idle_maintenance,
            execute_input=ports.execute_input,
            sleep=ports.sleep,
            report_error=ports.report_input_error,
            thread_factory=ports.thread_factory,
        )
