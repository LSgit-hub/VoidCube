"""Read-only checks for a body slot that is allowed to execute or improve."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any


_CODE_SUFFIXES = {".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}
_AGENT_ENTRYPOINT = Path("src/voidcube/runtime/agent/runner.py")


def inspect_body_execution_readiness(
    *,
    slot_id: str,
    worktree_path: str,
    expected_body_state: str | None = None,
    manifest_path: str | Path | None = None,
    require_agent_entrypoint: bool = False,
) -> dict[str, Any]:
    """Check that a slot points at a real, versioned, code-bearing workspace."""

    normalized_slot_id = str(slot_id or "").strip()
    raw_worktree = str(worktree_path or "").strip()
    worktree = Path(raw_worktree).resolve() if raw_worktree else None
    manifest = (
        Path(manifest_path).resolve()
        if manifest_path
        else (worktree.parent / "worktree-origin.json" if worktree else None)
    )
    checks: dict[str, bool] = {
        "slot_id_present": bool(normalized_slot_id),
        "worktree_exists": bool(worktree and worktree.is_dir()),
        "manifest_consistent": False,
        "git_worktree": False,
        "head_resolvable": False,
        "code_files": False,
        "agent_entrypoint": False,
        "body_state_consistent": True,
    }
    result: dict[str, Any] = {
        "ready": False,
        "reason": "slot_id_or_worktree_missing",
        "slot_id": normalized_slot_id,
        "worktree_path": str(worktree) if worktree else raw_worktree,
        "manifest_path": str(manifest) if manifest else None,
        "materialization_mode": None,
        "head_commit": None,
        "code_file_count": 0,
        "checks": checks,
    }

    if not normalized_slot_id or not worktree:
        return result
    if expected_body_state:
        checks["body_state_consistent"] = str(expected_body_state).strip().lower() in {
            "active",
            "shell",
            "candidate",
            "probe",
            "awaiting_user_consent",
            "retired",
        }

    manifest_data: dict[str, Any] = {}
    if manifest and manifest.is_file():
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest_data = loaded
        except (OSError, ValueError, TypeError):
            manifest_data = {}
    mode = str(manifest_data.get("materialization_mode") or "").strip().lower()
    result["materialization_mode"] = mode or None
    checks["manifest_consistent"] = bool(
        manifest_data
        and manifest_data.get("slot_id") == normalized_slot_id
        and Path(str(manifest_data.get("worktree_path") or "")).resolve() == worktree
        and mode in {"git_worktree", "directory_copy"}
    )

    if checks["worktree_exists"]:
        code_file_count = 0
        ignored_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", "cache", "logs", "sessions", "state"}
        for root, dirs, files in os.walk(worktree):
            dirs[:] = [name for name in dirs if name not in ignored_dirs]
            for name in files:
                if Path(name).suffix.lower() in _CODE_SUFFIXES:
                    code_file_count += 1
        result["code_file_count"] = code_file_count
        checks["code_files"] = code_file_count > 0
        checks["agent_entrypoint"] = (worktree / _AGENT_ENTRYPOINT).is_file()

        top_level = _git_top_level(worktree)
        checks["git_worktree"] = top_level == worktree
        if checks["git_worktree"]:
            result["head_commit"] = _git_head(worktree)
            checks["head_resolvable"] = bool(result["head_commit"])

    mode_ready = (
        mode == "git_worktree"
        and checks["git_worktree"]
        and checks["head_resolvable"]
    ) or (mode == "directory_copy" and checks["code_files"])
    required = {
        "slot_id_present": checks["slot_id_present"],
        "worktree_exists": checks["worktree_exists"],
        "manifest_consistent": checks["manifest_consistent"],
        "code_files": checks["code_files"],
        "body_state_consistent": checks["body_state_consistent"],
    }
    if require_agent_entrypoint:
        required["agent_entrypoint"] = checks["agent_entrypoint"]
    ready = all(required.values()) and mode_ready
    result["ready"] = ready
    if ready:
        result["reason"] = None
    elif not checks["worktree_exists"]:
        result["reason"] = "worktree_missing"
    elif not checks["manifest_consistent"]:
        result["reason"] = "worktree_manifest_inconsistent"
    elif not checks["code_files"]:
        result["reason"] = "executable_code_missing"
    elif not mode_ready:
        result["reason"] = "worktree_not_versioned"
    elif require_agent_entrypoint and not checks["agent_entrypoint"]:
        result["reason"] = "agent_entrypoint_missing"
    elif not checks["body_state_consistent"]:
        result["reason"] = "body_state_invalid"
    else:
        result["reason"] = "body_execution_not_ready"
    return result


def _git_top_level(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _git_head(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None
