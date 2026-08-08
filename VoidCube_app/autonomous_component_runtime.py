"""UI-independent lifecycle for an autonomous execution component."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, Protocol


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


@dataclass(frozen=True, slots=True)
class AutonomousComponentLoopPorts:
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


def run_autonomous_component_loop(ports: AutonomousComponentLoopPorts) -> None:
    while not ports.stop_event.is_set() and ports.component_active():
        try:
            ports.set_component_active(True)
            ports.refresh_statuses()
            if ports.can_poll_workflow():
                ports.poll_workflow()
                pending_input = ports.get_pending_input()
                if pending_input:
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


def start_autonomous_component_loop(
    ports: AutonomousComponentLoopPorts,
    *,
    thread_factory: Callable[..., Thread] = Thread,
) -> Thread:
    thread = thread_factory(
        target=lambda: run_autonomous_component_loop(ports),
        daemon=True,
        name="autonomous-execution-component",
    )
    thread.start()
    return thread


@dataclass(frozen=True, slots=True)
class AutonomousComponentStopPorts:
    deactivate_component_host: Callable[[], bool]
    interrupt_running_agent: Callable[[], None]
    interrupt_current_task: Callable[[], None]
    signal_stop: Callable[[], None]


def stop_autonomous_component(
    ports: AutonomousComponentStopPorts,
    *,
    interrupt: bool = False,
) -> None:
    component_present = ports.deactivate_component_host()
    if component_present and interrupt:
        ports.interrupt_running_agent()
        ports.interrupt_current_task()
    ports.signal_stop()


@dataclass(frozen=True, slots=True)
class AutonomousComponentRuntimePorts:
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


class AutonomousComponentRuntime:
    def __init__(self, ports: AutonomousComponentRuntimePorts) -> None:
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
        thread = start_autonomous_component_loop(
            AutonomousComponentLoopPorts(
                stop_event=stop_event,
                component_active=self.ports.parent_component_active,
                set_component_active=lambda active: self.ports.set_component_active(
                    component_host,
                    active,
                ),
                refresh_statuses=lambda: self.ports.refresh_statuses(component_host),
                can_poll_workflow=lambda: self.ports.can_poll_workflow(component_host),
                poll_workflow=executor_runtime.poll_workflow,
                get_pending_input=lambda: self.ports.get_pending_input(component_host),
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
        component_host = self.ports.get_component_host()
        stop_autonomous_component(
            AutonomousComponentStopPorts(
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


__all__ = [
    "AutonomousComponentLoopPorts",
    "AutonomousComponentRuntime",
    "AutonomousComponentRuntimePorts",
    "AutonomousComponentStopPorts",
    "run_autonomous_component_loop",
    "start_autonomous_component_loop",
    "stop_autonomous_component",
]
