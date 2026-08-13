from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_agent
from tools import delegate_tool
from VoidCube_app.contracts.execution import ExecutionState
from VoidCube_app.tool_events import ToolEvent, ToolEventKind


def _build_minimal_agent(*, print_fn):
    agent = run_agent.AIAgent.__new__(run_agent.AIAgent)
    agent._interrupt_requested = False
    agent._execution_thread_id = threading.current_thread().ident
    agent._tool_thread_ids = set()
    agent._tool_thread_ids_lock = threading.Lock()
    agent.quiet_mode = True
    agent.verbose_logging = False
    agent.log_prefix_chars = 80
    agent.log_prefix = ""
    agent._current_tool = None
    agent._touch_activity = lambda *_args, **_kwargs: None
    agent.tool_event_sink = None
    agent._checkpoint_mgr = SimpleNamespace(enabled=False)
    agent._should_emit_quiet_tool_messages = lambda: True
    agent._should_start_quiet_spinner = lambda: True
    agent._print_fn = print_fn
    agent._delegate_spinner = None
    agent._context_engine_tool_names = set()
    agent._memory_manager = None
    agent.valid_tool_names = []
    agent.session_id = "session-delegate"
    agent._current_main_runtime = lambda: {}
    agent._subdirectory_hints = SimpleNamespace(check_tool_call=lambda *_args, **_kwargs: "")
    agent.tool_delay = 0
    agent._vprint = lambda *_args, **_kwargs: None
    return agent


def _build_delegate_tool_call():
    return SimpleNamespace(
        id="tool-1",
        function=SimpleNamespace(
            name="delegate_task",
            arguments=json.dumps({"goal": "inspect codebase"}),
        ),
    )


def _patch_common_runtime_helpers(monkeypatch):
    monkeypatch.setattr(run_agent, "maybe_persist_tool_result", lambda **kwargs: kwargs["content"])
    monkeypatch.setattr(run_agent, "get_active_env", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "enforce_turn_budget", lambda _messages, env=None: None)
    monkeypatch.setattr(run_agent, "_detect_tool_failure", lambda _tool_name, _result: (False, None))
    monkeypatch.setattr(run_agent, "_get_cute_tool_message_impl", lambda *_args, **_kwargs: "delegate finished")


@pytest.mark.unit
def test_delegate_task_enables_rich_display_when_cli_print_fn_exists(monkeypatch):
    _patch_common_runtime_helpers(monkeypatch)

    captured = {}

    def fake_delegate_task(**kwargs):
        captured.update(kwargs)
        return json.dumps({"success": True})

    class _FakeSpinner:
        KAWAII_WAITING = ["(._.)"]

        def __init__(self, *_args, **_kwargs):
            raise AssertionError("legacy spinner should not start when rich display is enabled")

    monkeypatch.setattr("tools.delegate_tool.delegate_task", fake_delegate_task)
    monkeypatch.setattr(run_agent, "KawaiiSpinner", _FakeSpinner)

    agent = _build_minimal_agent(print_fn=lambda *_args, **_kwargs: None)
    assistant_message = SimpleNamespace(tool_calls=[_build_delegate_tool_call()])
    messages: list[dict] = []

    agent._execute_tool_calls(assistant_message, messages, "task-1")

    assert captured["enable_display"] is True
    assert captured["parent_agent"] is agent
    assert messages[-1]["tool_call_id"] == "tool-1"


@pytest.mark.unit
def test_delegate_call_forwards_declared_execution_overrides(monkeypatch):
    _patch_common_runtime_helpers(monkeypatch)
    captured = {}

    def fake_delegate_task(**kwargs):
        captured.update(kwargs)
        return json.dumps({"results": []})

    monkeypatch.setattr("tools.delegate_tool.delegate_task", fake_delegate_task)
    agent = _build_minimal_agent(print_fn=lambda *_args, **_kwargs: None)
    call = _build_delegate_tool_call()
    call.function.arguments = json.dumps(
        {
            "goal": "inspect codebase",
            "worktree_path": "F:/repo",
            "acp_command": "agent-command",
            "acp_args": ["--stdio"],
        }
    )

    agent._execute_tool_calls(
        SimpleNamespace(tool_calls=[call]),
        [],
        "task-overrides",
    )

    assert captured["worktree_path"] == "F:/repo"
    assert captured["acp_command"] == "agent-command"
    assert captured["acp_args"] == ["--stdio"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"results": [{"status": "completed"}]}, ExecutionState.SUCCEEDED),
        ({"results": [{"status": "failed"}]}, ExecutionState.FAILED),
        ({"results": [{"status": "timed_out"}]}, ExecutionState.TIMED_OUT),
        (
            {"results": [{"status": "completed"}, {"status": "interrupted"}]},
            ExecutionState.FAILED,
        ),
        ({"results": [{"status": "interrupted"}]}, ExecutionState.CANCELLED),
        ({"success": False, "error": "invalid worktree"}, ExecutionState.FAILED),
    ],
)
def test_delegate_result_state_aggregates_child_outcomes(payload, expected):
    assert run_agent.AIAgent._delegation_result_state(json.dumps(payload)) is expected


