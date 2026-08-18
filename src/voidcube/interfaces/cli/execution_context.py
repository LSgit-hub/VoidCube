"""Publish the active CLI execution environment for desktop presentation."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTEXT_PATH = Path.home() / ".VoidCube" / "run" / "desktop-execution-context.json"
_SANDBOX_BACKENDS = frozenset({"docker", "podman", "singularity", "modal", "daytona"})


def _git_branch(path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", path, "branch", "--show-current"],
            capture_output=True,
            timeout=3,
        )
        return (result.stdout or b"").decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def collect_execution_context(
    worktree_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect the configured tool backend and active CLI workspace."""
    from ...infrastructure.execution.terminal_tool import _get_env_config

    terminal = _get_env_config()
    backend = str(terminal.get("env_type") or "local").strip().lower()
    if backend in _SANDBOX_BACKENDS:
        mode = "sandbox"
    elif backend == "ssh":
        mode = "remote"
    else:
        mode = "system"

    worktree_path = str((worktree_info or {}).get("path") or "").strip()
    host_working_directory = str(
        worktree_path
        or terminal.get("host_cwd")
        or os.getenv("TERMINAL_CWD")
        or os.getcwd()
    )
    backend_working_directory = str(
        terminal.get("cwd") or host_working_directory
    )
    branch = str((worktree_info or {}).get("branch") or "").strip()
    if not branch:
        branch = _git_branch(host_working_directory)

    workspace_name = Path(host_working_directory).name or host_working_directory
    return {
        "mode": mode,
        "backend": backend,
        "hostPlatform": platform.system().lower(),
        "hostWorkingDirectory": host_working_directory,
        "backendWorkingDirectory": backend_working_directory,
        "workspaceName": workspace_name,
        "branch": branch,
        "worktree": bool(worktree_path),
        "workspaceMounted": bool(terminal.get("host_cwd")),
        "fallbackToLocal": bool(terminal.get("fallback_to_local", False)),
    }


def publish_execution_context(
    worktree_info: Mapping[str, Any] | None = None,
    *,
    path: Path = CONTEXT_PATH,
) -> dict[str, Any]:
    """Atomically publish the live desktop CLI context."""
    payload = {
        **collect_execution_context(worktree_info),
        "pid": os.getpid(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return payload


def load_execution_context(
    *,
    path: Path = CONTEXT_PATH,
    pid_alive: Callable[[int], bool] | None = None,
) -> dict[str, Any] | None:
    """Load the live CLI context, rejecting stale process ownership."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        pid = int(payload.get("pid") or 0)
        if pid <= 0 or (pid_alive is not None and not pid_alive(pid)):
            return None
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def clear_execution_context(
    pid: int,
    *,
    path: Path = CONTEXT_PATH,
) -> None:
    """Remove this CLI's context without deleting a newer session's state."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("pid") or 0) == int(pid):
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return
