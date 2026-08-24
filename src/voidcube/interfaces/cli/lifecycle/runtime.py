"""Coordinate interactive loop and application lifecycle runtimes."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import Thread
from typing import Any

from ..application_runtime import (
    CliApplicationPorts,
    CliApplicationRuntime,
)
from .idle_maintenance import (
    CliIdleMaintenancePorts,
    CliIdleMaintenanceRuntime,
)
from .guards import CliLifecycleGuardRuntime
from .run import CliRunRuntime, CliRunRuntimePorts


@dataclass(frozen=True, slots=True)
class CliInteractiveLifecyclePorts:
    """Interactive host operations supplied by the CLI composition root."""

    application: Any
    idle_maintenance: CliIdleMaintenancePorts
    lifecycle_guards: CliLifecycleGuardRuntime
    stop_requested: Callable[[], bool]
    refresh_status: Callable[[], None]
    presence_refresh_needed: Callable[[], bool]
    refresh_presence: Callable[[], None]
    command_running: Callable[[], bool]
    invalidate: Callable[[float], None]
    poll_scheduled_workflow: Callable[[], None]
    get_pending_input: Callable[[float], object]
    empty_input: type[Exception]
    execute_input: Callable[[object], None]
    report_input_error: Callable[[Exception], None]
    sleep: Callable[[float], None]
    monotonic_time: Callable[[], float]
    thread_factory: Callable[..., Thread]
    register_exit_cleanup: Callable[[Callable[[], None]], Any]
    cleanup: Callable[[], None]
    stdout_context: Callable[[], AbstractContextManager[Any]]
    report_unusable_stdin: Callable[[BaseException], None]
    request_stop: Callable[[], None]
    teardown: Callable[[], None]


class CliInteractiveLifecycleRuntime:
    """Start the existing loop runtime and run the existing application runtime."""

    def __init__(self, ports: CliInteractiveLifecyclePorts) -> None:
        self.ports = ports

    def run(self) -> None:
        ports = self.ports
        idle_runtime = CliIdleMaintenanceRuntime(ports.idle_maintenance)
        CliRunRuntime(
            CliRunRuntimePorts(
                stop_requested=ports.stop_requested,
                application_ready=lambda: bool(ports.application),
                refresh_status=ports.refresh_status,
                presence_refresh_needed=ports.presence_refresh_needed,
                refresh_presence=ports.refresh_presence,
                command_running=ports.command_running,
                invalidate=ports.invalidate,
                poll_scheduled_workflow=ports.poll_scheduled_workflow,
                perform_idle_maintenance=idle_runtime.run_once,
                get_pending_input=ports.get_pending_input,
                empty_input=ports.empty_input,
                execute_input=ports.execute_input,
                report_input_error=ports.report_input_error,
                sleep=ports.sleep,
                monotonic_time=ports.monotonic_time,
                thread_factory=ports.thread_factory,
            )
        ).start()

        CliApplicationRuntime(
            CliApplicationPorts(
                register_exit_cleanup=ports.register_exit_cleanup,
                cleanup=ports.cleanup,
                install_signal_handlers=ports.lifecycle_guards.install_signal_handlers,
                validate_stdin=ports.lifecycle_guards.validate_stdin,
                install_asyncio_exception_handler=(
                    ports.lifecycle_guards.install_asyncio_exception_handler
                ),
                stdout_context=ports.stdout_context,
                run_application=lambda: ports.application.run(handle_sigint=False),
                is_unusable_stdin_error=ports.lifecycle_guards.is_unusable_stdin_error,
                report_unusable_stdin=ports.report_unusable_stdin,
                request_stop=ports.request_stop,
                teardown=ports.teardown,
            )
        ).run()
