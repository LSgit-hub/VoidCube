from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "firecrawl" not in sys.modules:
    sys.modules["firecrawl"] = types.SimpleNamespace(Firecrawl=object)
if "psutil" not in sys.modules:
    sys.modules["psutil"] = types.SimpleNamespace()

import run_agent


def _build_minimal_agent(*, print_fn):
    agent = run_agent.AIAgent.__new__(run_agent.AIAgent)
    agent._interrupt_requested = False
    agent.quiet_mode = True
    agent.verbose_logging = False
    agent.log_prefix_chars = 80
    agent.log_prefix = ""
    agent._current_tool = None
    agent._touch_activity = lambda *_args, **_kwargs: None
    agent.tool_progress_callback = None
    agent.tool_start_callback = None
    agent.tool_complete_callback = None
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

    agent._execute_tool_calls_sequential(assistant_message, messages, "task-1")

    assert captured["enable_display"] is True
    assert captured["parent_agent"] is agent
    assert messages[-1]["tool_call_id"] == "tool-1"


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

    agent._execute_tool_calls_sequential(assistant_message, messages, "task-2")

    assert captured["enable_display"] is False
    assert spinner_events == ["init", "start", "stop:delegate finished"]
    assert messages[-1]["tool_call_id"] == "tool-1"
