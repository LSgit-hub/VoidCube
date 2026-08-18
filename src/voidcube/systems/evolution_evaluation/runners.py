"""Native-first linked-worktree runners for platform validation."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from systems.evolution_evaluation.executor import (
    BenchmarkCaseFailed,
    BenchmarkCaseResult,
    BenchmarkPackExecutor,
    BenchmarkRunRequest,
    BenchmarkRunner,
)
from systems.evolution_evaluation.models import (
    BenchmarkCommandEvidence,
    ExecutionEnvironmentManifest,
    HardGateResult,
    MetricValue,
    SubjectCheckoutEvidence,
)
from systems.evolution_evaluation.selection import BenchmarkPlatformSelection


class BenchmarkCaseEvaluation(BaseModel):
    """Case-owned output before infrastructure attaches environment evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(min_length=1)
    metrics: tuple[MetricValue, ...] = Field(min_length=1)
    hard_gate_results: tuple[HardGateResult, ...] = ()
    command_evidence: tuple[BenchmarkCommandEvidence, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class ValidationCaseEvaluator(Protocol):
    def __call__(
        self,
        request: BenchmarkRunRequest,
        task_id: str,
        environment: ExecutionEnvironmentManifest,
    ) -> BenchmarkCaseEvaluation | Mapping[str, object]: ...


PrepareValidationEnvironment = Callable[..., Mapping[str, object]]
ReleaseValidationEnvironment = Callable[[str], None]


class GitWorktreeValidationRunner:
    """Run one benchmark case in a disposable platform-specific worktree."""

    def __init__(
        self,
        repository: str | Path,
        *,
        worktree_path: str | Path,
        platform: str,
        baseline_commit: str,
        candidate_commit: str,
        evaluator: ValidationCaseEvaluator,
        python_executable: str | Path | None = None,
        workspace_dependencies: Mapping[str, str | Path] | None = None,
        prepare_environment: PrepareValidationEnvironment | None = None,
        release_environment: ReleaseValidationEnvironment | None = None,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        self.worktree_path = Path(worktree_path).expanduser().resolve()
        self.platform = str(platform).strip().lower()
        if self.platform not in {"linux", "windows"}:
            raise ValueError(f"unsupported validation platform: {platform}")
        if not (self.repository / ".git").exists():
            raise ValueError(
                f"validation repository is not a Git worktree: {self.repository}"
            )
        try:
            self.worktree_path.relative_to(self.repository)
        except ValueError:
            pass
        else:
            raise ValueError("validation worktree must be outside the repository")
        self.baseline_commit = _full_commit(baseline_commit, "baseline_commit")
        self.candidate_commit = _full_commit(candidate_commit, "candidate_commit")
        self.evaluator = evaluator
        self.python_executable = (
            Path(python_executable).expanduser().resolve()
            if python_executable is not None
            else None
        )
        self.workspace_dependencies = {
            _relative_workspace_path(relative_path): Path(source).expanduser().resolve()
            for relative_path, source in dict(workspace_dependencies or {}).items()
        }
        for source in self.workspace_dependencies.values():
            try:
                source.relative_to(self.repository)
            except ValueError as exc:
                raise ValueError(
                    "validation workspace dependencies must belong to the repository"
                ) from exc
        self._prepare_environment = (
            prepare_environment or self._prepare_platform_environment
        )
        self._release_environment = release_environment or _release_environment
        self._lock = threading.Lock()

    def __call__(self, request: BenchmarkRunRequest) -> BenchmarkCaseResult:
        if request.validation_platform != self.platform:
            raise BenchmarkCaseFailed(
                "validation runner platform mismatch: "
                f"expected {self.platform}, got {request.validation_platform}"
            )
        commit = (
            self.baseline_commit
            if request.subject == "baseline"
            else self.candidate_commit
        )
        if request.subject == "candidate" and request.candidate_commit != commit:
            raise BenchmarkCaseFailed(
                "validation runner candidate commit does not match the experiment"
            )
        task_id = _validation_task_id(
            self.platform,
            request,
            candidate_commit=self.candidate_commit,
        )
        with self._lock:
            return self._execute_case(request, task_id=task_id, commit=commit)

    def _execute_case(
        self,
        request: BenchmarkRunRequest,
        *,
        task_id: str,
        commit: str,
    ) -> BenchmarkCaseResult:
        worktree_created = False
        environment_preparation_started = False
        primary_error: BaseException | None = None
        try:
            self._create_worktree(commit)
            worktree_created = True
            if self.platform == "windows":
                self._link_workspace_dependencies()
            environment_preparation_started = True
            raw_manifest = self._prepare_environment(
                task_id,
                str(self.worktree_path),
                expected_head=commit,
                command_timeout_seconds=max(30, int(request.timeout_seconds)),
            )
            manifest = ExecutionEnvironmentManifest.model_validate(raw_manifest)
            if manifest.validated_platforms != (self.platform,):
                raise BenchmarkCaseFailed(
                    "validation environment returned evidence for a different platform"
                )
            raw_evaluation = self.evaluator(request, task_id, manifest)
            evaluation = (
                raw_evaluation
                if isinstance(raw_evaluation, BenchmarkCaseEvaluation)
                else BenchmarkCaseEvaluation.model_validate(raw_evaluation)
            )
            if evaluation.case_id != request.case.case_id:
                raise BenchmarkCaseFailed(
                    "validation evaluator returned a different benchmark case"
                )
            checkout = SubjectCheckoutEvidence.create(
                subject=request.subject,
                commit=commit,
                worktree_path=manifest.execution_workspace_path,
                execution_environment_identity_id=(
                    manifest.identity().execution_environment_identity_id
                ),
                checked_out_at=datetime.now(timezone.utc),
            )
            return BenchmarkCaseResult(
                **evaluation.model_dump(mode="python"),
                execution_environment=manifest,
                subject_checkout=checkout,
                validation_platform=self.platform,
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_error = self._cleanup(
                task_id,
                worktree_created=worktree_created,
                environment_preparation_started=environment_preparation_started,
            )
            if cleanup_error is not None and primary_error is None:
                raise BenchmarkCaseFailed(
                    f"validation runner cleanup failed: {cleanup_error}"
                ) from cleanup_error

    def _prepare_platform_environment(
        self,
        task_id: str,
        worktree_path: str,
        **kwargs: object,
    ) -> Mapping[str, object]:
        if self.platform == "windows":
            from tools.terminal_tool import prepare_task_native_git_worktree

            return prepare_task_native_git_worktree(
                task_id,
                worktree_path,
                expected_head=str(kwargs.get("expected_head") or ""),
                command_timeout_seconds=int(
                    kwargs.get("command_timeout_seconds") or 120
                ),
                python_executable=(
                    str(self.python_executable)
                    if self.python_executable is not None
                    else None
                ),
            )
        from tools.terminal_tool import prepare_task_git_worktree

        return prepare_task_git_worktree(
            task_id,
            worktree_path,
            expected_head=str(kwargs.get("expected_head") or ""),
            command_timeout_seconds=int(
                kwargs.get("command_timeout_seconds") or 120
            ),
            backend="podman",
        )

    def _create_worktree(self, commit: str) -> None:
        if self.worktree_path.exists():
            raise BenchmarkCaseFailed(
                f"validation worktree already exists: {self.worktree_path}"
            )
        self.worktree_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            (
                "git",
                "worktree",
                "add",
                "--detach",
                str(self.worktree_path),
                commit,
            ),
            cwd=self.repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode != 0:
            raise BenchmarkCaseFailed(
                (result.stderr or result.stdout).strip()[:1000]
                or "failed to create validation worktree"
            )

    def _link_workspace_dependencies(self) -> None:
        for relative_path, source in self.workspace_dependencies.items():
            if not source.is_dir():
                raise BenchmarkCaseFailed(
                    f"validation workspace dependency is unavailable: {source}"
                )
            destination = self.worktree_path.joinpath(*relative_path.split("/"))
            if destination.exists() or destination.is_symlink():
                raise BenchmarkCaseFailed(
                    f"validation workspace dependency destination exists: {relative_path}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                result = subprocess.run(
                    (
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(destination),
                        str(source),
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if result.returncode != 0:
                    raise BenchmarkCaseFailed(
                        (result.stderr or result.stdout).strip()[:1000]
                        or f"failed to link workspace dependency: {relative_path}"
                    )
            else:
                destination.symlink_to(source, target_is_directory=True)

    def _cleanup(
        self,
        task_id: str,
        *,
        worktree_created: bool,
        environment_preparation_started: bool,
    ) -> BaseException | None:
        errors: list[BaseException] = []
        if environment_preparation_started:
            try:
                self._release_environment(task_id)
            except BaseException as exc:
                errors.append(exc)
        if worktree_created:
            try:
                self._remove_workspace_dependency_links()
            except BaseException as exc:
                errors.append(exc)
            try:
                result = subprocess.run(
                    (
                        "git",
                        "worktree",
                        "remove",
                        "--force",
                        str(self.worktree_path),
                    ),
                    cwd=self.repository,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        (result.stderr or result.stdout).strip()[:1000]
                        or "failed to remove validation worktree"
                    )
            except BaseException as exc:
                errors.append(exc)
                if self.worktree_path.exists():
                    shutil.rmtree(self.worktree_path, ignore_errors=True)
                subprocess.run(
                    ("git", "worktree", "prune"),
                    cwd=self.repository,
                    capture_output=True,
                    timeout=30,
                )
        return errors[0] if errors else None

    def _remove_workspace_dependency_links(self) -> None:
        for relative_path in reversed(tuple(self.workspace_dependencies)):
            destination = self.worktree_path.joinpath(*relative_path.split("/"))
            if not destination.exists() and not destination.is_symlink():
                continue
            if destination.is_symlink():
                destination.unlink()
            else:
                os.rmdir(destination)


class NativeFirstBenchmarkExecutorFactory:
    """Create a one-experiment executor from its deterministic platform selection."""

    def __init__(
        self,
        repository: str | Path,
        *,
        worktree_root: str | Path,
        evaluators: Mapping[str, ValidationCaseEvaluator],
        python_executable: str | Path | None = None,
        workspace_dependencies: Mapping[str, str | Path] | None = None,
        prepare_environments: Mapping[str, PrepareValidationEnvironment] | None = None,
        release_environment: ReleaseValidationEnvironment | None = None,
        case_timeout_seconds: float = 30.0,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        self.worktree_root = Path(worktree_root).expanduser().resolve()
        self.evaluators = dict(evaluators)
        self.python_executable = python_executable
        self.workspace_dependencies = dict(workspace_dependencies or {})
        self.prepare_environments = dict(prepare_environments or {})
        self.release_environment = release_environment
        self.case_timeout_seconds = float(case_timeout_seconds)

    def __call__(
        self,
        *,
        selection: BenchmarkPlatformSelection,
        baseline_commit: str,
        candidate_commit: str,
    ) -> BenchmarkPackExecutor:
        platform_runners = build_native_first_platform_runners(
            self.repository,
            worktree_root=self.worktree_root,
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            required_platforms=selection.required_platforms,
            evaluators=self.evaluators,
            python_executable=self.python_executable,
            workspace_dependencies=self.workspace_dependencies,
            prepare_environments=self.prepare_environments,
            release_environment=self.release_environment,
        )
        return BenchmarkPackExecutor(
            {},
            platform_runners=platform_runners,
            case_timeout_seconds=self.case_timeout_seconds,
        )


def build_native_first_platform_runners(
    repository: str | Path,
    *,
    worktree_root: str | Path,
    baseline_commit: str,
    candidate_commit: str,
    required_platforms: Iterable[str],
    evaluators: Mapping[str, ValidationCaseEvaluator],
    python_executable: str | Path | None = None,
    workspace_dependencies: Mapping[str, str | Path] | None = None,
    prepare_environments: Mapping[str, PrepareValidationEnvironment] | None = None,
    release_environment: ReleaseValidationEnvironment | None = None,
) -> dict[str, dict[str, BenchmarkRunner]]:
    """Build only the platform runners selected for this immutable experiment."""

    platforms = tuple(dict.fromkeys(str(item).strip().lower() for item in required_platforms))
    if not platforms or any(item not in {"linux", "windows"} for item in platforms):
        raise ValueError("required platforms must contain only linux or windows")
    if not evaluators or any(not name or not callable(item) for name, item in evaluators.items()):
        raise ValueError("validation evaluators must be named callables")
    root = Path(worktree_root).expanduser().resolve()
    prepare_by_platform = dict(prepare_environments or {})
    return {
        platform: {
            name: GitWorktreeValidationRunner(
                repository,
                worktree_path=root / platform,
                platform=platform,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                evaluator=evaluator,
                python_executable=python_executable,
                workspace_dependencies=workspace_dependencies,
                prepare_environment=prepare_by_platform.get(platform),
                release_environment=release_environment,
            )
            for name, evaluator in evaluators.items()
        }
        for platform in platforms
    }


def _relative_workspace_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").strip("/")
    parts = tuple(part for part in normalized.split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("workspace dependency paths must be repository-relative")
    if parts[0].lower() == ".git" or ":" in parts[0]:
        raise ValueError("workspace dependency paths cannot target Git metadata")
    return "/".join(parts)


def _full_commit(value: str, field: str) -> str:
    commit = str(value or "").strip().lower()
    if len(commit) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise ValueError(f"{field} must be a full Git commit")
    return commit


def _validation_task_id(
    platform: str,
    request: BenchmarkRunRequest,
    *,
    candidate_commit: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{request.benchmark_pack_id}:{platform}:{request.subject}:"
            f"{request.case.case_id}:{candidate_commit}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"evolution-validation-{platform}-{digest}"


def _release_environment(task_id: str) -> None:
    from tools.terminal_tool import release_task_environment

    release_task_environment(task_id)


__all__ = [
    "BenchmarkCaseEvaluation",
    "GitWorktreeValidationRunner",
    "NativeFirstBenchmarkExecutorFactory",
    "ValidationCaseEvaluator",
    "build_native_first_platform_runners",
]
