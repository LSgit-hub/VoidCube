from __future__ import annotations

import json
import inspect
import threading
import time
from types import SimpleNamespace

import pytest

from agent.tool_execution import ToolExecutionCoordinator, ToolExecutionResult
from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.contracts.execution import ExecutionState
from run_agent import AIAgent
from VoidCube_app.tool_events import ToolEventKind


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


def _tool_call(call_id: str, name: str, arguments) -> SimpleNamespace:
    raw_arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=raw_arguments),
    )


def _coordinator(*, invoke, interrupted=lambda: False, delay=0, sleep=time.sleep):
    return ToolExecutionCoordinator(
        invoke=invoke,
        is_interrupted=interrupted,
        classify_failure=lambda _name, content: (content.startswith("Error"), ""),
        max_workers=4,
        delay=delay,
        sleep=sleep,
    )


def _agent() -> AIAgent:
    agent = AIAgent.__new__(AIAgent)
    agent._interrupt_requested = False
    agent._executing_tools = False
    agent._execution_thread_id = threading.current_thread().ident
    agent._tool_thread_ids = set()
    agent._tool_thread_ids_lock = threading.Lock()
    agent._active_children = []
    agent._active_children_lock = threading.Lock()
    agent._iters_since_skill = 2
    agent._current_tool = None
    agent._delegate_spinner = None
    agent._context_engine_tool_names = set()
    agent._memory_manager = None
    agent._session_db = None
    agent._todo_store = None
    agent._checkpoint_mgr = SimpleNamespace(enabled=False)
    agent._subdirectory_hints = SimpleNamespace(
        check_tool_call=lambda _name, _args: ""
    )
    agent.quiet_mode = True
    agent.verbose_logging = False
    agent.log_prefix_chars = 80
    agent.log_prefix = ""
    agent._print_fn = None
    agent.tool_event_sink = None
    agent.tool_delay = 0
    agent.session_id = "session-tools"
    agent.valid_tool_names = ["custom_tool", "read_file", "search_files"]
    agent.clarification_sink = None
    agent._current_main_runtime = lambda: {"provider": "safe"}
    agent._touch_activity = lambda _message: None
    agent._vprint = lambda *_args, **_kwargs: None
    agent._safe_print = lambda *_args, **_kwargs: None
    agent._should_start_quiet_spinner = lambda: False
    return agent


def test_prepare_preserves_calls_and_normalizes_invalid_arguments():
    calls = ToolExecutionCoordinator.prepare(
        [
            _tool_call("call-1", "read_file", {"path": "README.md"}),
            _tool_call("call-2", "search_files", "not-json"),
            _tool_call("call-3", "search_files", "[]"),
        ]
    )

    assert [(call.position, call.call_id, call.name) for call in calls] == [
        (1, "call-1", "read_file"),
        (2, "call-2", "search_files"),
        (3, "call-3", "search_files"),
    ]
    assert calls[0].arguments == {"path": "README.md"}
    assert calls[1].arguments == {}
    assert calls[2].arguments == {}


def test_parallel_execution_emits_outcomes_in_original_call_order():
    release_first = threading.Event()
    second_finished = threading.Event()

    def invoke(call):
        if call.call_id == "call-1":
            assert release_first.wait(timeout=2)
        else:
            second_finished.set()
            release_first.set()
        return call.call_id

    coordinator = _coordinator(invoke=invoke)
    calls = coordinator.prepare(
        [
            _tool_call("call-1", "read_file", {"path": "a"}),
            _tool_call("call-2", "read_file", {"path": "b"}),
        ]
    )
    completed: list[str] = []

    outcomes = coordinator.execute(
        calls,
        parallel=True,
        after_call=lambda outcome: completed.append(outcome.call.call_id),
    )

    assert second_finished.is_set()
    assert [outcome.content for outcome in outcomes] == ["call-1", "call-2"]
    assert completed == ["call-1", "call-2"]


def test_sequential_interrupt_after_first_call_completes_remaining_protocol_slots():
    interrupted = False
    invoked: list[str] = []

    def invoke(call):
        nonlocal interrupted
        invoked.append(call.call_id)
        interrupted = True
        return "done"

    coordinator = _coordinator(invoke=invoke, interrupted=lambda: interrupted)
    calls = coordinator.prepare(
        [
            _tool_call("call-1", "first", {}),
            _tool_call("call-2", "second", {}),
            _tool_call("call-3", "third", {}),
        ]
    )

    outcomes = coordinator.execute(calls, parallel=False)

    assert invoked == ["call-1"]
    assert len(outcomes) == 3
    assert outcomes[0].skipped is False
    assert [outcome.skip_reason for outcome in outcomes[1:]] == [
        "after_call",
        "after_call",
    ]
    assert "not started" in outcomes[1].content


