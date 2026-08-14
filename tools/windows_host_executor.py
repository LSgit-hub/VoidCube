"""Windows host execution for project-faithful evolution validation."""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from systems.evolution_evaluation.environment import capture_host_environment_manifest
from systems.evolution_evaluation.models import (
    ExecutionEnvironmentManifest,
)
from tools.task_execution import (
    TaskExecutionBlocked,
    TaskExecutionContract,
    begin_task_execution,
    block_task_execution,
    configure_task_execution,
    ensure_task_execution_request,
    mark_task_execution_ready,
    release_task_execution,
    validate_task_environment_manifest,
)


class WindowsHostExecutionError(RuntimeError):
    """A host command or worktree operation could not be completed."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class WindowsHostCommandResult(_FrozenModel):
    command: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    output: str
    exit_code: int
    timed_out: bool = False
    started_at: datetime
    finished_at: datetime


class WindowsHostExecutor:
    """Run one task in the Windows project environment.

    The executor deliberately uses the Windows process API and the configured
    project virtualenv. It never delegates commands to WSL, Podman, or a local
    fallback selected by the generic terminal configuration.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        task_id: str | None = None,
        python_executable: str | Path | None = None,
        timeout_seconds: int = 120,
        max_output_chars: int = 50_000,
        environment: Mapping[str, str] | None = None,
        required_tools: tuple[str, ...] = ("git", "python", "pytest"),
        required_platforms: tuple[str, ...] = ("windows",),
    ) -> None:
        if (platform.system() or "").strip().lower() != "windows":
            raise TaskExecutionBlocked(
                str(task_id or "windows-host").strip() or "windows-host",
                "windows_host_required",
                "WindowsHostExecutor can only run on a Windows host",
            )
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Windows host workspace does not exist: {root}")
        if not (root / ".git").exists():
            raise ValueError("Windows host executor requires a Git worktree")
        venv_root = root / ".venv"
        python_path = Path(python_executable or venv_root / "Scripts" / "python.exe")
        python_path = python_path.expanduser().resolve()
        if not python_path.is_file():
            raise TaskExecutionBlocked(
                str(task_id or "windows-host").strip() or "windows-host",
                "project_venv_missing",
                f"project virtualenv Python is unavailable: {python_path}",
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")

        self.workspace = root
        self.python_executable = python_path
        self.task_id = str(task_id or "").strip() or None
        self.timeout_seconds = int(timeout_seconds)
        self.max_output_chars = int(max_output_chars)
        self._environment = dict(environment or {})
        self._manifest: ExecutionEnvironmentManifest | None = None
        self._owned_worktrees: set[Path] = set()

        if self.task_id:
            configure_task_execution(
                TaskExecutionContract(
                    task_id=self.task_id,
                    backend="local",
                    validation_scope="host",
                    host_workspace_path=str(root),
                    execution_workspace_path=str(root),
                    allowed_execution_paths=(str(root),),
                    allowed_environment_variables=tuple(sorted(self._environment)),
                    command_timeout_seconds=self.timeout_seconds,
                    max_output_chars=self.max_output_chars,
                    required_tools=tuple(required_tools),
                    required_platforms=tuple(required_platforms),
                )
            )

    @property
    def manifest(self) -> ExecutionEnvironmentManifest:
        if self._manifest is None:
            raise RuntimeError("Windows host environment has not been probed")
        return self._manifest

    def probe(self) -> ExecutionEnvironmentManifest:
        """Capture the exact host toolchain used for subsequent commands."""
        try:
            head = _git_head(self.workspace)
            manifest = capture_host_environment_manifest(
                self.workspace,
                repository_head=head,
                python_executable=self.python_executable,
            )
            if self.task_id:
                validate_task_environment_manifest(self.task_id, manifest)
                begin_task_execution(self.task_id)
                mark_task_execution_ready(self.task_id, active_backend="local")
            self._manifest = manifest
            return manifest
        except TaskExecutionBlocked:
            raise
        except Exception as exc:
            if self.task_id:
                block_task_execution(
                    self.task_id,
                    code="windows_host_probe_failed",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            raise WindowsHostExecutionError(
                f"Windows host environment probe failed: {exc}"
            ) from exc

    def run(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> WindowsHostCommandResult:
        if not str(command or "").strip():
            raise ValueError("command must not be empty")
        if self._manifest is None:
            self.probe()
        effective_cwd = Path(cwd or self.workspace).expanduser().resolve()
        if not _is_same_or_child(effective_cwd, self.workspace):
            if self.task_id:
                block_task_execution(
                    self.task_id,
                    code="workdir_outside_allowed_paths",
                    reason=f"Windows host command cwd is outside workspace: {effective_cwd}",
                )
            raise TaskExecutionBlocked(
                self.task_id or "windows-host",
                "workdir_outside_allowed_paths",
                f"Windows host command cwd is outside workspace: {effective_cwd}",
            )
        effective_timeout = int(timeout_seconds or self.timeout_seconds)
        if self.task_id:
            ensure_task_execution_request(
                self.task_id,
                requested_backend="local",
                workdir=str(effective_cwd),
                timeout_seconds=effective_timeout,
                environment_variables=tuple(sorted(dict(environment or {}))),
                fallback_to_local=False,
            )

        started = datetime.now(timezone.utc)
        process_env = os.environ.copy()
        process_env.update(self._environment)
        process_env.update(dict(environment or {}))
        venv_scripts = str(self.python_executable.parent)
        process_env["VIRTUAL_ENV"] = str(self.python_executable.parent.parent)
        process_env["PATH"] = venv_scripts + os.pathsep + process_env.get("PATH", "")
        try:
            process = subprocess.Popen(
                str(command),
                cwd=str(effective_cwd),
                env=process_env,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            output, _ = process.communicate(timeout=effective_timeout)
            timed_out = False
            exit_code = int(process.returncode or 0)
        except subprocess.TimeoutExpired as exc:
            self._terminate_process(process)
            partial, _ = process.communicate()
            output = partial or str(exc.output or "")
            timed_out = True
            exit_code = 124
        except OSError as exc:
            raise WindowsHostExecutionError(
                f"Windows host command failed to start: {exc}"
            ) from exc
        finished = datetime.now(timezone.utc)
        output = str(output or "")
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + (
                f"\n[output truncated at {self.max_output_chars} characters]"
            )
        return WindowsHostCommandResult(
            command=str(command),
            cwd=str(effective_cwd),
            output=output,
            exit_code=exit_code,
            timed_out=timed_out,
            started_at=started,
            finished_at=finished,
        )

    def create_linked_worktree(
        self,
        target: str | Path,
        *,
        commit: str,
    ) -> Path:
        target_path = Path(target).expanduser().resolve()
        if target_path == self.workspace:
            raise ValueError("Windows host executor cannot use the primary worktree as a candidate")
        if target_path.exists() and any(target_path.iterdir()):
            raise ValueError(f"linked worktree target is not empty: {target_path}")
        _git_output(
            self.workspace,
            ("rev-parse", "--verify", f"{commit}^{{commit}}"),
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        result = _git_checked(
            self.workspace,
            ("worktree", "add", "--detach", str(target_path), commit),
        )
        if result.returncode != 0:
            raise WindowsHostExecutionError(
                f"failed to create Windows linked worktree: {result.stderr.strip()}"
            )
        top_level = _git_output(target_path, ("rev-parse", "--show-toplevel"))
        head = _git_output(target_path, ("rev-parse", "HEAD"))
        if Path(top_level).resolve() != target_path or head.lower() != commit.lower():
            raise WindowsHostExecutionError("created linked worktree failed identity verification")
        self._owned_worktrees.add(target_path)
        return target_path

    def remove_linked_worktree(self, target: str | Path) -> None:
        target_path = Path(target).expanduser().resolve()
        if target_path == self.workspace:
            raise ValueError("refusing to remove the primary Windows worktree")
        result = _git_checked(
            self.workspace,
            ("worktree", "remove", "--force", str(target_path)),
        )
        if result.returncode != 0:
            raise WindowsHostExecutionError(
                f"failed to remove linked worktree: {result.stderr.strip()}"
            )
        self._owned_worktrees.discard(target_path)

    def cleanup(self) -> None:
        for worktree in tuple(self._owned_worktrees):
            try:
                self.remove_linked_worktree(worktree)
            except Exception:
                continue
        if self.task_id:
            release_task_execution(self.task_id)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if platform.system().lower() == "windows":
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                capture_output=True,
                text=True,
                timeout=10,
            )
        else:
            process.kill()


def _git_head(root: Path) -> str:
    return _git_output(root, ("rev-parse", "HEAD"))


def _git_output(root: Path, args: tuple[str, ...]) -> str:
    result = _git_checked(root, args)
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        raise WindowsHostExecutionError(
            f"Git command failed: {(result.stderr or result.stdout).strip()}"
        )
    return output


def _git_checked(root: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", *args),
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsHostExecutionError(f"Git command could not run: {exc}") from exc


def _is_same_or_child(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = [
    "WindowsHostCommandResult",
    "WindowsHostExecutionError",
    "WindowsHostExecutor",
]
