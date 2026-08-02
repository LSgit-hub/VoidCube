from __future__ import annotations

from queue import Queue

from VoidCube_cli.autonomous_executor import (
    AutonomousExecutorPorts,
    AutonomousExecutorRuntime,
)


def test_executor_runtime_uses_explicit_state_ports_without_host_object():
    current_task = {"task_id": "task-1", "task_type": "self_learning"}
    pending_input = Queue()
    state = {
        "task": current_task,
        "started_at": 1.0,
        "run_id": "",
        "turn_result": None,
    }
    events = []

    runtime = AutonomousExecutorRuntime(
        AutonomousExecutorPorts(
            get_session_id=lambda: "session-1",
            get_current_task=lambda: state["task"],
            set_current_task=lambda value: state.__setitem__("task", value),
            get_current_task_started_at=lambda: state["started_at"],
            set_current_task_started_at=lambda value: state.__setitem__(
                "started_at", value
            ),
            set_current_task_run_id=lambda value: state.__setitem__("run_id", value),
            get_last_agent_turn_result=lambda: state["turn_result"],
            set_last_agent_turn_result=lambda value: state.__setitem__(
                "turn_result", value
            ),
            enqueue_pending_input=pending_input.put,
            agent_running=lambda: False,
            autonomous_gate_active=lambda: False,
            append_execution_event=lambda message, **kwargs: events.append(
                (message, kwargs)
            ),
        ),
        push_cli_agent_scene=lambda *args, **kwargs: None,
        git_head_commit=lambda _path: "head",
        git_improvement_diff=lambda _path, _head: None,
        cprint=lambda _message: None,
    )

    assert not hasattr(runtime, "host")
    assert runtime.inject_execution_prompt(current_task, "self_learning") is True
    assert pending_input.get_nowait().startswith("[Autonomous Learning Task]")
    assert state["run_id"]
    assert events[-1][1]["stage"] == "autonomous_execution_started"

    runtime.clear_current_task_state()

    assert state == {
        "task": None,
        "started_at": 0.0,
        "run_id": "",
        "turn_result": None,
    }
