from __future__ import annotations

from VoidCube_cli import execution_context


def test_collect_execution_context_distinguishes_sandbox_and_worktree(monkeypatch):
    monkeypatch.setattr(
        "tools.terminal_tool._get_env_config",
        lambda: {
            "env_type": "podman",
            "cwd": "/workspace",
            "host_cwd": "C:\\repo",
            "fallback_to_local": False,
        },
    )

    result = execution_context.collect_execution_context(
        {"path": "C:\\repo\\.worktrees\\task-1", "branch": "task-1"}
    )

    assert result["mode"] == "sandbox"
    assert result["backend"] == "podman"
    assert result["backendWorkingDirectory"] == "/workspace"
    assert result["hostWorkingDirectory"].endswith("task-1")
    assert result["workspaceName"] == "task-1"
    assert result["branch"] == "task-1"
    assert result["worktree"] is True


def test_published_execution_context_rejects_stale_pid_and_cleans_owner(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "context.json"
    monkeypatch.setattr(
        execution_context,
        "collect_execution_context",
        lambda worktree_info=None: {
            "mode": "system",
            "backend": "local",
            "workspaceName": "repo",
        },
    )

    published = execution_context.publish_execution_context(path=path)

    assert execution_context.load_execution_context(
        path=path,
        pid_alive=lambda pid: pid == published["pid"],
    ) == published
    assert execution_context.load_execution_context(
        path=path,
        pid_alive=lambda pid: False,
    ) is None

    execution_context.clear_execution_context(published["pid"] + 1, path=path)
    assert path.exists()
    execution_context.clear_execution_context(published["pid"], path=path)
    assert not path.exists()
