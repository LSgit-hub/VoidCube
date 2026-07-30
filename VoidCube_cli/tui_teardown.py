"""Ordered terminal-adapter teardown over explicit cleanup operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TuiTeardownPorts:
    """Cleanup operations whose resources remain owned by the CLI host."""

    stop_autonomous: Callable[[], None]
    interrupt_agent: Callable[[], None]
    shutdown_voice_recorder: Callable[[], None]
    cleanup_temp_voice_recordings: Callable[[], None]
    unregister_tool_callbacks: Callable[[], None]
    close_session: Callable[[], None]
    finish_interrupted_session: Callable[[], None]
    run_global_cleanup: Callable[[], None]
    print_exit_summary: Callable[[], None]


def run_tui_teardown(ports: TuiTeardownPorts) -> None:
    """Run the CLI's established shutdown order without owning its state."""
    ports.stop_autonomous()
    ports.interrupt_agent()
    ports.shutdown_voice_recorder()
    ports.cleanup_temp_voice_recordings()
    ports.unregister_tool_callbacks()
    ports.close_session()
    ports.finish_interrupted_session()
    ports.run_global_cleanup()
    ports.print_exit_summary()
