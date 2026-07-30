"""Polling mechanics for the CLI's embedded autonomous component."""

from __future__ import annotations

import contextlib
import io
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import Any, Protocol


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class _ThreadOutputProxy:
    """Discard writes from the component thread while preserving other output."""

    def __init__(self, original: Any, target_thread_id: int, sink: Any) -> None:
        self._original = original
        self._target_thread_id = target_thread_id
        self._sink = sink

    def write(self, data: str) -> Any:
        if threading.get_ident() == self._target_thread_id:
            return self._sink.write(data)
        return self._original.write(data)

    def flush(self) -> Any:
        if threading.get_ident() != self._target_thread_id:
            return self._original.flush()
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


@dataclass(frozen=True)
class EmbeddedAutonomousLoopPorts:
    """Host-owned operations used by the embedded component loop."""

    stop_event: StopEvent
    component_active: Callable[[], bool]
    set_component_active: Callable[[bool], None]
    refresh_statuses: Callable[[], None]
    can_poll_workflow: Callable[[], bool]
    poll_workflow: Callable[[], None]
    get_pending_input: Callable[[], object | None]
    execute_pending_input: Callable[[object], None]
    invalidate: Callable[[], None]
    report_error: Callable[[Exception], None]
    publish_idle_scene: Callable[[], None]


def run_embedded_autonomous_component_loop(
    ports: EmbeddedAutonomousLoopPorts,
) -> None:
    """Run one component poll cycle at a time without owning CLI state."""
    while not ports.stop_event.is_set() and ports.component_active():
        try:
            ports.set_component_active(True)
            ports.refresh_statuses()
            if ports.can_poll_workflow():
                ports.poll_workflow()
                pending_input = ports.get_pending_input()
                if pending_input:
                    thread_id = threading.get_ident()
                    stdout_proxy = _ThreadOutputProxy(sys.stdout, thread_id, io.StringIO())
                    stderr_proxy = _ThreadOutputProxy(sys.stderr, thread_id, io.StringIO())
                    with contextlib.redirect_stdout(stdout_proxy), contextlib.redirect_stderr(stderr_proxy):
                        ports.execute_pending_input(pending_input)
                    ports.poll_workflow()
        except Exception as error:
            ports.report_error(error)
        try:
            ports.invalidate()
        except Exception:
            pass
        ports.stop_event.wait(0.5)

    ports.set_component_active(False)
    try:
        ports.publish_idle_scene()
    except Exception:
        pass


def start_embedded_autonomous_component_loop(
    ports: EmbeddedAutonomousLoopPorts,
    *,
    thread_factory: Callable[..., Thread] = Thread,
) -> Thread:
    """Start the daemon autonomous loop over its explicit host ports."""
    thread = thread_factory(
        target=lambda: run_embedded_autonomous_component_loop(ports),
        daemon=True,
        name="autonomous-execution-component",
    )
    thread.start()
    return thread
