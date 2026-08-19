"""Read-only source collector for shadow self-cognition snapshots."""

from __future__ import annotations

import ast
import hashlib
import platform
import subprocess
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    HealthMetric,
    ModuleDependency,
    RuntimeCapability,
    SelfCognitionSnapshot,
)
from .repository import SelfCognitionRepository


DEFAULT_COLLECTOR_VERSION = "self-cognition-collector/1"
_CONFIG_FILES = (
    "config.yaml",
    "pyproject.toml",
    "requirements.txt",
    ".python-version",
)
_SOURCE_ROOTS = (
    "src/voidcube",
)
_SOURCE_MODULE_PREFIXES = {
    "src/voidcube": "voidcube",
}
_MEMORY_ROOTS = ("Mem", "memai")
_SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


class SelfCognitionCollector:
    """Collect a source snapshot without executing application behavior."""

    def __init__(
        self,
        root: str | Path,
        *,
        body_id: str = "source-body",
        collector_version: str = DEFAULT_COLLECTOR_VERSION,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.body_id = str(body_id).strip() or "source-body"
        self.collector_version = str(collector_version).strip() or DEFAULT_COLLECTOR_VERSION
        self._command_runner = command_runner or subprocess.run

    def collect(
        self,
        *,
        collected_at: datetime | None = None,
    ) -> SelfCognitionSnapshot:
        timestamp = collected_at or datetime.now(timezone.utc)
        git_commit, git_gap = self._git_commit()
        modules, module_gaps = self._collect_modules()
        capabilities, capability_gaps = self._collect_capabilities()
        health_metrics, health_gaps = self._collect_health_metrics(
            git_commit=git_commit,
            modules=modules,
        )
        known_gaps = tuple(
            sorted(
                {
                    *git_gap,
                    *module_gaps,
                    *capability_gaps,
                    *health_gaps,
                }
            )
        )
        uncovered_areas = tuple(
            sorted(
                root_name
                for root_name in (*_SOURCE_ROOTS, *_MEMORY_ROOTS)
                if not self._path_for_root(root_name).exists()
            )
        )
        return SelfCognitionSnapshot.create(
            body_id=self.body_id,
            git_commit=git_commit,
            config_digest=self._config_digest(),
            modules=tuple(modules),
            capabilities=tuple(capabilities),
            health_metrics=tuple(health_metrics),
            known_gaps=known_gaps,
            uncovered_areas=uncovered_areas,
            collector_version=self.collector_version,
            collected_at=timestamp,
        )

    def collect_and_store(
        self,
        repository: SelfCognitionRepository,
        *,
        collected_at: datetime | None = None,
    ) -> SelfCognitionSnapshot:
        """Collect and persist one shadow snapshot through the injected repository."""
        snapshot = self.collect(collected_at=collected_at)
        return repository.put(snapshot)

    def _git_commit(self) -> tuple[str, tuple[str, ...]]:
        try:
            result = self._command_runner(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown", ("git_commit_unavailable",)
        commit = str(result.stdout or "").strip()
        if result.returncode != 0 or not commit:
            return "unknown", ("git_commit_unavailable",)
        return commit, ()

    def _collect_modules(self) -> tuple[list[ModuleDependency], tuple[str, ...]]:
        modules: list[ModuleDependency] = []
        gaps: set[str] = set()
        for root_name in _SOURCE_ROOTS:
            source_root = self._path_for_root(root_name)
            if not source_root.is_dir():
                gaps.add(f"source_root_missing:{root_name}")
                continue
            for path in sorted(self._python_files(source_root)):
                relative = path.relative_to(source_root).with_suffix("")
                module_name = ".".join(
                    (_SOURCE_MODULE_PREFIXES[root_name], *relative.parts)
                )
                if module_name.endswith(".__init__"):
                    module_name = module_name.removesuffix(".__init__")
                dependencies, parse_error = self._imports_for(path)
                if parse_error:
                    gaps.add(f"module_parse_failed:{module_name}")
                modules.append(
                    ModuleDependency(
                        module=module_name,
                        dependencies=tuple(sorted(dependencies)),
                    )
                )
        return modules, tuple(sorted(gaps))

    def _imports_for(self, path: Path) -> tuple[set[str], bool]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            return set(), True
        dependencies: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                dependencies.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                dependencies.add(node.module.split(".", 1)[0])
        return dependencies, False

    def _collect_capabilities(
        self,
    ) -> tuple[list[RuntimeCapability], tuple[str, ...]]:
        capabilities = [
            RuntimeCapability(
                name="python",
                capability_type="runtime",
                version=platform.python_version(),
            ),
            RuntimeCapability(
                name="self_cognition_collector",
                capability_type="collector",
                version=self.collector_version,
            ),
        ]
        git_version, git_gaps = self._command_version(["git", "--version"])
        capabilities.append(
            RuntimeCapability(
                name="git",
                capability_type="vcs",
                version=git_version,
                available=git_version is not None,
            )
        )
        for root_name, capability_name, capability_type in (
            ("skills", "skills", "skill"),
            ("src/voidcube/extensions/tools", "tools", "tool"),
        ):
            path = self._path_for_root(root_name)
            if not path.is_dir():
                continue
            for entry in sorted(path.iterdir(), key=lambda item: item.name):
                if entry.name.startswith("."):
                    continue
                capabilities.append(
                    RuntimeCapability(
                        name=f"{capability_name}:{entry.name}",
                        capability_type=capability_type,
                        available=True,
                        evidence_refs=(str(entry.relative_to(self.root)),),
                    )
                )
        return capabilities, git_gaps

    def _collect_health_metrics(
        self,
        *,
        git_commit: str,
        modules: list[ModuleDependency],
    ) -> tuple[list[HealthMetric], tuple[str, ...]]:
        metrics: list[HealthMetric] = []
        gaps: set[str] = set()

        def path_metric(name: str, relative_path: str, evidence: str) -> None:
            present = self._path_for_root(relative_path).exists()
            metrics.append(
                HealthMetric(
                    name=name,
                    value=1.0 if present else 0.0,
                    unit="presence_ratio",
                    status="healthy" if present else "failed",
                    evidence_refs=(evidence,),
                )
            )
            if not present:
                gaps.add(f"health_failed:{name}")

        path_metric("startup_entrypoint", "src/voidcube/interfaces/cli/root_launcher.py", "src/voidcube/interfaces/cli/root_launcher.py")
        path_metric("configuration", "config.yaml", "config.yaml")
        path_metric("memory_runtime", "Mem", "Mem/")
        path_metric("test_suite", "tests", "tests/")
        metrics.append(
            HealthMetric(
                name="git_revision",
                value=1.0 if git_commit != "unknown" else 0.0,
                unit="presence_ratio",
                status="healthy" if git_commit != "unknown" else "unknown",
                evidence_refs=("git:rev-parse HEAD",),
            )
        )
        metrics.append(
            HealthMetric(
                name="module_inventory",
                value=float(len(modules)),
                unit="modules",
                status="healthy" if modules else "failed",
                evidence_refs=("static:python-ast",),
            )
        )
        if not modules:
            gaps.add("health_failed:module_inventory")
        return metrics, tuple(sorted(gaps))

    def _config_digest(self) -> str:
        digest = hashlib.sha256()
        found = False
        for relative_name in _CONFIG_FILES:
            path = self.root / relative_name
            if not path.is_file():
                continue
            found = True
            digest.update(relative_name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        if not found:
            digest.update(b"no-safe-config-files")
        return digest.hexdigest()

    def _command_version(self, command: list[str]) -> tuple[str | None, tuple[str, ...]]:
        try:
            result = self._command_runner(
                command,
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None, ("command_unavailable:" + command[0],)
        if result.returncode != 0:
            return None, ("command_unavailable:" + command[0],)
        output = str(result.stdout or "").strip()
        return output or None, ()

    def _path_for_root(self, relative_path: str) -> Path:
        return self.root / relative_path

    def _python_files(self, root: Path) -> Iterable[Path]:
        for path in root.rglob("*.py"):
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            yield path


__all__ = ["DEFAULT_COLLECTOR_VERSION", "SelfCognitionCollector"]
