"""Coordinate maintenance performed while the interactive CLI is idle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliIdleMaintenancePorts:
    """Host-owned state and side effects used by the idle maintenance pass."""

    agent_running: Callable[[], bool]
    check_config_changes: Callable[[], None]
    refresh_observation_surfaces: Callable[[], None]
    autonomous_gate_active: Callable[[], bool]
    start_autonomous_execution: Callable[[], None]
    application_ready: Callable[[], bool]
    invalidate: Callable[[float], None]
    enqueue_pending_input: Callable[[str], None]


class CliIdleMaintenanceRuntime:
    """Run one non-blocking maintenance pass between queued inputs."""

    def __init__(self, ports: CliIdleMaintenancePorts) -> None:
        self.ports = ports

    def run_once(self) -> None:
        ports = self.ports
        if ports.agent_running():
            return

        ports.check_config_changes()
        ports.refresh_observation_surfaces()
        if ports.autonomous_gate_active():
            ports.start_autonomous_execution()
            if ports.application_ready():
                ports.invalidate(0.5)
        drain_process_notifications(ports.enqueue_pending_input)


def drain_process_notifications(enqueue_pending_input: Callable[[str], None]) -> None:
    """Move completed terminal-process notifications into the CLI input queue."""
    try:
        from tools.process_registry import process_registry
        from VoidCube_cli.cli_handlers import _format_process_notification

        while not process_registry.completion_queue.empty():
            event = process_registry.completion_queue.get_nowait()
            session_id = event.get("session_id", "")
            if (
                event.get("type") == "completion"
                and process_registry.is_completion_consumed(session_id)
            ):
                continue
            synthesized = _format_process_notification(event)
            if synthesized:
                enqueue_pending_input(synthesized)
                if event.get("type") == "completion" and session_id:
                    process_registry.mark_completion_consumed(session_id)
    except Exception:
        pass
