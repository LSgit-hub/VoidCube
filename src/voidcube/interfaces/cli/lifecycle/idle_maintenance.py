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
        # Auto mode is an API-B planning switch. Approved work is consumed by
        # the employee scheduler; the CLI never starts an API-A pull loop.
        if ports.autonomous_gate_active() and ports.application_ready():
            ports.invalidate(0.5)
        drain_process_notifications(ports.enqueue_pending_input)


def drain_process_notifications(enqueue_pending_input: Callable[[str], None]) -> None:
    """Move completed terminal-process notifications into the CLI input queue."""
    try:
        from voidcube.infrastructure.execution.process_registry import ensure_process_registry
        from ..runtime_handlers import _format_process_notification

        registry = ensure_process_registry()
        while not registry.completion_queue.empty():
            event = registry.completion_queue.get_nowait()
            session_id = event.get("session_id", "")
            if (
                event.get("type") == "completion"
                and registry.is_completion_consumed(session_id)
            ):
                continue
            synthesized = _format_process_notification(event)
            if synthesized:
                enqueue_pending_input(synthesized)
                if event.get("type") == "completion" and session_id:
                    registry.mark_completion_consumed(session_id)
    except Exception:
        pass
