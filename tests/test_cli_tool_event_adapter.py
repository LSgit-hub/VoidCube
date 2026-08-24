from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.domain.contracts.execution import ExecutionState
from voidcube.domain.contracts.tool_events import ToolEvent
from voidcube.interfaces.cli.tool_event_adapter import project_tool_event


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _host(**overrides):
    invalidations = []
    values = {
        "_tool_start_time": 0.0,
        "_current_tool_name": "",
        "_autonomous_gate_active": False,
        "_current_autonomous_task": None,
        "_inline_diffs_enabled": False,
        "tool_progress_mode": "off",
        "_last_scrollback_tool": "",
        "_should_emit_scrollback_output": lambda: True,
        "_invalidate": lambda: invalidations.append(True),
    }
    values.update(overrides)
    return SimpleNamespace(**values), invalidations


def test_started_and_completed_events_update_cli_view_state() -> None:
    host, invalidations = _host()
    autonomous_events = []
    append_event = lambda *args, **kwargs: autonomous_events.append((args, kwargs))

    project_tool_event(
        host,
        ToolEvent.started(
            call_id="call-1",
            name="shell",
            arguments={"command": "echo ok"},
            preview="echo ok",
        ),
        append_autonomous_event=append_event,
        emit_line=lambda _line: None,
    )

    assert host._current_tool_name == "shell"
    assert host._tool_start_time > 0
    assert "echo ok" in host._spinner_text

    project_tool_event(
        host,
        ToolEvent.terminal(
            call_id="call-1",
            name="shell",
            arguments={"command": "echo ok"},
            result="ok",
            duration=1.5,
            state=ExecutionState.SUCCEEDED,
        ),
        append_autonomous_event=append_event,
        emit_line=lambda _line: None,
    )

    assert host._current_tool_name == ""
    assert host._tool_start_time == 0
    assert len(invalidations) == 2
    assert autonomous_events == []


def test_completed_event_uses_its_own_arguments_for_scrollback(monkeypatch) -> None:
    observed = []
    monkeypatch.setattr(
        "voidcube.runtime.agent.display.get_cute_tool_message",
        lambda name, arguments, duration, result: observed.append(
            (name, arguments, duration, result)
        ) or "done",
    )
    host, _ = _host(tool_progress_mode="all")
    lines = []

    project_tool_event(
        host,
        ToolEvent.terminal(
            call_id="call-2",
            name="read_file",
            arguments={"path": "README.md"},
            result="content",
            duration=0.25,
            state=ExecutionState.SUCCEEDED,
        ),
        append_autonomous_event=lambda *_args, **_kwargs: None,
        emit_line=lines.append,
    )

    assert observed == [("read_file", {"path": "README.md"}, 0.25, "content")]
    assert lines == ["  ✓ done"]


def test_completed_error_has_explicit_failure_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        "voidcube.runtime.agent.display.get_cute_tool_message",
        lambda *_args, **_kwargs: "shell command",
    )
    host, _ = _host(tool_progress_mode="all")
    lines = []

    project_tool_event(
        host,
        ToolEvent.terminal(
            call_id="call-error",
            name="shell",
            arguments={"command": "false"},
            result="failed",
            duration=0.4,
            state=ExecutionState.FAILED,
        ),
        append_autonomous_event=lambda *_args, **_kwargs: None,
        emit_line=lines.append,
    )

    assert lines == ["  ✗ shell command"]


def test_delegate_batch_terminal_is_not_rendered_twice(monkeypatch) -> None:
    monkeypatch.setattr(
        "voidcube.runtime.agent.display.get_cute_tool_message",
        lambda *_args, **_kwargs: "delegate batch",
    )
    host, _ = _host(tool_progress_mode="all")
    lines = []

    project_tool_event(
        host,
        ToolEvent.terminal(
            call_id="call-delegate",
            name="delegate_task",
            arguments={"tasks": [{"goal": "one"}, {"goal": "two"}]},
            result='{"results":[{"status":"completed"}]}',
            duration=10.0,
            state=ExecutionState.SUCCEEDED,
        ),
        append_autonomous_event=lambda *_args, **_kwargs: None,
        emit_line=lines.append,
    )

    assert lines == []


def test_delegate_preflight_error_remains_visible(monkeypatch) -> None:
    monkeypatch.setattr(
        "voidcube.runtime.agent.display.get_cute_tool_message",
        lambda *_args, **_kwargs: "delegate invalid worktree",
    )
    host, _ = _host(tool_progress_mode="all")
    lines = []

    project_tool_event(
        host,
        ToolEvent.terminal(
            call_id="call-delegate-error",
            name="delegate_task",
            arguments={"tasks": [{"goal": "one"}]},
            result='{"success":false,"error":"invalid worktree"}',
            duration=0.0,
            state=ExecutionState.FAILED,
        ),
        append_autonomous_event=lambda *_args, **_kwargs: None,
        emit_line=lines.append,
    )

    assert lines == ["  ✗ delegate invalid worktree"]


def test_reasoning_and_subagent_events_do_not_mutate_cli_state() -> None:
    host, invalidations = _host()

    for event in (
        ToolEvent.reasoning("checked"),
        ToolEvent.subagent_progress("read_file"),
    ):
        project_tool_event(
            host,
            event,
            append_autonomous_event=lambda *_args, **_kwargs: None,
            emit_line=lambda _line: None,
        )

    assert invalidations == []
    assert host._current_tool_name == ""
