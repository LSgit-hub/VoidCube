from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from voidcube.infrastructure.execution.process_registry import ProcessRegistry


@pytest.fixture
def process_registry(tmp_path):
    registry = ProcessRegistry(tmp_path / "process-registry")
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

    assert result["status"] == "succeeded"
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

    assert result["status"] == "succeeded"
    assert result["output"] == "input text"


@pytest.mark.unit
def test_kill_stops_local_process(process_registry, tmp_path):
    session = process_registry.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="kill-one",
    )

    result = process_registry.kill(session.id)

    assert result["status"] == "cancelled"
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

    assert process_registry.get(first.id).status == "cancelled"
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

    assert result["status"] == "succeeded"
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
    assert result["status"] == "failed"


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 126])
def test_local_nonzero_exit_is_failed(process_registry, tmp_path, exit_code):
    session = process_registry.spawn_local(
        command=f"exit {exit_code}",
        cwd=str(tmp_path),
        task_id=f"local-failure-{exit_code}",
    )

    result = process_registry.wait(session.id, timeout=5)

    assert result["status"] == "failed"
    assert result["exit_code"] == exit_code


@pytest.mark.unit
def test_completed_session_and_output_survive_registry_restart(tmp_path):
    storage = tmp_path / "persistent-registry"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="printf persisted",
        cwd=str(tmp_path),
        task_id="restart-complete",
    )
    assert first.wait(session.id, timeout=5)["status"] == "succeeded"

    restarted = ProcessRegistry(storage)
    recovered = restarted.poll(session.id)

    assert recovered["status"] == "succeeded"
    assert recovered["output"] == ""
    assert restarted.get(session.id).spool_path.read_text(encoding="utf-8") == "persisted"


@pytest.mark.unit
def test_live_local_session_recovers_as_running_then_converges_from_marker(tmp_path):
    storage = tmp_path / "live-recovery"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="printf before; sleep 0.4; printf after",
        cwd=str(tmp_path),
        task_id="restart-running",
    )
    assert _wait_for_output(first, session.id) == "before"

    restarted = ProcessRegistry(storage)
    recovered_session = restarted.get(session.id)

    assert recovered_session.status == "running"
    assert "control restored" in recovered_session.error
    assert recovered_session._done.wait(5)
    recovered = restarted.poll(session.id)
    assert recovered["status"] == "succeeded"
    assert recovered["output"] == "after"


@pytest.mark.unit
def test_unknown_session_is_rechecked_on_later_restart(tmp_path):
    storage = tmp_path / "unknown-restart"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="printf recovered",
        cwd=str(tmp_path),
        task_id="unknown-restart",
    )
    assert first.wait(session.id, timeout=5)["status"] == "succeeded"
    with first._connect() as connection:
        connection.execute(
            "UPDATE process_sessions SET status = 'unknown', exit_code = NULL "
            "WHERE session_id = ?",
            (session.id,),
        )

    restarted = ProcessRegistry(storage)

    assert restarted.poll(session.id)["status"] == "succeeded"


@pytest.mark.unit
def test_unknown_session_converges_when_marker_arrives_after_recovery(tmp_path):
    storage = tmp_path / "late-marker"
    registry = ProcessRegistry(storage)
    session = registry.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="late-marker",
    )
    registry.kill(session.id)
    session.marker_path.unlink(missing_ok=True)
    with registry._connect() as connection:
        connection.execute(
            "UPDATE process_sessions SET status = 'unknown', exit_code = NULL "
            "WHERE session_id = ?",
            (session.id,),
        )

    recovered = ProcessRegistry(storage)
    recovered_session = recovered.get(session.id)
    recovered_session.marker_path.write_text(
        json.dumps({"exit_code": 0, "output_truncated": False, "error": ""}),
        encoding="utf-8",
    )

    assert recovered.poll(session.id)["status"] == "succeeded"


@pytest.mark.unit
def test_concurrent_spool_observers_do_not_duplicate_output(tmp_path):
    storage = tmp_path / "concurrent-spool"
    registry = ProcessRegistry(storage)
    session = registry.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="spool-race",
    )
    session.spool_path.write_text("single", encoding="utf-8")
    barrier = threading.Barrier(3)

    def observe() -> None:
        barrier.wait()
        registry._observe_spool(session)

    threads = [threading.Thread(target=observe), threading.Thread(target=observe)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
    try:
        assert session._output == "single"
        assert session._observed_bytes == len(b"single")
    finally:
        registry.kill(session.id)


@pytest.mark.unit
def test_spool_observer_preserves_utf8_character_split_across_reads(tmp_path):
    registry = ProcessRegistry(tmp_path / "utf8-spool")
    session = registry.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="utf8-split",
    )
    encoded = "中".encode("utf-8")
    try:
        session.spool_path.write_bytes(encoded[:2])
        registry._observe_spool(session)
        assert session._output == ""

        with session.spool_path.open("ab") as handle:
            handle.write(encoded[2:])
        registry._observe_spool(session)

        assert session._output == "中"
        assert session._pending_utf8 == b""
    finally:
        registry.kill(session.id)


@pytest.mark.unit
def test_verified_recovered_process_can_be_killed(tmp_path):
    storage = tmp_path / "recovered-kill"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="recovered-kill",
    )
    restarted = ProcessRegistry(storage)
    recovered = restarted.get(session.id)

    assert recovered.status == "running"
    assert restarted.kill(session.id)["status"] == "cancelled"


