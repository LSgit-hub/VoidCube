"""Lifecycle coordinator for the embedded autonomous CLI component."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any

from VoidCube_cli.embedded_autonomous_loop import (
    EmbeddedAutonomousLoopPorts,
    start_embedded_autonomous_component_loop,
)
from VoidCube_cli.embedded_autonomous_stop import (
    EmbeddedAutonomousStopPorts,
    stop_embedded_autonomous_component,
)


@dataclass(frozen=True, slots=True)
class EmbeddedAutonomousRuntimePorts:
    """Explicit operations owned by the parent CLI around the child host."""

    get_component_host: Callable[[], Any | None]
    ensure_component_host: Callable[[], Any]
    get_component_thread: Callable[[], Thread | None]
    store_component_thread: Callable[[Thread], None]
    ensure_stop_event: Callable[[], Event]
    parent_component_active: Callable[[], bool]
    set_component_active: Callable[[Any, bool], None]
    build_executor_runtime: Callable[[Any], Any]
    refresh_statuses: Callable[[Any], None]
    can_poll_workflow: Callable[[Any], bool]
    get_pending_input: Callable[[Any], object | None]
    execute_pending_input: Callable[[Any, object], None]
    invalidate: Callable[[], None]
    report_error: Callable[[Exception], None]
    publish_idle_scene: Callable[[Any], None]
    deactivate_component_host: Callable[[Any | None], bool]
    interrupt_running_agent: Callable[[Any | None], None]
    interrupt_current_task: Callable[[], None]
    signal_stop: Callable[[], None]
    thread_factory: Callable[..., Thread]


class EmbeddedAutonomousComponentRuntime:
    """Own child-component loop lifecycle while delegating CLI operations."""

    def __init__(self, ports: EmbeddedAutonomousRuntimePorts) -> None:
        self.ports = ports

    def start(self) -> bool:
        stop_event = self.ports.ensure_stop_event()
        stop_event.clear()
        component_host = self.ports.ensure_component_host()
        self.ports.set_component_active(component_host, True)

        thread = self.ports.get_component_thread()
        if thread is not None and thread.is_alive():
            return True

        executor_runtime = self.ports.build_executor_runtime(component_host)

        def get_pending_input() -> object | None:
            return self.ports.get_pending_input(component_host)

        thread = start_embedded_autonomous_component_loop(
            EmbeddedAutonomousLoopPorts(
                stop_event=stop_event,
                component_active=self.ports.parent_component_active,
                set_component_active=lambda active: self.ports.set_component_active(
                    component_host,
                    active,
                ),
                refresh_statuses=lambda: self.ports.refresh_statuses(component_host),
                can_poll_workflow=lambda: self.ports.can_poll_workflow(component_host),
                poll_workflow=executor_runtime.poll_workflow,
                get_pending_input=get_pending_input,
                execute_pending_input=lambda pending: self.ports.execute_pending_input(
                    component_host,
                    pending,
                ),
                invalidate=self.ports.invalidate,
                report_error=self.ports.report_error,
                publish_idle_scene=lambda: self.ports.publish_idle_scene(component_host),
            ),
            thread_factory=self.ports.thread_factory,
        )
        self.ports.store_component_thread(thread)
        return True

    def stop(self, *, interrupt: bool = False) -> None:
        component_host = self._current_component_host()
        stop_embedded_autonomous_component(
            EmbeddedAutonomousStopPorts(
                deactivate_component_host=lambda: self.ports.deactivate_component_host(
                    component_host,
                ),
                interrupt_running_agent=lambda: self.ports.interrupt_running_agent(
                    component_host,
                ),
                interrupt_current_task=self.ports.interrupt_current_task,
                signal_stop=self.ports.signal_stop,
            ),
            interrupt=interrupt,
        )

    def _current_component_host(self) -> Any | None:
        return self.ports.get_component_host()