def test_preexisting_interrupt_skips_every_call_without_invocation():
    coordinator = _coordinator(
        invoke=lambda _call: pytest.fail("tool should not run"),
        interrupted=lambda: True,
    )
    calls = coordinator.prepare([_tool_call("call-1", "read_file", {})])

    outcomes = coordinator.execute(calls, parallel=True)

    assert len(outcomes) == 1
    assert outcomes[0].skip_reason == "before_batch"
    assert "cancelled" in outcomes[0].content


def test_invocation_exception_becomes_an_error_outcome():
    def fail(_call):
        raise RuntimeError("backend unavailable")

    coordinator = _coordinator(invoke=fail)
    calls = coordinator.prepare([_tool_call("call-1", "web_search", {})])

    outcome = coordinator.execute(calls, parallel=False)[0]

    assert outcome.state is ExecutionState.FAILED
    assert outcome.content == "Error executing tool 'web_search': backend unavailable"


def test_structured_tool_result_preserves_artifacts_in_outcome():
    artifact = Artifact(
        kind="image",
        uri="C:/tmp/screenshot.png",
        mime_type="image/png",
    )
    coordinator = _coordinator(
        invoke=lambda _call: ToolExecutionResult(
            content='{"success": true}',
            artifacts=(artifact,),
        )
    )
    calls = coordinator.prepare([_tool_call("call-1", "browser_vision", {})])

    outcome = coordinator.execute(calls, parallel=False)[0]

    assert outcome.content == '{"success": true}'
    assert outcome.artifacts == (artifact,)


@pytest.mark.parametrize(
    "content",
    [
        '{"success": false}',
        '{"error": "denied"}',
        '{"exit_code": 2}',
        '{"status": "blocked"}',
    ],
)
def test_structured_failure_payloads_share_one_failed_outcome(content):
    coordinator = _coordinator(invoke=lambda _call: content)
    call = coordinator.prepare([_tool_call("call-failed", "tool", {})])

    outcome = coordinator.execute(call, parallel=False)[0]

    assert outcome.state is ExecutionState.FAILED
    assert outcome.failed is True


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"status": "cancelled"}', ExecutionState.CANCELLED),
        ('{"status": "timed_out"}', ExecutionState.TIMED_OUT),
        ('{"status": "unknown"}', ExecutionState.UNKNOWN),
    ],
)
def test_structured_terminal_payload_preserves_non_failure_state(content, expected):
    coordinator = _coordinator(invoke=lambda _call: content)
    call = coordinator.prepare([_tool_call("call-state", "tool", {})])

    outcome = coordinator.execute(call, parallel=False)[0]

    assert outcome.state is expected


def test_sequential_delay_runs_only_between_started_calls():
    sleeps: list[float] = []
    coordinator = _coordinator(
        invoke=lambda call: call.name,
        delay=0.25,
        sleep=sleeps.append,
    )
    calls = coordinator.prepare(
        [
            _tool_call("call-1", "first", {}),
            _tool_call("call-2", "second", {}),
        ]
    )

    coordinator.execute(calls, parallel=False)

    assert sleeps == [0.25]


def test_agent_canonical_path_routes_callbacks_persistence_and_hints(monkeypatch):
    import run_agent

    events = []
    routed: list[dict] = []
    budgeted: list[list[dict]] = []
    agent = _agent()
    agent.tool_event_sink = events.append
    agent._subdirectory_hints = SimpleNamespace(
        check_tool_call=lambda name, _args: f"\n[hint:{name}]"
    )

    def route(name, args, task_id, **kwargs):
        routed.append(
            {"name": name, "args": args, "task_id": task_id, **kwargs}
        )
        return "raw-result"

    monkeypatch.setattr(run_agent, "handle_function_call", route)
    monkeypatch.setattr(
        run_agent,
        "maybe_persist_tool_result",
        lambda **kwargs: kwargs["content"] + "\n[persisted]",
    )
    monkeypatch.setattr(run_agent, "get_active_env", lambda task_id: f"env:{task_id}")
    monkeypatch.setattr(
        run_agent,
        "enforce_turn_budget",
        lambda messages, env=None: budgeted.append(list(messages)),
    )
    assistant = SimpleNamespace(
        tool_calls=[_tool_call("call-1", "custom_tool", {"value": 3})]
    )
    messages: list[dict] = []

    agent._execute_tool_calls(assistant, messages, "task-1")

    assert routed == [
        {
            "name": "custom_tool",
            "args": {"value": 3},
            "task_id": "task-1",
            "tool_call_id": "call-1",
            "session_id": "session-tools",
            "enabled_tools": ["custom_tool", "read_file", "search_files"],
            "main_runtime": {"provider": "safe"},
        }
    ]
    assert [event.kind for event in events] == [
        ToolEventKind.STARTED,
        ToolEventKind.SUCCEEDED,
    ]
    assert [event.call_id for event in events] == ["call-1", "call-1"]
    assert events[0].arguments == {"value": 3}
    assert events[1].result == "raw-result"
    assert events[1].state is ExecutionState.SUCCEEDED
    assert messages == [
        {
            "role": "tool",
            "content": "raw-result\n[persisted]\n[hint:custom_tool]",
            "tool_call_id": "call-1",
        }
    ]
    assert budgeted == [messages]


