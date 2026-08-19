from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, Field

ProbeCheckName = Literal[
    "startup_ok",
    "config_load_ok",
    "memory_path_ok",
    "tool_smoke_ok",
    "task_replay_ok",
]
ProbeStatus = Literal["passed", "failed"]

DEFAULT_REQUIRED_PROBE_CHECKS: tuple[ProbeCheckName, ...] = (
    "startup_ok",
    "config_load_ok",
    "memory_path_ok",
    "tool_smoke_ok",
    "task_replay_ok",
)


class ProbeCheckResult(BaseModel):
    name: ProbeCheckName
    passed: bool
    details: Dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    summary: Optional[str] = None


class ProbeReport(BaseModel):
    slot_id: str
    overall_passed: bool
    overall_status: ProbeStatus
    checks: list[ProbeCheckResult] = Field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    required_check_count: int = 0
    missing_required_checks: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    summary: Optional[str] = None
    source_branch: Optional[str] = None
    source_commit: Optional[str] = None
    candidate_branch: Optional[str] = None
    candidate_commit: Optional[str] = None
    active_ref: Optional[str] = None
    rollback_ref: Optional[str] = None
    rollback_commit: Optional[str] = None
    diff_summary: str = ""
    changed_files: list[str] = Field(default_factory=list)


class ProbeExecutionContext(BaseModel):
    slot_id: str
    repo_root: str
    worktree_path: str
    runtime_path: str
    logs_path: str
    entrypoint_path: str
    config_path: str
    soul_store_path: str
    strict_task_replay: bool = False
    task_replay_command: Optional[list[str]] = None
    startup_command: Optional[list[str]] = None


class ProbeRunner:
    """Build normalized probe reports for candidate bodies.

    This is a protocol layer, not a full process inspector. It turns raw
    health-check facts into a stable report shape the governor can trust.
    """

    def __init__(
        self,
        *,
        required_checks: Iterable[ProbeCheckName] = DEFAULT_REQUIRED_PROBE_CHECKS,
    ) -> None:
        self.required_checks = tuple(required_checks)

    def build_report(
        self,
        slot_id: str,
        checks: Iterable[ProbeCheckResult | Dict[str, Any]],
        *,
        summary: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        source_branch: Optional[str] = None,
        source_commit: Optional[str] = None,
        candidate_branch: Optional[str] = None,
        candidate_commit: Optional[str] = None,
        active_ref: Optional[str] = None,
        rollback_ref: Optional[str] = None,
        rollback_commit: Optional[str] = None,
        diff_summary: str = "",
        changed_files: Optional[Iterable[str]] = None,
    ) -> ProbeReport:
        normalized_checks = [self._normalize_check(item) for item in checks]
        check_names = {check.name for check in normalized_checks}

        missing_required = [
            name for name in self.required_checks if name not in check_names
        ]
        for name in missing_required:
            normalized_checks.append(
                ProbeCheckResult(
                    name=name,
                    passed=False,
                    required=True,
                    summary="Required probe check was not supplied.",
                )
            )

        required_failures = [
            check for check in normalized_checks if check.required and not check.passed
        ]
        overall_passed = not required_failures
        final_summary = summary or self._default_summary(
            overall_passed=overall_passed,
            failed_checks=required_failures,
            missing_required=missing_required,
        )

        return ProbeReport(
            slot_id=slot_id,
            overall_passed=overall_passed,
            overall_status="passed" if overall_passed else "failed",
            checks=normalized_checks,
            passed_count=sum(1 for check in normalized_checks if check.passed),
            failed_count=sum(1 for check in normalized_checks if not check.passed),
            required_check_count=sum(1 for check in normalized_checks if check.required),
            missing_required_checks=list(missing_required),
            started_at=started_at or datetime.utcnow(),
            completed_at=completed_at or datetime.utcnow(),
            summary=final_summary,
            source_branch=source_branch,
            source_commit=source_commit,
            candidate_branch=candidate_branch,
            candidate_commit=candidate_commit,
            active_ref=active_ref,
            rollback_ref=rollback_ref,
            rollback_commit=rollback_commit,
            diff_summary=diff_summary,
            changed_files=[str(path) for path in changed_files or []],
        )

    def _normalize_check(self, item: ProbeCheckResult | Dict[str, Any]) -> ProbeCheckResult:
        if isinstance(item, ProbeCheckResult):
            return item
        return ProbeCheckResult.model_validate(item)

    def _default_summary(
        self,
        *,
        overall_passed: bool,
        failed_checks: list[ProbeCheckResult],
        missing_required: list[str],
    ) -> str:
        if overall_passed:
            return "Probe checks passed and the candidate is eligible for governor review."
        if missing_required:
            return (
                "Probe failed because required checks were missing: "
                + ", ".join(missing_required)
            )
        failed_names = ", ".join(check.name for check in failed_checks)
        return f"Probe failed required checks: {failed_names}"


