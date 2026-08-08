from __future__ import annotations

from threading import Event
from types import SimpleNamespace

from VoidCube_app import autonomous_execution_runtime as runtime_module
from VoidCube_app.autonomous_execution_runtime import (
    AutonomousExecutionRuntime,
    AutonomousExecutionRuntimePorts,
)


def _ports(calls, *, host):
    stop_event = Event()
    stored_threads = []
    executor = SimpleNamespace(poll_workflow=lambda: calls.append("poll"))

    ports = AutonomousExecutionRuntimePorts(
        get_execution_host=lambda: host,
        ensure_execution_host=lambda: calls.append("ensure") or host,
        get_execution_thread=lambda: None,
        store_execution_thread=stored_threads.append,
        ensure_stop_event=lambda: stop_event,
        execution_active=lambda: True,
        set_execution_active=lambda _host, active: calls.append(("active", active)),
        build_executor_runtime=lambda _host: calls.append("runtime") or executor,
        refresh_statuses=lambda _host: calls.append("refresh"),
        can_poll_workflow=lambda _host: True,
        get_pending_input=lambda _host: None,
        execute_pending_input=lambda _host, _pending: calls.append("execute"),
        invalidate=lambda: calls.append("invalidate"),
        report_error=lambda error: calls.append(("error", str(error))),
        publish_idle_scene=lambda _host: calls.append("idle"),
        deactivate_execution_host=lambda _host: calls.append("deactivate") or True,
        interrupt_running_agent=lambda _host: calls.append("agent"),
        interrupt_current_task=lambda: calls.append("task"),
        signal_stop=lambda: calls.append("signal"),
        thread_factory=lambda **_kwargs: None,
    )
    return ports, stop_event, stored_threads


def test_execution_runtime_delegates_start_to_one_loop_owner(monkeypatch):
    calls = []
    host = object()
    ports, _stop_event, stored_threads = _ports(calls, host=host)
    captured = []

    class FakeThread:
        def is_alive(self):
            return False

    def start_loop(loop_ports, *, thread_factory):
        del thread_factory
        captured.append(loop_ports)
        calls.append("loop")
        return FakeThread()

    monkeypatch.setattr(
        runtime_module,
        "start_autonomous_execution_loop",
        start_loop,
    )

    assert AutonomousExecutionRuntime(ports).start() is True
    assert calls == ["ensure", ("active", True), "runtime", "loop"]
    assert len(stored_threads) == 1
    assert captured[0].poll_workflow is not None


def test_execution_runtime_stop_uses_existing_host_without_creating_one(monkeypatch):
    calls = []
    host = object()
    ports, _stop_event, _stored_threads = _ports(calls, host=host)
    monkeypatch.setattr(
        runtime_module,
        "stop_autonomous_execution",
        lambda stop_ports, *, interrupt: calls.append(
            ("stop", interrupt, stop_ports.deactivate_execution_host())
        ),
    )

    AutonomousExecutionRuntime(ports).stop(interrupt=True)

    assert calls == ["deactivate", ("stop", True, True)]