def test_agent_tool_message_binds_action_ref_from_journal(monkeypatch):
    import run_agent
    import agent.action_journal as action_journal_module

    agent = _agent()
    monkeypatch.setattr(
        run_agent,
        "handle_function_call",
        lambda *_args, **_kwargs: "created",
    )
    monkeypatch.setattr(
        run_agent,
        "maybe_persist_tool_result",
        lambda **kwargs: kwargs["content"],
    )
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda *_args, **_kwargs: None)

    class _ActionRef:
        def as_dict(self):
            return {
                "action_id": "act-bound",
                "state": "succeeded",
                "target_summary": "resource-1",
                "evidence_refs": [],
            }

    class _Journal:
        def find_by_call_id(self, call_id, *, task_id=None):
            assert (call_id, task_id) == ("call-write", "task-write")
            return _ActionRef()

    monkeypatch.setattr(action_journal_module, "get_action_journal", lambda: _Journal())
    messages: list[dict] = []
    agent._execute_tool_calls(
        SimpleNamespace(
            tool_calls=[_tool_call("call-write", "custom_tool", {"value": 1})]
        ),
        messages,
        "task-write",
    )

    assert messages[0]["action_refs"] == [
        {
            "action_id": "act-bound",
            "state": "succeeded",
            "target_summary": "resource-1",
            "evidence_refs": [],
        }
    ]


def test_agent_canonical_path_keeps_structured_artifacts_on_tool_event(monkeypatch):
    import run_agent

    artifact = Artifact(
        kind="image",
        uri="C:/tmp/screenshot.png",
        mime_type="image/png",
    )
    events = []
    agent = _agent()
    agent.tool_event_sink = events.append

    monkeypatch.setattr(
        run_agent,
        "handle_function_call",
        lambda *_args, **_kwargs: ToolExecutionResult(
            content='{"success": true}',
            artifacts=(artifact,),
        ),
    )
    monkeypatch.setattr(
        run_agent,
        "maybe_persist_tool_result",
        lambda **kwargs: kwargs["content"],
    )
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda *_args, **_kwargs: None)

    assistant = SimpleNamespace(
        tool_calls=[_tool_call("call-1", "browser_vision", {})]
    )
    messages: list[dict] = []

    agent._execute_tool_calls(assistant, messages, "task-artifact")

    assert events[1].artifacts == (artifact,)
    assert messages[0]["content"] == '{"success": true}'


def test_agent_clarify_route_passes_options_to_shared_sink() -> None:
    from VoidCube_app.interaction_contract import (
        ClarificationDecision,
        ClarificationStatus,
    )

    agent = _agent()
    requests = []
    agent.clarification_sink = lambda request: (
        requests.append(request)
        or ClarificationDecision(
            ClarificationStatus.ANSWERED,
            answer="staging",
        )
    )
    call = SimpleNamespace(
        name="clarify",
        arguments={
            "question": "Which environment?",
            "options": ["staging", "production"],
        },
        call_id="clarify-1",
    )

    result = json.loads(
        agent._route_tool_call(call, messages=[], effective_task_id="task-clarify")
    )

    assert requests[0].question == "Which environment?"
    assert requests[0].options == ("staging", "production")
    assert result["status"] == "answered"
    assert result["answer"] == "staging"


def test_agent_parallel_path_writes_results_in_assistant_order(monkeypatch):
    import run_agent

    agent = _agent()
    events = []
    agent.tool_event_sink = events.append
    release_first = threading.Event()
    second_finished = threading.Event()

    def route(_name, args, _task_id, **_kwargs):
        if args["path"] == "a.txt":
            assert release_first.wait(timeout=2)
        else:
            second_finished.set()
            release_first.set()
        return args["path"]

    monkeypatch.setattr(run_agent, "handle_function_call", route)
    monkeypatch.setattr(
        run_agent,
        "maybe_persist_tool_result",
        lambda **kwargs: kwargs["content"],
    )
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda *_args, **_kwargs: None)
    assistant = SimpleNamespace(
        tool_calls=[
            _tool_call("call-1", "read_file", {"path": "a.txt"}),
            _tool_call("call-2", "read_file", {"path": "b.txt"}),
        ]
    )
    messages: list[dict] = []

    agent._execute_tool_calls(assistant, messages, "task-2")

    assert second_finished.is_set()
    assert [message["tool_call_id"] for message in messages] == [
        "call-1",
        "call-2",
    ]
    assert [message["content"] for message in messages] == ["a.txt", "b.txt"]
    assert [(event.kind, event.call_id) for event in events] == [
        (ToolEventKind.STARTED, "call-1"),
        (ToolEventKind.STARTED, "call-2"),
        (ToolEventKind.SUCCEEDED, "call-1"),
        (ToolEventKind.SUCCEEDED, "call-2"),
    ]


