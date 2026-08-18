"""Backend-aware path normalization for local, WSL, and container runtimes."""

from __future__ import annotations

import os
import platform
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from VoidCube_cli.path_utils import windows_path_to_wsl, wsl_path_to_windows
from VoidCube_app.infrastructure.runtime.environment import is_wsl

_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_WSL_MNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
_GIT_BASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(?:/(.*))?$")


def _is_native_windows() -> bool:
    return platform.system() == "Windows" and not is_wsl()


def _git_bash_path_to_windows(path: str) -> str:
    match = _GIT_BASH_DRIVE_RE.match(path)
    if not match:
        return path
    drive = match.group(1).upper()
    rest = match.group(2) or ""
    return f"{drive}:/{rest}" if rest else f"{drive}:/"


def _normalize_windows_host_path(path: str) -> str:
    if _WIN_DRIVE_RE.match(path):
        return path.replace("\\", "/")
    if path.startswith("/mnt/"):
        return wsl_path_to_windows(path).replace("\\", "/")
    if path == "/tmp" or path.startswith("/tmp/"):
        suffix = path.removeprefix("/tmp").lstrip("/")
        return str(Path(tempfile.gettempdir(), suffix)).replace("\\", "/")
    converted = _git_bash_path_to_windows(path)
    if converted != path:
        return converted
    return path


def _normalize_wsl_host_path(path: str) -> str:
    if _WIN_DRIVE_RE.match(path):
        return windows_path_to_wsl(path)
    match = _GIT_BASH_DRIVE_RE.match(path)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2) or ""
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    return path.replace("\\", "/")


def _normalize_host_path(path: str) -> str:
    if _is_native_windows():
        return _normalize_windows_host_path(path)
    if is_wsl():
        return _normalize_wsl_host_path(path)
    return path.replace("\\", "/")


def normalize_host_path(path: str) -> str:
    """Normalize WSL/Git Bash paths into the current host path syntax."""
    return _normalize_host_path(str(path or ""))


def _looks_like_absolute_host_path(path: str) -> bool:
    if not path:
        return False
    if _WIN_DRIVE_RE.match(path):
        return True
    if path.startswith("/mnt/"):
        return True
    if _GIT_BASH_DRIVE_RE.match(path):
        return True
    return os.path.isabs(path)


def _normalize_compare_key(path: str) -> str:
    normalized = _normalize_host_path(path)
    if _is_native_windows():
        return os.path.normcase(normalized.rstrip("/\\"))
    return normalized.rstrip("/")


def _host_path_within(candidate: str, root: str) -> bool:
    rel_parts = _host_relative_to(candidate, root)
    return rel_parts is not None


def _host_relative_to(candidate: str, root: str) -> tuple[str, ...] | None:
    try:
        candidate_path = Path(_normalize_host_path(candidate)).resolve(strict=False)
        root_path = Path(_normalize_host_path(root)).resolve(strict=False)
        rel = candidate_path.relative_to(root_path)
        return rel.parts
    except (OSError, ValueError):
        return None


def _resolve_local_host_path(path: str, env: Any | None) -> str | None:
    if not path or path.startswith("~"):
        return None

    normalized = _normalize_host_path(path)
    if _looks_like_absolute_host_path(normalized):
        return str(Path(normalized).expanduser().resolve(strict=False))

    base_dir = (
        os.getenv("TERMINAL_CWD")
        or getattr(env, "cwd", None)
        or os.getcwd()
    )
    return str((Path(base_dir) / normalized).expanduser().resolve(strict=False))


def _collect_backend_mounts(env: Any | None) -> list[tuple[str, str]]:
    if env is None:
        return []

    mounts: list[tuple[str, str]] = []
    workspace_host = getattr(env, "_voidcube_workspace_host_path", None)
    workspace_backend = getattr(env, "_voidcube_workspace_backend_path", "/workspace")
    home_host = getattr(env, "_voidcube_home_host_path", None)
    home_backend = getattr(env, "_voidcube_home_backend_path", "/root")

    if workspace_host:
        mounts.append((str(workspace_host), workspace_backend))
    if home_host:
        mounts.append((str(home_host), home_backend))
    return mounts


def _map_host_path_into_backend(path: str, env: Any | None) -> str | None:
    if not path or not _looks_like_absolute_host_path(path):
        return None

    normalized = _normalize_host_path(path)
    for host_root, backend_root in _collect_backend_mounts(env):
        if not _host_path_within(normalized, host_root):
            continue
        rel_parts = _host_relative_to(normalized, host_root)
        if rel_parts is None:
            continue
        return str(PurePosixPath(backend_root).joinpath(*rel_parts))
    return None


def _absolute_backend_path(path: str, env: Any | None, backend: str) -> str:
    if not path or path.startswith("~"):
        return path

    if backend == "local":
        normalized = _normalize_host_path(path)
        if _looks_like_absolute_host_path(normalized):
            return normalized
        base_dir = (
            os.getenv("TERMINAL_CWD")
            or getattr(env, "cwd", None)
            or os.getcwd()
        )
        return str((Path(base_dir) / normalized).resolve(strict=False))

    if path.startswith("/"):
        return path.replace("\\", "/")
    if _WIN_DRIVE_RE.match(path):
        # No known backend mapping for this host path; preserve it so the
        # underlying tool can report a concrete path error instead of guessing.
        return path.replace("\\", "/")

    cwd = getattr(env, "cwd", None)
    if cwd and str(cwd).startswith("/"):
        return str(PurePosixPath(str(cwd)).joinpath(path.replace("\\", "/")))
    return path.replace("\\", "/")


def _map_backend_path_to_host(path: str, env: Any | None, backend: str) -> str | None:
    if backend == "local":
        return _resolve_local_host_path(path, env)

    if not path or path.startswith("~"):
        return None

    absolute_backend = _absolute_backend_path(path, env, backend)
    if not absolute_backend.startswith("/"):
        return None

    for host_root, backend_root in _collect_backend_mounts(env):
        backend_root = backend_root.rstrip("/") or "/"
        if absolute_backend == backend_root:
            rel_parts: tuple[str, ...] = ()
        elif absolute_backend.startswith(backend_root + "/"):
            rel_parts = tuple(
                part for part in PurePosixPath(absolute_backend).relative_to(PurePosixPath(backend_root)).parts
            )
        else:
            continue
        return str(Path(host_root, *rel_parts).resolve(strict=False))
    return None


@dataclass(frozen=True)
class RuntimePath:
    """A path resolved for the active execution backend."""

    original_path: str
    backend_path: str
    host_path: str | None
    tracking_path: str


def resolve_runtime_path(path: str, env: Any | None) -> RuntimePath:
    """Normalize a user-supplied path for the active execution backend."""
    raw_path = str(path or "")
    backend = str(getattr(env, "_voidcube_active_backend", "local") or "local").lower()

    if backend == "local":
        backend_path = _absolute_backend_path(raw_path, env, backend)
    else:
        backend_path = _map_host_path_into_backend(raw_path, env) or raw_path.replace("\\", "/")
        backend_path = _absolute_backend_path(backend_path, env, backend)

    host_path = _map_backend_path_to_host(backend_path, env, backend)
    tracking_path = host_path or backend_path or raw_path

    return RuntimePath(
        original_path=raw_path,
        backend_path=backend_path,
        host_path=host_path,
        tracking_path=tracking_path,
    )
