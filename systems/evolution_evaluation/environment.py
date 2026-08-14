"""Execution-environment identity for reproducible evolution evidence."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from systems.evolution_evaluation.models import (
    ExecutionEnvironmentManifest,
    RuntimeToolIdentity,
    WorkspacePathMapping,
)


_DEPENDENCY_FILENAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pylock.toml",
    "poetry.lock",
    "uv.lock",
}
_IGNORED_PARTS = {".git", ".venv", "node_modules", "runtime", "__pycache__"}
_TOOL_NAMES = ("git", "python", "pytest", "node", "npm")


def dependency_fingerprint(workspace: str | Path) -> str:
    """Hash dependency declarations without depending on installed packages."""
    root = Path(workspace).resolve()
    entries: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _IGNORED_PARTS for part in path.parts):
            continue
        name = path.name.lower()
        if name not in _DEPENDENCY_FILENAMES and not (
            name.startswith("requirements") and name.endswith(".txt")
        ) and name != "pyproject.toml":
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        entries.append(
            {"path": path.relative_to(root).as_posix(), "sha256": digest}
        )
    canonical = json.dumps(
        sorted(entries, key=lambda item: item["path"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capture_host_runtime_tools(workspace: str | Path) -> tuple[RuntimeToolIdentity, ...]:
    root = Path(workspace).resolve()
    identities = [
        _host_command_identity("git", ("git", "--version"), cwd=root),
        RuntimeToolIdentity(
            scope="host",
            name="python",
            available=True,
            executable=str(Path(sys.executable).resolve()),
            version=platform.python_version(),
        ),
        _host_pytest_identity(),
        _host_command_identity("node", ("node", "--version"), cwd=root),
        _host_command_identity("npm", ("npm", "--version"), cwd=root),
    ]
    return tuple(identities)


def capture_host_environment_manifest(
    workspace: str | Path,
    *,
    repository_head: str | None = None,
) -> ExecutionEnvironmentManifest:
    root = Path(workspace).resolve()
    head = str(repository_head or _git_head(root)).strip()
    system = platform.system() or "unknown"
    return ExecutionEnvironmentManifest.create(
        backend="local",
        validation_scope="host",
        host_os=f"{system} {platform.release()}".strip(),
        execution_os=f"{system} {platform.release()}".strip(),
        architecture=platform.machine() or "unknown",
        host_workspace_path=str(root),
        execution_workspace_path=str(root),
        path_mappings=(
            WorkspacePathMapping(host_path=str(root), execution_path=str(root)),
        ),
        tools=capture_host_runtime_tools(root),
        repository_head=head,
        dependency_fingerprint=dependency_fingerprint(root),
        validated_platforms=(_platform_key(system),),
    )


def build_container_environment_manifest(
    workspace: str | Path,
    *,
    backend: str,
    execution_workspace_path: str,
    probe: Mapping[str, object],
) -> ExecutionEnvironmentManifest:
    """Combine trusted host facts with tool identities probed inside a sandbox."""
    root = Path(workspace).resolve()
    execution_system = str(probe.get("os_name") or "unknown").strip()
    execution_release = str(probe.get("os_release") or "").strip()
    repository_head = str(probe.get("repository_head") or "").strip()
    if not repository_head:
        raise ValueError("sandbox environment probe did not return a repository HEAD")
    raw_tools = probe.get("tools")
    raw_tools = raw_tools if isinstance(raw_tools, Mapping) else {}
    execution_tools = tuple(
        _execution_tool_identity(name, raw_tools.get(name)) for name in _TOOL_NAMES
    )
    host_system = platform.system() or "unknown"
    return ExecutionEnvironmentManifest.create(
        backend=str(backend).strip().lower(),
        validation_scope="container",
        host_os=f"{host_system} {platform.release()}".strip(),
        execution_os=f"{execution_system} {execution_release}".strip(),
        architecture=str(probe.get("architecture") or "unknown").strip(),
        host_workspace_path=str(root),
        execution_workspace_path=str(execution_workspace_path).strip(),
        path_mappings=(
            WorkspacePathMapping(
                host_path=str(root),
                execution_path=str(execution_workspace_path).strip(),
            ),
        ),
        tools=(*capture_host_runtime_tools(root), *execution_tools),
        repository_head=repository_head,
        dependency_fingerprint=dependency_fingerprint(root),
        validated_platforms=(_platform_key(execution_system),),
    )


def _host_command_identity(
    name: str,
    command: tuple[str, ...],
    *,
    cwd: Path,
) -> RuntimeToolIdentity:
    executable = shutil.which(command[0])
    if not executable:
        return RuntimeToolIdentity(scope="host", name=name, available=False)
    try:
        result = subprocess.run(
            (executable, *command[1:]),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return RuntimeToolIdentity(scope="host", name=name, available=False)
    version = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not version:
        return RuntimeToolIdentity(scope="host", name=name, available=False)
    return RuntimeToolIdentity(
        scope="host",
        name=name,
        available=True,
        executable=str(Path(executable).resolve()),
        version=version[0][:300],
    )


def _host_pytest_identity() -> RuntimeToolIdentity:
    try:
        version = importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        return RuntimeToolIdentity(scope="host", name="pytest", available=False)
    return RuntimeToolIdentity(
        scope="host",
        name="pytest",
        available=True,
        executable=f"{Path(sys.executable).resolve()} -m pytest",
        version=version,
    )


def _execution_tool_identity(name: str, value: object) -> RuntimeToolIdentity:
    item = value if isinstance(value, Mapping) else {}
    executable = str(item.get("executable") or "").strip()
    version = str(item.get("version") or "").strip()
    available = bool(executable and version)
    return RuntimeToolIdentity(
        scope="execution",
        name=name,
        available=available,
        executable=executable if available else "",
        version=version[:300] if available else "",
    )


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("host Git HEAD could not be captured") from exc
    head = result.stdout.strip()
    if result.returncode != 0 or not head:
        raise ValueError("host Git HEAD could not be captured")
    return head


def _platform_key(value: str) -> str:
    normalized = str(value or "unknown").strip().lower()
    if normalized.startswith("win"):
        return "windows"
    if normalized.startswith("linux"):
        return "linux"
    if normalized in {"darwin", "macos", "mac"}:
        return "macos"
    return normalized or "unknown"


__all__ = [
    "build_container_environment_manifest",
    "capture_host_environment_manifest",
    "capture_host_runtime_tools",
    "dependency_fingerprint",
]
