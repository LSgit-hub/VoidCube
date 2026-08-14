from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.task_execution import TaskExecutionBlocked, clear_task_execution_state, get_task_execution_state
from tools.windows_host_executor import WindowsHostExecutor


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
PROJECT_ROOT = Path(__file__).parents[1].resolve()
PROJECT_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


@pytest.fixture(autouse=True)
def _clear_task_states():
    clear_task_execution_state()
    yield
    clear_task_execution_state()


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def test_windows_host_probe_uses_project_virtualenv_and_records_host_evidence():
    executor = WindowsHostExecutor(
        PROJECT_ROOT,
        task_id="windows-probe-task",
        python_executable=PROJECT_PYTHON,
    )

    manifest = executor.probe()

    assert manifest.backend == "local"
    assert manifest.validation_scope == "host"
    assert manifest.validated_platforms == ("windows",)
    python = next(tool for tool in manifest.tools if tool.name == "python")
    pytest_tool = next(tool for tool in manifest.tools if tool.name == "pytest")
    assert python.executable == str(PROJECT_PYTHON)
    assert pytest_tool.executable == f"{PROJECT_PYTHON} -m pytest"
    state = get_task_execution_state("windows-probe-task")
    assert state is not None and state.status == "ready"

    executor.cleanup()
    state = get_task_execution_state("windows-probe-task")
    assert state is not None and state.status == "released"


def test_windows_host_runs_with_project_python_and_enforces_workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "VoidCube Test", cwd=repo)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=repo)
    _git("commit", "-m", "baseline", cwd=repo)

    executor = WindowsHostExecutor(
        repo,
        task_id="windows-command-task",
        python_executable=PROJECT_PYTHON,
    )
    result = executor.run(f'"{PROJECT_PYTHON}" -c "print(123)"')

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.output.strip() == "123"
    with pytest.raises(TaskExecutionBlocked, match="outside workspace"):
        executor.run("echo forbidden", cwd=tmp_path)
    executor.cleanup()


def test_windows_host_timeout_terminates_child_process(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "VoidCube Test", cwd=repo)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=repo)
    _git("commit", "-m", "baseline", cwd=repo)

    executor = WindowsHostExecutor(repo, python_executable=PROJECT_PYTHON)
    result = executor.run(
        f'"{PROJECT_PYTHON}" -c "import time; time.sleep(2)"',
        timeout_seconds=1,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    executor.cleanup()


def test_windows_host_manages_verified_linked_worktree(tmp_path: Path):
    repo = tmp_path / "repo"
    worktree = tmp_path / "candidate"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "VoidCube Test", cwd=repo)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=repo)
    _git("commit", "-m", "baseline", cwd=repo)
    commit = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    executor = WindowsHostExecutor(repo, python_executable=PROJECT_PYTHON)
    created = executor.create_linked_worktree(worktree, commit=commit)

    assert created == worktree.resolve()
    assert _git("rev-parse", "HEAD", cwd=created).stdout.strip() == commit
    executor.remove_linked_worktree(created)
    assert not created.exists()
    executor.cleanup()


def test_windows_host_requires_a_project_virtualenv(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)

    with pytest.raises(TaskExecutionBlocked, match="virtualenv Python"):
        WindowsHostExecutor(repo, python_executable=repo / ".venv" / "Scripts" / "python.exe")
