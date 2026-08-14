from __future__ import annotations

from queue import Queue
import json
from pathlib import Path

from VoidCube_cli.autonomous_executor import (
    AutonomousExecutorPorts,
    AutonomousExecutorRuntime,
)
from systems.evolution_evaluation import (
    ExecutionEnvironmentManifest,
    capture_host_environment_manifest,
)


_HOST_ENVIRONMENT = capture_host_environment_manifest(
    Path(__file__).parents[1],
    repository_head="b" * 40,
)
_BODY_ENVIRONMENT_PAYLOAD = _HOST_ENVIRONMENT.content_payload()
_BODY_ENVIRONMENT_PAYLOAD.update(
    backend="podman",
    validation_scope="container",
    execution_os="Linux 6.8",
    architecture="x86_64",
    execution_workspace_path="/workspace",
    path_mappings=(
        {
            "host_path": _HOST_ENVIRONMENT.host_workspace_path,
            "execution_path": "/workspace",
        },
    ),
    validated_platforms=("linux",),
)
_BODY_ENVIRONMENT = ExecutionEnvironmentManifest.create(
    **_BODY_ENVIRONMENT_PAYLOAD
).model_dump(mode="json")


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
            "experiment_result_id": "experiment-result-" + "1" * 64,
            "evaluated_baseline_commit": "b" * 40,
            "evaluated_candidate_commit": "a" * 40,
            "must_not_create_new_commit": True,
            "must_match_evaluated_commit": True,
            "requires_governor_review": True,
            "requires_user_consent": True,
            "execution_environment_id": "execution-environment-" + "e" * 64,
            "validation_scope": "host",
            "validated_platforms": ["windows"],
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
            prepare_body_worktree=lambda task_id, path, head: (
                calls.append(("prepare", task_id, path, head)),
                _BODY_ENVIRONMENT,
            )[1],
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
        "b" * 40,
    )
    assert calls[1] == "enqueue"
    prompt = pending_input.get_nowait()
    assert "/workspace" in prompt
    assert "do not claim that Windows-host tests passed" in prompt
    assert task["_execution_environment"]["validation_scope"] == "container"


def test_body_task_releases_environment_and_can_retry_when_enqueue_fails():
    task = {
        "execution_kind": "body_improvement",
        "constraints": {
            "worktree_path": "F:/body/slot-B/worktree",
            "experiment_result_id": "experiment-result-" + "1" * 64,
            "evaluated_baseline_commit": "b" * 40,
            "evaluated_candidate_commit": "a" * 40,
            "must_not_create_new_commit": True,
            "must_match_evaluated_commit": True,
            "requires_governor_review": True,
            "requires_user_consent": True,
            "execution_environment_id": "execution-environment-" + "e" * 64,
            "validation_scope": "host",
            "validated_platforms": ["windows"],
        },
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
            prepare_body_worktree=lambda _task_id, _path, _head: _BODY_ENVIRONMENT,
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


def test_current_task_lease_heartbeat_updates_persisted_task(monkeypatch):
    current = {
        "task_id": "task-heartbeat",
        "execution_lease": {
            "generation": 2,
            "attempt_id": "attempt-2",
            "state": "active",
        },
    }
    state = {"task": current}
    requests = []

    class Response:
        def read(self):
            return json.dumps(
                {
                    "task": {
                        **current,
                        "execution_lease": {
                            **current["execution_lease"],
                            "heartbeat_at": "2026-08-13T00:00:00+00:00",
                        },
                    }
                }
            ).encode()

    def urlopen(request, timeout=0):
        requests.append(json.loads(request.data.decode()))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    runtime = AutonomousExecutorRuntime(
        AutonomousExecutorPorts(
            get_session_id=lambda: "session-1",
            get_current_task=lambda: state["task"],
            set_current_task=lambda value: state.__setitem__("task", value),
            get_current_task_started_at=lambda: 0.0,
            set_current_task_started_at=lambda _value: None,
            set_current_task_run_id=lambda _value: None,
            get_last_agent_turn_result=lambda: None,
            set_last_agent_turn_result=lambda _value: None,
            enqueue_pending_input=lambda _value: None,
            agent_running=lambda: True,
            autonomous_gate_active=lambda: True,
            append_execution_event=lambda *_args, **_kwargs: None,
            prepare_body_worktree=lambda *_args: None,
            release_task_environment=lambda _value: None,
        ),
        push_cli_agent_scene=lambda *args, **kwargs: None,
        git_head_commit=lambda _path: "",
        git_improvement_diff=lambda _path, _head: None,
        cprint=lambda _message: None,
    )

    assert runtime.renew_current_task_lease_if_due(now=100.0) is True
    assert requests[0]["decision"] == "running"
    assert requests[0]["execution_lease"]["attempt_id"] == "attempt-2"
    assert state["task"]["execution_lease"]["heartbeat_at"]
    assert runtime.renew_current_task_lease_if_due(now=120.0) is True
    assert len(requests) == 1
