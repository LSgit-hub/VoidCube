from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tools.process_registry import ProcessRegistry


@pytest.fixture
def process_registry():
    registry = ProcessRegistry()
    yield registry
    registry.kill_all()
    for session in registry._sessions.values():
        if session.kind == "local" and session._process is not None:
            session._done.wait(5)
            assert session._process.poll() is not None


def _wait_for_output(registry: ProcessRegistry, session_id: str, timeout: float = 5) -> str:
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        result = registry.poll(session_id)
        output += result["output"]
        if output or result["status"] != "running":
            return output
        time.sleep(0.02)
    pytest.fail("background process produced no output")


@pytest.mark.unit
def test_local_process_wait_returns_output_and_exit_code(process_registry, tmp_path):
    session = process_registry.spawn_local(
        command="printf completed",
        cwd=str(tmp_path),
        task_id="local-wait",
    )

    result = process_registry.wait(session.id, timeout=5)

    assert result["status"] == "completed"
    assert result["output"] == "completed"
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert process_registry.has_active_processes("local-wait") is False


@pytest.mark.unit
def test_poll_returns_only_new_output(process_registry, tmp_path):
    session = process_registry.spawn_local(
        command="printf first; sleep 0.5; printf second",
        cwd=str(tmp_path),
        task_id="incremental",
    )

    first = _wait_for_output(process_registry, session.id)
    final = process_registry.wait(session.id, timeout=5)

    assert first == "first"
    assert final["output"] == "second"
    assert process_registry.poll(session.id)["output"] == ""


@pytest.mark.unit
def test_write_and_close_deliver_stdin_and_eof(process_registry, tmp_path):
    session = process_registry.spawn_local(
        command="cat",
        cwd=str(tmp_path),
        task_id="stdin",
    )

    process_registry.write(session.id, "input text")
    process_registry.close(session.id)
    result = process_registry.wait(session.id, timeout=5)

    assert result["status"] == "completed"
    assert result["output"] == "input text"


@pytest.mark.unit
def test_kill_stops_local_process(process_registry, tmp_path):
    session = process_registry.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="kill-one",
    )

    result = process_registry.kill(session.id)

    assert result["status"] == "killed"
    assert process_registry.has_active_processes("kill-one") is False


@pytest.mark.unit
def test_kill_all_only_targets_requested_task(process_registry, tmp_path):
    first = process_registry.spawn_local(
        command="sleep 30", cwd=str(tmp_path), task_id="target"
    )
    second = process_registry.spawn_local(
        command="sleep 30", cwd=str(tmp_path), task_id="keep"
    )

    assert process_registry.kill_all(task_id="target") == 1
    first._done.wait(5)

    assert process_registry.get(first.id).status == "killed"
    assert process_registry.get(second.id).status == "running"
    assert process_registry.kill_all() == 1
    assert second._done.wait(5)
    assert second._process.poll() is not None


@pytest.mark.unit
def test_completion_and_watch_notifications_are_emitted_once(process_registry, tmp_path):
    session = process_registry.spawn_local(
        command="printf 'ready now\n'",
        cwd=str(tmp_path),
        task_id="notifications",
        notify_on_complete=True,
        watch_patterns=["ready"],
    )

    result = process_registry.wait(session.id, timeout=5)
    events = [process_registry.completion_queue.get(timeout=1) for _ in range(2)]

    assert result["status"] == "completed"
    assert [event["type"] for event in events] == ["watch_match", "completion"]
    assert events[0]["pattern"] == "ready"
    assert process_registry.is_completion_consumed(session.id) is True
    with pytest.raises(queue.Empty):
        process_registry.completion_queue.get_nowait()


class _FakeEnvironment:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, command, cwd=""):
        self.started.set()
        self.release.wait(5)
        return {"output": f"{cwd}:{command}", "returncode": 7}


@pytest.mark.unit
def test_remote_process_runs_in_background_and_rejects_stdin(process_registry):
    env = _FakeEnvironment()
    session = process_registry.spawn_via_env(
        env=env,
        command="remote command",
        cwd="/workspace",
        task_id="remote",
    )
    assert env.started.wait(1)

    with pytest.raises(ValueError, match="do not support stdin"):
        process_registry.write(session.id, "data")
    with pytest.raises(ValueError, match="do not support stdin"):
        process_registry.close(session.id)

    env.release.set()
    result = process_registry.wait(session.id, timeout=5)
    assert result["output"] == "/workspace:remote command"
    assert result["exit_code"] == 7


@pytest.mark.unit
def test_process_tool_remains_registered_after_ops_tools_import():
    probe = """
from types import SimpleNamespace
import sys
sys.modules['psutil'] = SimpleNamespace()
from tools.process_registry import process_tool
from tools.registry import registry
import tools.ops_register
assert registry.get('process') is process_tool
assert registry.get('top_processes') is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.unit
def test_process_tool_reports_unknown_session():
    from tools.process_registry import process_tool

    result = json.loads(process_tool({"action": "poll", "session_id": "missing"}))

    assert result["success"] is False
    assert "Unknown process session" in result["error"]


@pytest.mark.unit
def test_terminal_background_execution_uses_process_registry(monkeypatch, tmp_path):
    import tools.terminal_tool as terminal_tool_module
    from tools.process_registry import process_registry as shared_registry

    task_id = "terminal-background-integration"
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    monkeypatch.setattr(
        terminal_tool_module,
        "_check_tirith_security",
        lambda command: {"action": "allow", "findings": []},
    )
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)

    try:
        started = json.loads(terminal_tool_module.terminal_tool(
            "printf integrated",
            background=True,
            task_id=task_id,
            force=True,
        ))
        result = shared_registry.wait(started["session_id"], timeout=5)
    finally:
        shared_registry.kill_all(task_id=task_id)
        terminal_tool_module.cleanup_vm(task_id)

    assert started["output"] == "Background process started"
    assert result["output"] == "integrated"
    assert result["exit_code"] == 0
