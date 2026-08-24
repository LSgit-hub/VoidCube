"""TUI refresh-thread lifecycle for the CLI terminal adapter."""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Thread


logger = logging.getLogger(__name__)


def run_tui_refresh_loop(
    *,
    stop_requested: Callable[[], bool],
    application_ready: Callable[[], bool],
    refresh_status: Callable[[], None],
    presence_refresh_needed: Callable[[], bool],
    refresh_presence: Callable[[], None],
    command_running: Callable[[], bool],
    invalidate: Callable[[float], None],
    monotonic_time: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Refresh TUI surfaces and presence at the existing cadence."""
    last_idle_refresh = 0.0
    last_status_refresh = 0.0
    last_presence_refresh = 0.0
    while not stop_requested():
        if not application_ready():
            sleep(0.1)
            continue
        now = monotonic_time()
        if now - last_status_refresh >= 1.0:
            last_status_refresh = now
            try:
                refresh_status()
            except Exception:
                logger.warning("TUI status refresh failed", exc_info=True)
        if now - last_presence_refresh >= 5.0 and presence_refresh_needed():
            try:
                refresh_presence()
            except Exception:
                logger.warning("TUI presence refresh failed", exc_info=True)
            last_presence_refresh = now
        if command_running():
            try:
                invalidate(0.1)
            except Exception:
                logger.debug("TUI command repaint failed", exc_info=True)
            sleep(0.1)
            continue
        if now - last_idle_refresh >= 1.0:
            last_idle_refresh = now
            try:
                invalidate(1.0)
            except Exception:
                logger.debug("TUI idle repaint failed", exc_info=True)
        sleep(0.2)


def start_tui_refresh_loop(
    *,
    stop_requested: Callable[[], bool],
    application_ready: Callable[[], bool],
    refresh_status: Callable[[], None],
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
            refresh_status=refresh_status,
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
