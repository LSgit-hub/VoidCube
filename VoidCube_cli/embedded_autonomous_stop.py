"""Stop sequencing for the CLI's embedded autonomous component."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddedAutonomousStopPorts:
    """Component operations whose resource ownership stays with the CLI."""

    deactivate_component_host: Callable[[], bool]
    interrupt_running_agent: Callable[[], None]
    interrupt_current_task: Callable[[], None]
    signal_stop: Callable[[], None]


def stop_embedded_autonomous_component(
    ports: EmbeddedAutonomousStopPorts,
    *,
    interrupt: bool = False,
) -> None:
    """Deactivate the child host, optionally interrupt work, then signal its loop."""
    component_present = ports.deactivate_component_host()
    if component_present and interrupt:
        ports.interrupt_running_agent()
        ports.interrupt_current_task()
    ports.signal_stop()
