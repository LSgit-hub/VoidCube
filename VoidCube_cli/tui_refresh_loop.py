"""TUI refresh-thread lifecycle for the CLI terminal adapter."""

from __future__ import annotations

from collections.abc import Callable
from threading import Thread


def run_tui_refresh_loop(
    *,
    stop_requested: Callable[[], bool],
    application_ready: Callable[[], bool],
    presence_refresh_needed: Callable[[], bool],
    refresh_presence: Callable[[], None],
    command_running: Callable[[], bool],
    invalidate: Callable[[float], None],
    monotonic_time: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Refresh TUI surfaces and presence at the existing cadence."""
    last_idle_refresh = 0.0
    last_presence_refresh = 0.0
    while not stop_requested():
        if not application_ready():
            sleep(0.1)
            continue
        now = monotonic_time()
        if now - last_presence_refresh >= 5.0 and presence_refresh_needed():
            refresh_presence()
            last_presence_refresh = now
        if command_running():
            invalidate(0.1)
            sleep(0.1)
            continue
        if now - last_idle_refresh >= 1.0:
            last_idle_refresh = now
            invalidate(1.0)
        sleep(0.2)


def start_tui_refresh_loop(
    *,
    stop_requested: Callable[[], bool],
    application_ready: Callable[[], bool],
    presence_refresh_needed: Callable[[], bool],
    refresh_presence: Callable[[], None],
    command_running: Callable[[], bool],
    invalidate: Callable[[float], None],
    monotonic_time: Callable[[], float],
    sleep: Callable[[float], None],
    thread_factory: Callable[..., Thread] = Thread,
) -> Thread:
    """Start the daemon thread that runs the supplied TUI refresh ports."""
    thread = thread_factory(
        target=lambda: run_tui_refresh_loop(
            stop_requested=stop_requested,
            application_ready=application_ready,
            presence_refresh_needed=presence_refresh_needed,
            refresh_presence=refresh_presence,
            command_running=command_running,
            invalidate=invalidate,
            monotonic_time=monotonic_time,
            sleep=sleep,
        ),
        daemon=True,
    )
    thread.start()
    return thread
