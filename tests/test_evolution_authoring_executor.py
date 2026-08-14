from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from systems.evolution_authoring import (
    AuthoringAgentReport,
    EvolutionAuthoringExecutor,
    EvolutionAuthoringSpec,
)
from systems.evolution_evaluation import (
    ExecutionEnvironmentManifest,
    RuntimeToolIdentity,
    WorkspacePathMapping,
)
from tools.task_execution import TaskExecutionBlocked


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.com", cwd=repository)
    (repository / "agent").mkdir()
    (repository / "agent/demo.py").write_text("VALUE = 'baseline'\n", encoding="utf-8")
    _git("add", "agent/demo.py", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository).stdout.strip()


def _manifest(worktree: Path, head: str) -> ExecutionEnvironmentManifest:
    tools = tuple(
        RuntimeToolIdentity(
            scope="execution",
            name=name,
            available=True,
            executable=f"/usr/bin/{name}",
            version=f"{name} test-version",
        )
        for name in ("git", "python", "pytest", "node", "npm")
    )
    return ExecutionEnvironmentManifest.create(
        backend="podman",
        validation_scope="container",
        host_os="Windows test",
        execution_os="Linux test",
        architecture="x86_64",
        host_workspace_path=str(worktree),
        execution_workspace_path="/workspace",
        path_mappings=(
            WorkspacePathMapping(host_path=str(worktree), execution_path="/workspace"),
        ),
        tools=tools,
        repository_head=head,
        dependency_fingerprint="d" * 64,
        validated_platforms=("linux",),
        image_reference="localhost/voidcube-project-podman:py314-v1",
        image_digest="sha256:" + "e" * 64,
    )


class _Harness:
    def __init__(self) -> None:
        self.worktree: Path | None = None
        self.released: list[str] = []
        self.commands: list[str] = []

    def prepare(
        self, task_id: str, worktree: str, **kwargs: object
    ) -> dict[str, object]:
        self.worktree = Path(worktree)
        assert (
            kwargs["expected_head"]
            == _git("rev-parse", "HEAD", cwd=self.worktree).stdout.strip()
        )
        return _manifest(self.worktree, str(kwargs["expected_head"])).model_dump(
            mode="json"
        )

    def release(self, task_id: str) -> None:
        self.released.append(task_id)

    def terminal(self, command: str, **kwargs: object) -> dict[str, object]:
        assert self.worktree is not None
        self.commands.append(command)
        if command.startswith("git add -A --"):
            result = _git("add", "-A", cwd=self.worktree, check=False)
        elif " commit -m " in command:
            result = _git(
                "-c",
                "user.name=VoidCube Evolution Author",
                "-c",
                "user.email=evolution@voidcube.local",
                "commit",
                "-m",
                "candidate",
                cwd=self.worktree,
                check=False,
            )
        else:
            result = subprocess.run(
                command,
                cwd=self.worktree,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(kwargs.get("timeout") or 30),
            )
        return {
            "output": (result.stdout or result.stderr).strip(),
            "exit_code": result.returncode,
            "status": "ok" if result.returncode == 0 else "error",
        }


def _spec(
    task_id: str, baseline: str, *, test_command: str = "python -c \"print('ok')\""
) -> EvolutionAuthoringSpec:
    return EvolutionAuthoringSpec(
        task_id=task_id,
        objective="Improve the demo behavior",
        improvement_hypothesis="A focused value change will improve the behavior",
        baseline_commit=baseline,
        allowed_paths=("agent/demo.py",),
        forbidden_patterns=("**/credential*",),
        max_files_changed=1,
        test_commands=(test_command,),
        command_timeout_seconds=30,
        commit_message="Improve demo behavior",
    )


def _executor(
    repository: Path, tmp_path: Path, harness: _Harness
) -> EvolutionAuthoringExecutor:
    return EvolutionAuthoringExecutor(
        repository,
        worktree_root=tmp_path / "authoring-worktrees",
        prepare_environment=harness.prepare,
        release_environment=harness.release,
        terminal_runner=harness.terminal,
    )


