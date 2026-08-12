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
    prepared = []
    released = []

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
            prepare_body_worktree=lambda task_id, path, head: prepared.append(
                (task_id, path, head)
            ),
            release_task_environment=released.append,
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
    assert prepared == []
    assert released == ["session-1"]


def test_body_task_prepares_worktree_before_prompt_is_enqueued():
    task = {
        "task_id": "body-1",
        "execution_kind": "body_improvement",
        "constraints": {
            "worktree_path": "F:/body/slot-B/worktree",
            "target_slot_id": "slot-B",
        },
    }
    pending_input = Queue()
    calls = []

    runtime = AutonomousExecutorRuntime(
        AutonomousExecutorPorts(
            get_session_id=lambda: "autonomous-session-1",
            get_current_task=lambda: task,
            set_current_task=lambda _value: None,
            get_current_task_started_at=lambda: 0.0,
            set_current_task_started_at=lambda _value: None,
            set_current_task_run_id=lambda _value: None,
            get_last_agent_turn_result=lambda: None,
            set_last_agent_turn_result=lambda _value: None,
            enqueue_pending_input=lambda prompt: (
                calls.append("enqueue"),
                pending_input.put(prompt),
            ),
            agent_running=lambda: False,
            autonomous_gate_active=lambda: True,
            append_execution_event=lambda *_args, **_kwargs: None,
            prepare_body_worktree=lambda task_id, path, head: calls.append(
                ("prepare", task_id, path, head)
            ),
            release_task_environment=lambda _task_id: None,
        ),
        push_cli_agent_scene=lambda *args, **kwargs: None,
        git_head_commit=lambda _path: "baseline-head",
        git_improvement_diff=lambda _path, _head: None,
        cprint=lambda _message: None,
    )

    assert runtime.inject_execution_prompt(task, "body_improvement") is True
    assert calls[0] == (
        "prepare",
        "autonomous-session-1",
        "F:/body/slot-B/worktree",
        "baseline-head",
    )
    assert calls[1] == "enqueue"
    assert "/workspace" in pending_input.get_nowait()


def test_body_task_releases_environment_and_can_retry_when_enqueue_fails():
    task = {
        "execution_kind": "body_improvement",
        "constraints": {"worktree_path": "F:/body/slot-B/worktree"},
    }
    state = {"run_id": ""}
    released = []

    runtime = AutonomousExecutorRuntime(
        AutonomousExecutorPorts(
            get_session_id=lambda: "autonomous-session-2",
            get_current_task=lambda: task,
            set_current_task=lambda _value: None,
            get_current_task_started_at=lambda: 0.0,
            set_current_task_started_at=lambda _value: None,
            set_current_task_run_id=lambda value: state.__setitem__("run_id", value),
            get_last_agent_turn_result=lambda: None,
            set_last_agent_turn_result=lambda _value: None,
            enqueue_pending_input=lambda _prompt: (_ for _ in ()).throw(
                RuntimeError("queue unavailable")
            ),
            agent_running=lambda: False,
            autonomous_gate_active=lambda: True,
            append_execution_event=lambda *_args, **_kwargs: None,
            prepare_body_worktree=lambda _task_id, _path, _head: None,
            release_task_environment=released.append,
        ),
        push_cli_agent_scene=lambda *args, **kwargs: None,
        git_head_commit=lambda _path: "baseline-head",
        git_improvement_diff=lambda _path, _head: None,
        cprint=lambda _message: None,
    )

    assert runtime.inject_execution_prompt(task, "body_improvement") is False
    assert task.get("_autonomous_execution_started") is None
    assert task.get("_autonomous_task_run_id") is None
    assert state["run_id"] == ""
    assert released == ["autonomous-session-2"]
