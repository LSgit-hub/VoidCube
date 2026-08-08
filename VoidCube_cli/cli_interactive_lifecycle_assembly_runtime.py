"""Assemble CLI-owned idle and interactive lifecycle ports."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import Thread

from VoidCube_cli.cli_idle_maintenance_runtime import CliIdleMaintenancePorts
from VoidCube_cli.cli_interactive_lifecycle_runtime import (
    CliInteractiveLifecyclePorts,
    CliInteractiveLifecycleRuntime,
)
from VoidCube_cli.cli_lifecycle_guards import CliLifecycleGuardRuntime


@dataclass(frozen=True, slots=True)
class CliInteractiveLifecycleAssemblyPorts:
    """Host operations used to construct the interactive lifecycle ports."""

    application: object
    lifecycle_guards: CliLifecycleGuardRuntime
    agent_running: Callable[[], bool]
    check_config_changes: Callable[[], None]
    refresh_observation_surfaces: Callable[[Callable[[], None]], None]
    refresh_gateway_presence: Callable[[bool], None]
    autonomous_gate_active: Callable[[], bool]
    start_autonomous_execution: Callable[[], None]
    application_ready: Callable[[], bool]
    invalidate: Callable[[float], None]
    enqueue_pending_input: Callable[[str], None]
    stop_requested: Callable[[], bool]
    presence_refresh_needed: Callable[[], bool]
    command_running: Callable[[], bool]
    poll_scheduled_workflow: Callable[[], None]
    get_pending_input: Callable[[float], object]
    empty_input: type[Exception]
    execute_input: Callable[[object], None]
    report_input_error: Callable[[Exception], None]
    sleep: Callable[[float], None]
    monotonic_time: Callable[[], float]
    thread_factory: Callable[..., Thread]
    register_exit_cleanup: Callable[[Callable[[], None]], object]
    cleanup: Callable[[], None]
    stdout_context: Callable[[], AbstractContextManager[object]]
    report_unusable_stdin: Callable[[BaseException], None]
    request_stop: Callable[[], None]
    teardown: Callable[[], None]


class CliInteractiveLifecycleAssemblyRuntime:
    """Build and run the existing interactive lifecycle with host ports."""

    def __init__(self, ports: CliInteractiveLifecycleAssemblyPorts) -> None:
        self.ports = ports

    def run(self) -> None:
        ports = self.ports
        idle_maintenance = CliIdleMaintenancePorts(
            agent_running=ports.agent_running,
            check_config_changes=ports.check_config_changes,
            refresh_observation_surfaces=lambda: ports.refresh_observation_surfaces(
                lambda: ports.refresh_gateway_presence(False)
            ),
            autonomous_gate_active=ports.autonomous_gate_active,
            start_autonomous_execution=ports.start_autonomous_execution,
            application_ready=ports.application_ready,
            invalidate=ports.invalidate,
            enqueue_pending_input=ports.enqueue_pending_input,
        )
        CliInteractiveLifecycleRuntime(
            CliInteractiveLifecyclePorts(
                application=ports.application,
                idle_maintenance=idle_maintenance,
                lifecycle_guards=ports.lifecycle_guards,
                stop_requested=ports.stop_requested,
                presence_refresh_needed=ports.presence_refresh_needed,
                refresh_presence=lambda: ports.refresh_gateway_presence(True),
                command_running=ports.command_running,
                invalidate=ports.invalidate,
                poll_scheduled_workflow=ports.poll_scheduled_workflow,
                get_pending_input=ports.get_pending_input,
                empty_input=ports.empty_input,
                execute_input=ports.execute_input,
                report_input_error=ports.report_input_error,
                sleep=ports.sleep,
                monotonic_time=ports.monotonic_time,
                thread_factory=ports.thread_factory,
                register_exit_cleanup=ports.register_exit_cleanup,
                cleanup=ports.cleanup,
                stdout_context=ports.stdout_context,
                report_unusable_stdin=ports.report_unusable_stdin,
                request_stop=ports.request_stop,
                teardown=ports.teardown,
            )
        ).run()