@pytest.mark.unit
def test_stale_registry_cannot_overwrite_proven_terminal_state(tmp_path):
    storage = tmp_path / "terminal-monotonic"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="printf done",
        cwd=str(tmp_path),
        task_id="terminal-monotonic",
    )
    stale = ProcessRegistry(storage)
    assert first.wait(session.id, timeout=5)["status"] == "succeeded"

    stale_session = stale.get(session.id)
    stale_session.status = "unknown"
    stale_session.output_cursor = 0
    stale._persist(stale_session)

    with first._connect() as connection:
        row = connection.execute(
            "SELECT status, exit_code FROM process_sessions WHERE session_id = ?",
            (session.id,),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["exit_code"] == 0


@pytest.mark.unit
def test_two_registries_atomically_claim_incremental_output(tmp_path):
    storage = tmp_path / "cursor-claim"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="printf shared",
        cwd=str(tmp_path),
        task_id="cursor-claim",
    )
    assert session._done.wait(5)
    second = ProcessRegistry(storage)
    first._load_output(first.get(session.id))
    second._load_output(second.get(session.id))
    barrier = threading.Barrier(3)
    outputs = []

    def poll(registry: ProcessRegistry) -> None:
        barrier.wait()
        outputs.append(registry.poll(session.id)["output"])

    threads = [
        threading.Thread(target=poll, args=(first,)),
        threading.Thread(target=poll, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outputs) == ["", "shared"]


@pytest.mark.unit
def test_recovery_rejects_reused_or_unverifiable_pid_identity(tmp_path, monkeypatch):
    storage = tmp_path / "identity-recovery"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="identity",
    )
    expected = session.process_create_time
    monkeypatch.setattr(
        ProcessRegistry,
        "_process_identity",
        staticmethod(lambda _pid: (expected or 0.0) + 100.0),
    )

    restarted = ProcessRegistry(storage)
    recovered = restarted.get(session.id)
    try:
        assert recovered.status == "unknown"
        assert "identity could not be verified" in recovered.error
        assert recovered._done.is_set()
    finally:
        first.kill(session.id)


@pytest.mark.unit
def test_completion_notification_consumption_survives_restart(tmp_path):
    storage = tmp_path / "notification-recovery"
    first = ProcessRegistry(storage)
    session = first.spawn_local(
        command="printf notification",
        cwd=str(tmp_path),
        task_id="notification",
        notify_on_complete=True,
    )
    assert session._done.wait(5)

    restarted = ProcessRegistry(storage)
    event = restarted.completion_queue.get(timeout=1)
    assert event["type"] == "completion"
    assert event["session_id"] == session.id
    restarted.mark_completion_consumed(session.id)

    second_restart = ProcessRegistry(storage)
    with pytest.raises(queue.Empty):
        second_restart.completion_queue.get_nowait()


@pytest.mark.unit
def test_spool_is_bounded_per_session(tmp_path):
    registry = ProcessRegistry(
        tmp_path / "bounded-spool",
        max_spool_bytes=64,
        max_total_spool_bytes=128,
    )
    session = registry.spawn_local(
        command="printf 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789--overflow'",
        cwd=str(tmp_path),
        task_id="bounded",
    )

    result = registry.wait(session.id, timeout=5)

    assert result["status"] == "succeeded"
    assert result["output_truncated"] is True
    assert session.spool_path.stat().st_size == 64
    assert len(result["output"].encode("utf-8")) == 64


@pytest.mark.unit
def test_capacity_never_removes_active_or_unknown_sessions(tmp_path):
    storage = tmp_path / "capacity"
    registry = ProcessRegistry(
        storage,
        max_spool_bytes=64,
        max_total_spool_bytes=64,
        max_retained_sessions=1,
        retention_days=0,
    )
    active = registry.spawn_local(
        command="sleep 30",
        cwd=str(tmp_path),
        task_id="active",
    )
    with pytest.raises(RuntimeError, match="capacity is exhausted"):
        registry.spawn_local(
            command="printf blocked",
            cwd=str(tmp_path),
            task_id="blocked",
        )
    registry.kill(active.id)

    with registry._connect() as connection:
        connection.execute(
            "UPDATE process_sessions SET status = 'unknown' WHERE session_id = ?",
            (active.id,),
        )
    registry.get(active.id).status = "unknown"

    assert registry.cleanup() == 0
    assert registry.get(active.id) is not None


@pytest.mark.unit
def test_process_tool_remains_registered_after_ops_tools_import():
    probe = """
from types import SimpleNamespace
import sys
sys.modules['psutil'] = SimpleNamespace()
from voidcube.infrastructure.execution.process_registry import process_tool
from voidcube.extensions.tools.registry import registry
import voidcube.extensions.tools.ops_register
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
    from voidcube.infrastructure.execution.process_registry import process_tool

    result = json.loads(process_tool({"action": "poll", "session_id": "missing"}))

    assert result["success"] is False
    assert "Unknown process session" in result["error"]


@pytest.mark.unit
def test_terminal_background_execution_uses_process_registry(monkeypatch, tmp_path):
    import voidcube.infrastructure.execution.terminal_tool as terminal_tool_module
    from voidcube.infrastructure.execution.process_registry import process_registry as shared_registry

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
