"""Ordered terminal-adapter teardown over explicit cleanup operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TuiTeardownPorts:
    """Cleanup operations whose resources remain owned by the CLI host."""

    stop_autonomous: Callable[[], None]
    interrupt_agent: Callable[[], None]
    interrupt_voice: Callable[[], None]
    close_voice_session: Callable[[], None]
    unregister_tool_callbacks: Callable[[], None]
    close_session: Callable[[], None]
    close_session_owner: Callable[[], None]
    finish_interrupted_session: Callable[[], None]
    run_global_cleanup: Callable[[], None]
    print_exit_summary: Callable[[], None]
    shutdown_scheduler: Callable[[], None] | None = None


def run_tui_teardown(ports: TuiTeardownPorts) -> None:
    """Run the CLI's established shutdown order without owning its state."""
    if ports.shutdown_scheduler is not None:
        ports.shutdown_scheduler()
    ports.stop_autonomous()
    ports.interrupt_agent()
    ports.interrupt_voice()
    ports.close_voice_session()
    ports.unregister_tool_callbacks()
    ports.close_session()
    ports.close_session_owner()
    ports.finish_interrupted_session()
    ports.run_global_cleanup()
    ports.print_exit_summary()