class ProbeExecutor:
    """Run a minimal real probe against a candidate/probe body slot."""

    def build_context(
        self,
        *,
        slot_id: str,
        repo_root: str | Path,
        worktree_path: str | Path,
        runtime_path: str | Path,
        logs_path: str | Path,
        soul_store_path: str | Path,
        options: Optional[Dict[str, Any]] = None,
    ) -> ProbeExecutionContext:
        options = options or {}
        repo_root = Path(repo_root).resolve()
        worktree_path = Path(worktree_path).resolve()
        runtime_path = Path(runtime_path).resolve()
        logs_path = Path(logs_path).resolve()
        soul_store_path = Path(soul_store_path).resolve()

        entrypoint_path = Path(
            options.get(
                "entrypoint_path",
                worktree_path / "src" / "voidcube" / "interfaces" / "cli" / "root_launcher.py",
            )
        ).resolve()
        config_path = Path(
            options.get("config_path", worktree_path / "config.yaml")
        ).resolve()

        startup_command = options.get("startup_command")
        if startup_command is not None:
            startup_command = [str(part) for part in startup_command]

        task_replay_command = options.get("task_replay_command")
        if task_replay_command is not None:
            task_replay_command = [str(part) for part in task_replay_command]

        return ProbeExecutionContext(
            slot_id=slot_id,
            repo_root=str(repo_root),
            worktree_path=str(worktree_path),
            runtime_path=str(runtime_path),
            logs_path=str(logs_path),
            entrypoint_path=str(entrypoint_path),
            config_path=str(config_path),
            soul_store_path=str(soul_store_path),
            strict_task_replay=bool(options.get("strict_task_replay", False)),
            task_replay_command=task_replay_command,
            startup_command=startup_command,
        )

    def run(self, context: ProbeExecutionContext) -> ProbeReport:
        started_at = datetime.utcnow()
        checks = [
            self._check_startup(context),
            self._check_config(context),
            self._check_memory_path(context),
            self._check_tool_smoke(context),
            self._check_task_replay(context),
        ]
        completed_at = datetime.utcnow()
        return ProbeRunner().build_report(
            context.slot_id,
            checks,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _check_startup(self, context: ProbeExecutionContext) -> ProbeCheckResult:
        entrypoint = Path(context.entrypoint_path)
        runtime_dir = Path(context.runtime_path)

        if context.startup_command:
            result = self._run_command(
                context.startup_command,
                cwd=Path(context.worktree_path),
            )
            return ProbeCheckResult(
                name="startup_ok",
                passed=result["returncode"] == 0,
                details=result,
                summary="startup_command executed",
            )

        passed = entrypoint.exists() and runtime_dir.exists()
        return ProbeCheckResult(
            name="startup_ok",
            passed=passed,
            details={
                "entrypoint_path": str(entrypoint),
                "entrypoint_exists": entrypoint.exists(),
                "runtime_path": str(runtime_dir),
                "runtime_exists": runtime_dir.exists(),
            },
            summary="Entrypoint and runtime directory presence check.",
        )

    def _check_config(self, context: ProbeExecutionContext) -> ProbeCheckResult:
        config_path = Path(context.config_path)
        passed = config_path.exists() and config_path.is_file()
        details = {
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
        }
        if passed:
            try:
                details["config_size"] = config_path.stat().st_size
            except OSError:
                pass
        return ProbeCheckResult(
            name="config_load_ok",
            passed=passed,
            details=details,
            summary="Config file existence check.",
        )

    def _check_memory_path(self, context: ProbeExecutionContext) -> ProbeCheckResult:
        soul_store = Path(context.soul_store_path)
        passed = soul_store.exists() and soul_store.is_dir()
        return ProbeCheckResult(
            name="memory_path_ok",
            passed=passed,
            details={
                "soul_store_path": str(soul_store),
                "soul_store_exists": soul_store.exists(),
            },
            summary="Soul store directory availability check.",
        )

    def _check_tool_smoke(self, context: ProbeExecutionContext) -> ProbeCheckResult:
        repo_root = Path(context.worktree_path)
        expected = [
            repo_root / "src" / "voidcube" / "extensions" / "tools" / "__init__.py",
            repo_root / "src" / "voidcube" / "extensions" / "tools" / "model_tools.py",
        ]
        missing = [str(path) for path in expected if not path.exists()]
        return ProbeCheckResult(
            name="tool_smoke_ok",
            passed=not missing,
            details={
                "expected_files": [str(path) for path in expected],
                "missing_files": missing,
            },
            summary="Minimal tool surface file presence check.",
        )

    def _check_task_replay(self, context: ProbeExecutionContext) -> ProbeCheckResult:
        if context.task_replay_command:
            result = self._run_command(
                context.task_replay_command,
                cwd=Path(context.worktree_path),
            )
            return ProbeCheckResult(
                name="task_replay_ok",
                passed=result["returncode"] == 0,
                details=result,
                summary="task_replay_command executed",
            )

        if context.strict_task_replay:
            return ProbeCheckResult(
                name="task_replay_ok",
                passed=False,
                details={"strict_task_replay": True},
                summary="Strict replay mode requires a task_replay_command.",
            )

        return ProbeCheckResult(
            name="task_replay_ok",
            passed=True,
            details={"mode": "stub"},
            summary="Phase 1 stub replay accepted because no replay command was configured.",
        )

    def _run_command(self, command: list[str], *, cwd: Path) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=20,
            )
            return {
                "command": command,
                "cwd": str(cwd),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except Exception as exc:
            return {
                "command": command,
                "cwd": str(cwd),
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
            }
