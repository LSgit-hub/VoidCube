"""Create an audited evolution candidate without performing promotion."""

from __future__ import annotations

import fnmatch
import inspect
import json
import logging
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Protocol

from .models import (
    AuthoringAgentReport,
    AuthoringCommandEvidence,
    EvolutionAuthoringContext,
    EvolutionAuthoringResult,
    EvolutionAuthoringSpec,
    candidate_ref_for_task,
)
from ..evolution_boundary import (
    classify_agent_evolution_changes,
    normalize_repo_path,
)
from ..evolution_evaluation.models import ExecutionEnvironmentManifest
from ...infrastructure.execution.task_execution import TaskExecutionBlocked


logger = logging.getLogger(__name__)


class AuthoringAgent(Protocol):
    def author(
        self,
        context: EvolutionAuthoringContext,
    ) -> (
        Awaitable[AuthoringAgentReport | Mapping[str, object]]
        | AuthoringAgentReport
        | Mapping[str, object]
    ): ...


PrepareEnvironment = Callable[..., Mapping[str, object]]
ReleaseEnvironment = Callable[[str], None]
TerminalRunner = Callable[..., str | Mapping[str, object]]


class EvolutionAuthoringExecutor:
    """Own worktree, verification, test, commit, and cleanup for one candidate."""

    def __init__(
        self,
        repository: str | Path,
        *,
        worktree_root: str | Path | None = None,
        prepare_environment: PrepareEnvironment | None = None,
        release_environment: ReleaseEnvironment | None = None,
        terminal_runner: TerminalRunner | None = None,
        python_executable: str | Path | None = None,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        if not (self.repository / ".git").exists():
            raise ValueError(
                f"authoring repository is not a Git worktree: {self.repository}"
            )
        self.worktree_root = (
            Path(
                worktree_root
                or self.repository.parent
                / f".{self.repository.name}-evolution-authoring"
            )
            .expanduser()
            .resolve()
        )
        if self.worktree_root == self.repository:
            raise ValueError("authoring worktree root cannot be the primary repository")
        try:
            self.worktree_root.relative_to(self.repository)
        except ValueError:
            pass
        else:
            raise ValueError(
                "authoring worktree root must be outside the primary repository"
            )
        self.python_executable = Path(
            python_executable
            or self.repository / ".venv" / "Scripts" / "python.exe"
        ).expanduser().resolve()
        self._prepare_environment = prepare_environment or self._prepare_native_environment
        self._release_environment = release_environment or _release_environment
        self._terminal_runner = terminal_runner or _terminal_runner

    def _prepare_native_environment(
        self,
        task_id: str,
        worktree: str,
        **kwargs: object,
    ) -> Mapping[str, object]:
        from ...infrastructure.execution.terminal_tool import prepare_task_native_git_worktree

        return prepare_task_native_git_worktree(
            task_id,
            worktree,
            expected_head=str(kwargs.get("expected_head") or ""),
            command_timeout_seconds=int(
                kwargs.get("command_timeout_seconds") or 120
            ),
            python_executable=str(self.python_executable),
        )

    async def execute(
        self,
        spec: EvolutionAuthoringSpec,
        *,
        agent: AuthoringAgent,
    ) -> EvolutionAuthoringResult:
        started_at = datetime.now(timezone.utc)
        worktree = self.worktree_root / spec.task_id
        environment: ExecutionEnvironmentManifest | None = None
        command_evidence: list[AuthoringCommandEvidence] = []
        changed_files: tuple[str, ...] = ()
        agent_summary = ""
        environment_started = False
        worktree_created = False
        initial_refs: dict[str, str] | None = None
        published_ref: tuple[str, str] | None = None

        try:
            baseline = _git_output(
                self.repository,
                ("rev-parse", "--verify", f"{spec.baseline_commit}^{{commit}}"),
            ).lower()
            if baseline != spec.baseline_commit:
                return self._failure(
                    spec,
                    started_at,
                    status="blocked",
                    error_code="baseline_commit_mismatch",
                    error_reason="baseline commit does not resolve to the requested full commit",
                )
            candidate_ref = candidate_ref_for_task(spec.task_id)
            if _git_ref_exists(self.repository, candidate_ref):
                return self._failure(
                    spec,
                    started_at,
                    status="blocked",
                    error_code="candidate_ref_exists",
                    error_reason=f"candidate ref already exists: {candidate_ref}",
                )
            self._create_worktree(worktree, baseline)
            worktree_created = True
            initial_refs = _git_refs(self.repository)

            raw_manifest = self._prepare_environment(
                spec.task_id,
                str(worktree),
                expected_head=baseline,
                command_timeout_seconds=spec.command_timeout_seconds,
            )
            environment_started = True
            environment = ExecutionEnvironmentManifest.model_validate(raw_manifest)
            context = EvolutionAuthoringContext(
                task_id=spec.task_id,
                objective=spec.objective,
                improvement_hypothesis=spec.improvement_hypothesis,
                baseline_commit=baseline,
                execution_workspace_path=environment.execution_workspace_path,
                allowed_paths=spec.allowed_paths,
                forbidden_patterns=spec.forbidden_patterns,
                max_files_changed=spec.max_files_changed,
                stop_conditions=(
                    "Do not edit outside allowed_paths.",
                    "Do not create a Git commit or candidate ref.",
                    "Do not claim tests passed; the executor runs every test command.",
                    "Stop when the requested code and focused tests are ready for verification.",
                ),
                environment_manifest=environment,
            )
            raw_report = agent.author(context)
            if inspect.isawaitable(raw_report):
                raw_report = await raw_report
            report = AuthoringAgentReport.model_validate(raw_report)
            agent_summary = report.summary
            if not report.completed:
                return self._failure(
                    spec,
                    started_at,
                    status="authoring_failed",
                    error_code="agent_did_not_complete",
                    error_reason=report.summary
                    or "authoring agent did not complete the edit",
                    environment=environment,
                    agent_summary=agent_summary,
                )

            git_error = _git_state_error(
                worktree, baseline, initial_refs, self.repository
            )
            if git_error:
                changed_files = _changed_files(worktree)
                return self._failure(
                    spec,
                    started_at,
                    status="policy_violation",
                    error_code=git_error[0],
                    error_reason=git_error[1],
                    environment=environment,
                    changed_files=changed_files,
                    agent_summary=agent_summary,
                )

            changed_files = _changed_files(worktree)
            git_error = _git_state_error(
                worktree, baseline, initial_refs, self.repository
            )
            if git_error:
                return self._failure(
                    spec,
                    started_at,
                    status="policy_violation",
                    error_code=git_error[0],
                    error_reason=git_error[1],
                    environment=environment,
                    changed_files=changed_files,
                    command_evidence=tuple(command_evidence),
                    agent_summary=agent_summary,
                )
            policy_error = _policy_error(spec, changed_files)
            if policy_error:
                return self._failure(
                    spec,
                    started_at,
                    status="no_changes" if not changed_files else "policy_violation",
                    error_code=policy_error[0],
                    error_reason=policy_error[1],
                    environment=environment,
                    changed_files=changed_files,
                    agent_summary=agent_summary,
                )

            for command in spec.test_commands:
                evidence, payload_status = self._run_command(spec, command)
                command_evidence.append(evidence)
                if evidence.exit_code != 0:
                    status = "blocked" if payload_status == "blocked" else "test_failed"
                    code = (
                        "test_environment_blocked"
                        if status == "blocked"
                        else "test_command_timed_out"
                        if evidence.timed_out
                        else "test_command_failed"
                    )
                    return self._failure(
                        spec,
                        started_at,
                        status=status,
                        error_code=code,
                        error_reason=f"test command failed: {command}",
                        environment=environment,
                        changed_files=changed_files,
                        command_evidence=tuple(command_evidence),
                        agent_summary=agent_summary,
                    )

            changed_files = _changed_files(worktree)
            policy_error = _policy_error(spec, changed_files)
            if policy_error:
                return self._failure(
                    spec,
                    started_at,
                    status="no_changes" if not changed_files else "policy_violation",
                    error_code=policy_error[0],
                    error_reason=policy_error[1],
                    environment=environment,
                    changed_files=changed_files,
                    command_evidence=tuple(command_evidence),
                    agent_summary=agent_summary,
                )

            add_command = "git add -A -- " + " ".join(
                shlex.quote(path) for path in changed_files
            )
            add_evidence, _ = self._run_command(spec, add_command)
            command_evidence.append(add_evidence)
            if add_evidence.exit_code != 0:
                return self._commit_failure(
                    spec,
                    started_at,
                    environment,
                    changed_files,
                    command_evidence,
                    agent_summary,
                    "git_add_failed",
                )

            commit_command = (
                "git -c user.name='VoidCube Evolution Author' "
                "-c user.email='evolution@voidcube.local' commit -m "
                + shlex.quote(spec.commit_message)
            )
            commit_evidence, _ = self._run_command(spec, commit_command)
            command_evidence.append(commit_evidence)
            if commit_evidence.exit_code != 0:
                return self._commit_failure(
                    spec,
                    started_at,
                    environment,
                    changed_files,
                    command_evidence,
                    agent_summary,
                    "git_commit_failed",
                )

            candidate_commit = _git_output(worktree, ("rev-parse", "HEAD")).lower()
            if candidate_commit == baseline:
                return self._commit_failure(
                    spec,
                    started_at,
                    environment,
                    changed_files,
                    command_evidence,
                    agent_summary,
                    "candidate_matches_baseline",
                )
            if _git_output(
                worktree,
                ("status", "--porcelain", "--untracked-files=all"),
                require_output=False,
            ):
                return self._commit_failure(
                    spec,
                    started_at,
                    environment,
                    changed_files,
                    command_evidence,
                    agent_summary,
                    "worktree_not_clean_after_commit",
                )
            committed_files = _committed_files(worktree, baseline, candidate_commit)
            if committed_files != changed_files:
                return self._commit_failure(
                    spec,
                    started_at,
                    environment,
                    changed_files,
                    command_evidence,
                    agent_summary,
                    "committed_files_mismatch",
                )
            _git_output(
                self.repository,
                ("update-ref", candidate_ref, candidate_commit),
                require_output=False,
            )
            published_ref = (candidate_ref, candidate_commit)
            identity = environment.identity()
            return EvolutionAuthoringResult.create(
                task_id=spec.task_id,
                status="candidate_created",
                baseline_commit=baseline,
                candidate_commit=candidate_commit,
                candidate_ref=candidate_ref,
                changed_files=changed_files,
                environment_manifest_id=environment.execution_environment_id,
                environment_identity_id=identity.execution_environment_identity_id,
                environment_dependency_fingerprint=environment.dependency_fingerprint,
                command_evidence=tuple(command_evidence),
                agent_summary=agent_summary,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        except TaskExecutionBlocked as exc:
            return self._failure(
                spec,
                started_at,
                status="blocked",
                error_code=exc.code,
                error_reason=exc.reason,
                environment=environment,
                changed_files=changed_files,
                command_evidence=tuple(command_evidence),
                agent_summary=agent_summary,
            )
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
            return self._failure(
                spec,
                started_at,
                status="blocked" if environment is None else "authoring_failed",
                error_code=(
                    "authoring_environment_unavailable"
                    if environment is None
                    else "authoring_execution_error"
                ),
                error_reason=f"{type(exc).__name__}: {exc}",
                environment=environment,
                changed_files=changed_files,
                command_evidence=tuple(command_evidence),
                agent_summary=agent_summary,
            )
        finally:
            if environment_started:
                try:
                    self._release_environment(spec.task_id)
                except Exception:
                    logger.warning(
                        "Failed to release authoring environment %s",
                        spec.task_id,
                        exc_info=True,
                    )
            if worktree_created:
                self._remove_worktree(worktree)
            if initial_refs is not None:
                allowed_refs = dict(initial_refs)
                if published_ref is not None:
                    allowed_refs[published_ref[0]] = published_ref[1]
                _restore_refs(self.repository, allowed_refs)

    def _create_worktree(self, target: Path, baseline: str) -> None:
        if target.exists() and any(target.iterdir()):
            raise ValueError(f"authoring worktree target is not empty: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _git_output(
            self.repository,
            ("worktree", "add", "--detach", str(target), baseline),
            require_output=False,
        )
        if _git_output(target, ("rev-parse", "HEAD")).lower() != baseline:
            raise RuntimeError("created authoring worktree has the wrong Git HEAD")

    def _remove_worktree(self, target: Path) -> None:
        try:
            _git_output(
                self.repository,
                ("worktree", "remove", "--force", str(target)),
                require_output=False,
            )
        except Exception:
            logger.warning(
                "Failed to remove authoring worktree %s", target, exc_info=True
            )

    def _run_command(
        self,
        spec: EvolutionAuthoringSpec,
        command: str,
    ) -> tuple[AuthoringCommandEvidence, str]:
        raw = self._terminal_runner(
            command,
            task_id=spec.task_id,
            timeout=spec.command_timeout_seconds,
        )
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        exit_code = int(payload.get("exit_code", -1))
        status = str(payload.get("status") or "").strip().lower()
        return (
            AuthoringCommandEvidence(
                command=command,
                exit_code=exit_code,
                output=str(payload.get("output") or payload.get("error") or "")[
                    :50_000
                ],
                timed_out=exit_code == 124 or status == "timeout",
                security_scanner_status=payload.get("security_scanner_status"),
                container_disk_quota_status=payload.get(
                    "container_disk_quota_status"
                ),
            ),
            status,
        )

    def _commit_failure(
        self,
        spec: EvolutionAuthoringSpec,
        started_at: datetime,
        environment: ExecutionEnvironmentManifest,
        changed_files: tuple[str, ...],
        command_evidence: list[AuthoringCommandEvidence],
        agent_summary: str,
        error_code: str,
    ) -> EvolutionAuthoringResult:
        return self._failure(
            spec,
            started_at,
            status="commit_failed",
            error_code=error_code,
            error_reason=error_code.replace("_", " "),
            environment=environment,
            changed_files=changed_files,
            command_evidence=tuple(command_evidence),
            agent_summary=agent_summary,
        )

    @staticmethod
    def _failure(
        spec: EvolutionAuthoringSpec,
        started_at: datetime,
        *,
        status: str,
        error_code: str,
        error_reason: str,
        environment: ExecutionEnvironmentManifest | None = None,
        changed_files: tuple[str, ...] = (),
        command_evidence: tuple[AuthoringCommandEvidence, ...] = (),
        agent_summary: str = "",
    ) -> EvolutionAuthoringResult:
        return EvolutionAuthoringResult.create(
            task_id=spec.task_id,
            status=status,
            baseline_commit=spec.baseline_commit,
            changed_files=changed_files,
            environment_manifest_id=(
                environment.execution_environment_id if environment else None
            ),
            environment_identity_id=(
                environment.identity().execution_environment_identity_id
                if environment
                else None
            ),
            environment_dependency_fingerprint=(
                environment.dependency_fingerprint if environment else None
            ),
            command_evidence=command_evidence,
            agent_summary=agent_summary,
            error_code=error_code,
            error_reason=error_reason[:4000],
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )


def _policy_error(
    spec: EvolutionAuthoringSpec,
    changed_files: tuple[str, ...],
) -> tuple[str, str] | None:
    if not changed_files:
        return "no_changes", "authoring agent produced no repository changes"
    if len(changed_files) > spec.max_files_changed:
        return (
            "max_files_exceeded",
            f"changed {len(changed_files)} files; limit is {spec.max_files_changed}",
        )
    boundary = classify_agent_evolution_changes(changed_files)
    if not boundary.ok:
        return "evolution_boundary_violation", ", ".join(boundary.violations)
    disallowed = [
        path
        for path in changed_files
        if not _matches_allowed_path(path, spec.allowed_paths)
    ]
    if disallowed:
        return "allowed_path_violation", ", ".join(disallowed)
    forbidden = [
        path
        for path in changed_files
        if any(
            fnmatch.fnmatch(path, pattern.replace("\\", "/"))
            for pattern in spec.forbidden_patterns
        )
    ]
    if forbidden:
        return "forbidden_pattern_violation", ", ".join(forbidden)
    return None


def _matches_allowed_path(path: str, allowed_paths: tuple[str, ...]) -> bool:
    return any(
        path == allowed.rstrip("/")
        if not allowed.endswith("/")
        else path.startswith(allowed)
        for allowed in allowed_paths
    )


def _changed_files(worktree: Path) -> tuple[str, ...]:
    tracked = _git_output_bytes(worktree, ("diff", "--name-only", "-z", "HEAD", "--"))
    untracked = _git_output_bytes(
        worktree,
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    )
    paths = [
        normalize_repo_path(item.decode("utf-8", errors="replace"))
        for item in (*tracked.split(b"\0"), *untracked.split(b"\0"))
        if item
    ]
    return tuple(sorted(set(paths)))


def _committed_files(worktree: Path, baseline: str, candidate: str) -> tuple[str, ...]:
    output = _git_output_bytes(
        worktree,
        ("diff", "--name-only", "-z", f"{baseline}..{candidate}", "--"),
    )
    return tuple(
        sorted(
            {
                normalize_repo_path(item.decode("utf-8", errors="replace"))
                for item in output.split(b"\0")
                if item
            }
        )
    )


def _git_ref_exists(repository: Path, ref: str) -> bool:
    result = subprocess.run(
        ("git", "show-ref", "--verify", "--quiet", ref),
        cwd=repository,
        capture_output=True,
        timeout=15,
    )
    return result.returncode == 0


def _git_refs(repository: Path) -> dict[str, str]:
    result = subprocess.run(
        ("git", "for-each-ref", "--format=%(refname)%00%(objectname)"),
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("Git refs could not be captured")
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        ref, separator, commit = line.partition("\0")
        if separator and ref and commit:
            refs[ref] = commit.lower()
    return refs


def _git_state_error(
    worktree: Path,
    baseline: str,
    initial_refs: dict[str, str],
    repository: Path,
) -> tuple[str, str] | None:
    if _git_output(worktree, ("rev-parse", "HEAD")).lower() != baseline:
        return "agent_created_commit", "authoring agent changed Git HEAD"
    if _git_refs(repository) != initial_refs:
        return "agent_modified_git_refs", "authoring agent changed repository refs"
    return None


def _restore_refs(repository: Path, expected: dict[str, str]) -> None:
    try:
        current = _git_refs(repository)
        for ref in sorted(set(current) - set(expected)):
            _git_output(repository, ("update-ref", "-d", ref), require_output=False)
        for ref, commit in expected.items():
            if current.get(ref) != commit:
                _git_output(
                    repository, ("update-ref", ref, commit), require_output=False
                )
    except Exception:
        logger.error("Failed to restore repository refs after authoring", exc_info=True)


def _git_output(
    repository: Path,
    args: tuple[str, ...],
    *,
    require_output: bool = True,
) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    output = result.stdout.strip()
    if result.returncode != 0 or (require_output and not output):
        raise RuntimeError(
            f"Git command failed ({' '.join(args)}): "
            f"{(result.stderr or result.stdout).strip()[:1000]}"
        )
    return output


def _git_output_bytes(repository: Path, args: tuple[str, ...]) -> bytes:
    result = subprocess.run(
        ("git", *args),
        cwd=repository,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed ({' '.join(args)}): "
            + result.stderr.decode("utf-8", errors="replace")[:1000]
        )
    return result.stdout


def _release_environment(task_id: str) -> None:
    from ...infrastructure.execution.terminal_tool import release_task_environment

    release_task_environment(task_id)


def _terminal_runner(command: str, **kwargs: object) -> str:
    from ...infrastructure.execution.terminal_tool import terminal_tool

    return terminal_tool(command, **kwargs)


__all__ = ["AuthoringAgent", "EvolutionAuthoringExecutor"]