@pytest.mark.unit
def test_delegate_task_falls_back_to_legacy_spinner_without_cli_print_fn(monkeypatch):
    _patch_common_runtime_helpers(monkeypatch)

    captured = {}
    spinner_events: list[str] = []

    def fake_delegate_task(**kwargs):
        captured.update(kwargs)
        return json.dumps({"success": True})

    class _FakeSpinner:
        KAWAII_WAITING = ["(._.)"]

        def __init__(self, *_args, **_kwargs):
            spinner_events.append("init")

        def start(self):
            spinner_events.append("start")

        def stop(self, final_message=None):
            spinner_events.append(f"stop:{final_message}")

    monkeypatch.setattr("tools.delegate_tool.delegate_task", fake_delegate_task)
    monkeypatch.setattr(run_agent, "KawaiiSpinner", _FakeSpinner)

    agent = _build_minimal_agent(print_fn=None)
    assistant_message = SimpleNamespace(tool_calls=[_build_delegate_tool_call()])
    messages: list[dict] = []

    agent._execute_tool_calls(assistant_message, messages, "task-2")

    assert captured["enable_display"] is False
    assert spinner_events == ["init", "start", "stop:delegate finished"]
    assert messages[-1]["tool_call_id"] == "tool-1"


@pytest.mark.unit
def test_delegate_task_validates_and_binds_declared_worktree(monkeypatch, tmp_path):
    worktree = tmp_path / "slot-worktree"
    worktree.mkdir()
    captured: dict = {}

    monkeypatch.setattr(delegate_tool, "_load_config", lambda: {"max_iterations": 3})
    monkeypatch.setattr(delegate_tool, "_get_max_concurrent_children", lambda: 1)
    monkeypatch.setattr(
        delegate_tool,
        "_resolve_delegation_credentials",
        lambda _cfg, _parent: {
            "model": None,
            "provider": None,
            "base_url": None,
            "api_key": None,
        },
    )

    def fake_build_child_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(delegate_tool, "_build_child_agent", fake_build_child_agent)
    monkeypatch.setattr(
        delegate_tool,
        "_run_single_child",
        lambda task_index, goal, child=None, parent_agent=None: {
            "task_index": task_index,
            "status": "completed",
            "summary": "done",
            "duration_seconds": 0,
        },
    )

    parent = _build_minimal_agent(print_fn=None)
    result = json.loads(
        delegate_tool.delegate_task(
            goal="inspect isolated slot",
            parent_agent=parent,
            enable_display=False,
            worktree_path=str(worktree),
        )
    )

    assert result["results"][0]["status"] == "completed"
    assert captured["worktree_path"] == str(worktree.resolve())


@pytest.mark.unit
def test_delegate_task_rejects_missing_declared_worktree(tmp_path):
    parent = _build_minimal_agent(print_fn=None)

    result = json.loads(
        delegate_tool.delegate_task(
            goal="inspect isolated slot",
            parent_agent=parent,
            enable_display=False,
            worktree_path=str(tmp_path / "missing"),
        )
    )

    assert result["error"]
    assert "worktree_path does not exist" in result["error"]


@pytest.mark.unit
def test_child_event_sink_batches_started_tools_for_parent() -> None:
    parent_events = []
    parent = SimpleNamespace(tool_event_sink=parent_events.append, _delegate_spinner=None)
    sink = delegate_tool._build_child_event_sink(0, parent)

    for index in range(5):
        sink(
            ToolEvent.started(
                call_id=f"call-{index}",
                name=f"tool-{index}",
                arguments={},
            )
        )

    assert len(parent_events) == 1
    assert parent_events[0].kind is ToolEventKind.SUBAGENT_PROGRESS
    assert parent_events[0].text == (
        "🔀 tool-0, tool-1, tool-2, tool-3, tool-4"
    )


@pytest.mark.unit
def test_rich_subagent_sink_maps_structured_events() -> None:
    calls = []
    manager = SimpleNamespace(
        on_thinking=lambda *args: calls.append(("thinking", args)),
        on_tool_start=lambda *args, **kwargs: calls.append(
            ("started", args, kwargs)
        ),
        on_tool_complete=lambda *args, **kwargs: calls.append(
            ("completed", args, kwargs)
        ),
        get_task=lambda _task_id: SimpleNamespace(iteration=3),
    )
    sink = delegate_tool._build_subagent_display_sink("task-1", 0, manager)

    sink(ToolEvent.reasoning("checking"))
    sink(
        ToolEvent.started(
            call_id="call-1",
            name="read_file",
            arguments={"path": "README.md"},
            preview="README.md",
        )
    )
    sink(
        ToolEvent.terminal(
            call_id="call-1",
            name="read_file",
            arguments={"path": "README.md"},
            result="failed",
            duration=0.5,
            state=ExecutionState.FAILED,
        )
    )
    assert calls[0] == ("thinking", ("task-1", "checking", 3))
    assert calls[1][0] == "started"
    assert calls[1][1][:2] == ("task-1", "read_file")
    assert calls[1][2]["args_preview"] == "README.md"
    assert calls[2][0] == "completed"
    assert calls[2][2] == {
        "result_preview": "failed",
        "state": ExecutionState.FAILED,
    }
    assert len(calls) == 3