def test_tool_event_sink_failure_does_not_interrupt_execution(monkeypatch):
    import run_agent

    agent = _agent()
    agent.tool_event_sink = lambda _event: (_ for _ in ()).throw(
        RuntimeError("renderer stopped")
    )
    monkeypatch.setattr(run_agent, "handle_function_call", lambda *_args, **_kwargs: "ok")
    monkeypatch.setattr(
        run_agent,
        "maybe_persist_tool_result",
        lambda **kwargs: kwargs["content"],
    )
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda *_args, **_kwargs: None)
    assistant = SimpleNamespace(
        tool_calls=[_tool_call("call-1", "read_file", {"path": "README.md"})]
    )
    messages = []

    agent._execute_tool_calls(assistant, messages, "task-sink-failure")

    assert messages == [
        {"role": "tool", "content": "ok", "tool_call_id": "call-1"}
    ]


def test_agent_interrupt_still_completes_every_tool_protocol_slot(monkeypatch):
    import run_agent

    agent = _agent()
    agent._interrupt_requested = True
    events = []
    agent.tool_event_sink = events.append
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda *_args, **_kwargs: None)
    assistant = SimpleNamespace(
        tool_calls=[
            _tool_call("call-1", "read_file", {"path": "a.txt"}),
            _tool_call("call-2", "read_file", {"path": "b.txt"}),
        ]
    )
    messages: list[dict] = []

    agent._execute_tool_calls(assistant, messages, "task-interrupt")

    assert [event.kind for event in events] == [
        ToolEventKind.CANCELLED,
        ToolEventKind.CANCELLED,
    ]
    assert [message["tool_call_id"] for message in messages] == [
        "call-1",
        "call-2",
    ]
    assert all("cancelled" in message["content"] for message in messages)


def test_parallel_tool_workers_receive_agent_interrupt(monkeypatch):
    import run_agent
    from tools.interrupt import is_interrupted

    agent = _agent()
    workers_started = threading.Barrier(3)

    def route(_name, _args, _task_id, **_kwargs):
        workers_started.wait(timeout=2)
        deadline = time.monotonic() + 2
        while not is_interrupted() and time.monotonic() < deadline:
            time.sleep(0.01)
        return "interrupted" if is_interrupted() else "timed out"

    monkeypatch.setattr(run_agent, "handle_function_call", route)
    monkeypatch.setattr(
        run_agent,
        "maybe_persist_tool_result",
        lambda **kwargs: kwargs["content"],
    )
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda *_args, **_kwargs: None)
    assistant = SimpleNamespace(
        tool_calls=[
            _tool_call("call-1", "read_file", {"path": "a.txt"}),
            _tool_call("call-2", "read_file", {"path": "b.txt"}),
        ]
    )
    messages: list[dict] = []

    execution = threading.Thread(
        target=agent._execute_tool_calls,
        args=(assistant, messages, "task-parallel-interrupt"),
    )
    execution.start()
    workers_started.wait(timeout=2)
    agent.interrupt("new input")
    execution.join(timeout=2)
    agent.clear_interrupt()

    assert not execution.is_alive()
    assert [message["content"] for message in messages] == [
        "interrupted",
        "interrupted",
    ]
    assert agent._tool_thread_ids == set()


def test_tool_worker_inherits_interrupt_that_arrived_before_registration():
    from tools.interrupt import is_interrupted

    agent = _agent()
    agent.interrupt("new input")
    observed: list[bool] = []

    def invoke_in_worker():
        thread_id = agent._register_tool_thread()
        try:
            observed.append(is_interrupted())
        finally:
            agent._unregister_tool_thread(thread_id)
        observed.append(is_interrupted())

    worker = threading.Thread(target=invoke_in_worker)
    worker.start()
    worker.join(timeout=2)
    agent.clear_interrupt()

    assert observed == [True, False]
    assert agent._tool_thread_ids == set()


def test_agent_constructor_exposes_only_structured_tool_event_port() -> None:
    parameters = inspect.signature(AIAgent.__init__).parameters

    assert "tool_event_sink" in parameters
    assert "tool_progress_callback" not in parameters
    assert "tool_start_callback" not in parameters
    assert "tool_complete_callback" not in parameters
