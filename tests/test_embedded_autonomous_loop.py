from __future__ import annotations

from VoidCube_app.autonomous_component_runtime import (
    AutonomousComponentLoopPorts,
    run_autonomous_component_loop,
    start_autonomous_component_loop,
)


class _OneCycleStopEvent:
    def __init__(self) -> None:
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, _timeout: float) -> bool:
        self.stopped = True
        return True


def test_component_loop_polls_and_executes_pending_input_without_owning_host() -> None:
    stop_event = _OneCycleStopEvent()
    calls: list[object] = []
    pending = ["autonomous prompt"]

    ports = AutonomousComponentLoopPorts(
        stop_event=stop_event,
        execution_active=lambda: True,
        set_execution_active=lambda active: calls.append(("active", active)),
        refresh_statuses=lambda: calls.append("refresh"),
        can_poll_workflow=lambda: True,
        poll_workflow=lambda: calls.append("poll"),
        get_pending_input=lambda: pending.pop() if pending else None,
        execute_pending_input=lambda prompt: calls.append(("execute", prompt)),
        invalidate=lambda: calls.append("invalidate"),
        report_error=lambda error: calls.append(("error", str(error))),
        publish_idle_scene=lambda: calls.append("idle"),
    )

    run_autonomous_component_loop(ports)

    assert calls == [
        ("active", True),
        "refresh",
        "poll",
        ("execute", "autonomous prompt"),
        "poll",
        "invalidate",
        ("active", False),
        "idle",
    ]


def test_component_loop_reports_errors_then_releases_component_state() -> None:
    stop_event = _OneCycleStopEvent()
    calls: list[object] = []
    ports = AutonomousComponentLoopPorts(
        stop_event=stop_event,
        execution_active=lambda: True,
        set_execution_active=lambda active: calls.append(("active", active)),
        refresh_statuses=lambda: (_ for _ in ()).throw(RuntimeError("refresh failed")),
        can_poll_workflow=lambda: True,
        poll_workflow=lambda: None,
        get_pending_input=lambda: None,
        execute_pending_input=lambda prompt: None,
        invalidate=lambda: calls.append("invalidate"),
        report_error=lambda error: calls.append(("error", str(error))),
        publish_idle_scene=lambda: calls.append("idle"),
    )

    run_autonomous_component_loop(ports)

    assert calls == [
        ("active", True),
        ("error", "refresh failed"),
        "invalidate",
        ("active", False),
        "idle",
    ]


def test_start_component_loop_builds_a_named_daemon_thread() -> None:
    captured: dict[str, object] = {}

    class _Thread:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def start(self) -> None:
            captured["started"] = True

    ports = AutonomousComponentLoopPorts(
        stop_event=_OneCycleStopEvent(),
        execution_active=lambda: False,
        set_execution_active=lambda active: None,
        refresh_statuses=lambda: None,
        can_poll_workflow=lambda: False,
        poll_workflow=lambda: None,
        get_pending_input=lambda: None,
        execute_pending_input=lambda prompt: None,
        invalidate=lambda: None,
        report_error=lambda error: None,
        publish_idle_scene=lambda: None,
    )

    start_autonomous_component_loop(ports, thread_factory=_Thread)

    assert captured["daemon"] is True
    assert captured["name"] == "autonomous-execution-component"
    assert captured["started"] is True