@pytest.mark.asyncio
async def test_authoring_executor_creates_tested_candidate_ref_and_cleans_worktree(
    tmp_path: Path,
):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        async def author(self, context):
            assert context.execution_workspace_path == "/workspace"
            assert context.allowed_paths == ("agent/demo.py",)
            assert harness.worktree is not None
            (harness.worktree / "agent/demo.py").write_text(
                "VALUE = 'candidate'\n", encoding="utf-8"
            )
            return AuthoringAgentReport(completed=True, summary="Updated the value")

    result = await _executor(repository, tmp_path, harness).execute(
        _spec("candidate-success", baseline), agent=Agent()
    )

    assert result.status == "candidate_created"
    assert result.candidate_commit != baseline
    assert result.candidate_ref == "refs/voidcube/candidates/candidate-success"
    assert result.changed_files == ("agent/demo.py",)
    assert result.environment_manifest_id
    assert result.environment_identity_id
    assert len(result.command_evidence) == 3
    assert (
        _git("show", f"{result.candidate_ref}:agent/demo.py", cwd=repository).stdout
        == "VALUE = 'candidate'\n"
    )
    assert _git("rev-parse", "HEAD", cwd=repository).stdout.strip() == baseline
    assert (repository / "agent/demo.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 'baseline'\n"
    assert not (tmp_path / "authoring-worktrees/candidate-success").exists()
    assert harness.released == ["candidate-success"]


@pytest.mark.asyncio
async def test_authoring_executor_rejects_duplicate_candidate_task(tmp_path: Path):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            assert harness.worktree is not None
            (harness.worktree / "agent/demo.py").write_text(
                "VALUE = 'candidate'\n", encoding="utf-8"
            )
            return {"completed": True, "summary": "created candidate"}

    executor = _executor(repository, tmp_path, harness)
    first = await executor.execute(_spec("candidate-duplicate", baseline), agent=Agent())
    second = await executor.execute(_spec("candidate-duplicate", baseline), agent=Agent())

    assert first.status == "candidate_created"
    assert second.status == "blocked"
    assert second.error_code == "candidate_ref_exists"
    assert harness.released == ["candidate-duplicate"]


@pytest.mark.asyncio
async def test_authoring_executor_rejects_changes_outside_allowed_boundary(
    tmp_path: Path,
):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            assert harness.worktree is not None
            (harness.worktree / "tests").mkdir()
            (harness.worktree / "tests/forbidden.py").write_text(
                "BAD = True\n", encoding="utf-8"
            )
            return {"completed": True, "summary": "changed a forbidden file"}

    result = await _executor(repository, tmp_path, harness).execute(
        _spec("candidate-forbidden", baseline), agent=Agent()
    )

    assert result.status == "policy_violation"
    assert result.error_code == "evolution_boundary_violation"
    assert result.candidate_commit is None
    assert harness.commands == []
    assert (
        _git(
            "show-ref",
            "--verify",
            "--quiet",
            "refs/voidcube/candidates/candidate-forbidden",
            cwd=repository,
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.asyncio
async def test_authoring_executor_rejects_agent_created_commit(tmp_path: Path):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            assert harness.worktree is not None
            (harness.worktree / "agent/demo.py").write_text(
                "VALUE = 'hidden'\n", encoding="utf-8"
            )
            _git("add", "agent/demo.py", cwd=harness.worktree)
            _git("commit", "-m", "agent must not commit", cwd=harness.worktree)
            return {"completed": True, "summary": "committed directly"}

    result = await _executor(repository, tmp_path, harness).execute(
        _spec("candidate-agent-commit", baseline), agent=Agent()
    )

    assert result.status == "policy_violation"
    assert result.error_code == "agent_created_commit"
    assert result.candidate_commit is None
    assert _git("rev-parse", "HEAD", cwd=repository).stdout.strip() == baseline


@pytest.mark.asyncio
async def test_authoring_executor_restores_refs_modified_by_agent(tmp_path: Path):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            assert harness.worktree is not None
            _git("branch", "unauthorized-agent-ref", cwd=harness.worktree)
            return {"completed": True, "summary": "created an unauthorized ref"}

    result = await _executor(repository, tmp_path, harness).execute(
        _spec("candidate-agent-ref", baseline), agent=Agent()
    )

    assert result.status == "policy_violation"
    assert result.error_code == "agent_modified_git_refs"
    assert (
        _git(
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/unauthorized-agent-ref",
            cwd=repository,
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.asyncio
async def test_authoring_executor_returns_no_changes_without_running_tests(
    tmp_path: Path,
):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            return {"completed": True, "summary": "nothing to change"}

    result = await _executor(repository, tmp_path, harness).execute(
        _spec("candidate-empty", baseline), agent=Agent()
    )

    assert result.status == "no_changes"
    assert result.error_code == "no_changes"
    assert harness.commands == []


@pytest.mark.asyncio
async def test_authoring_executor_does_not_publish_failing_candidate(tmp_path: Path):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            assert harness.worktree is not None
            (harness.worktree / "agent/demo.py").write_text(
                "VALUE = 'broken'\n", encoding="utf-8"
            )
            return {"completed": True, "summary": "updated value"}

    result = await _executor(repository, tmp_path, harness).execute(
        _spec(
            "candidate-test-failure",
            baseline,
            test_command='python -c "raise SystemExit(2)"',
        ),
        agent=Agent(),
    )

    assert result.status == "test_failed"
    assert result.error_code == "test_command_failed"
    assert result.command_evidence[0].exit_code == 2
    assert result.candidate_commit is None


@pytest.mark.asyncio
async def test_authoring_executor_reports_environment_blocked_and_removes_worktree(
    tmp_path: Path,
):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    def blocked_prepare(task_id: str, worktree: str, **kwargs: object):
        raise TaskExecutionBlocked(
            task_id, "podman_unavailable", "Podman is unavailable"
        )

    executor = EvolutionAuthoringExecutor(
        repository,
        worktree_root=tmp_path / "authoring-worktrees",
        prepare_environment=blocked_prepare,
        release_environment=harness.release,
        terminal_runner=harness.terminal,
    )

    result = await executor.execute(
        _spec("candidate-blocked", baseline), agent=object()
    )

    assert result.status == "blocked"
    assert result.error_code == "podman_unavailable"
    assert result.environment_manifest_id is None
    assert not (tmp_path / "authoring-worktrees/candidate-blocked").exists()


@pytest.mark.asyncio
async def test_authoring_result_rejects_tampered_content_address(tmp_path: Path):
    repository, baseline = _repository(tmp_path)
    harness = _Harness()

    class Agent:
        def author(self, context):
            assert harness.worktree is not None
            (harness.worktree / "agent/demo.py").write_text(
                "VALUE = 'candidate'\n", encoding="utf-8"
            )
            return {"completed": True, "summary": "updated value"}

    result = await _executor(repository, tmp_path, harness).execute(
        _spec("candidate-content-address", baseline), agent=Agent()
    )
    payload = result.model_dump(mode="json")
    payload["agent_summary"] = "tampered after creation"

    with pytest.raises(ValidationError, match="content_hash does not match"):
        type(result).model_validate(payload)
