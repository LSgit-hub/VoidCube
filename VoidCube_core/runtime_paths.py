"""Canonical and legacy runtime-data paths for VoidCube services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from VoidCube_core.constants import get_VoidCube_home


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    home: Path
    runtime_root: Path
    memory_root: Path
    memory_db: Path
    supervisor_root: Path
    supervisor_governance_log: Path
    body_root: Path
    body_slots_root: Path
    body_registry: Path
    body_active_pointer: Path
    session_db: Path


@dataclass(frozen=True, slots=True)
class LegacyProjectRuntimeLayout:
    project_root: Path
    memory_db: Path
    supervisor_root: Path
    body_slots_root: Path
    body_registry: Path
    body_active_pointer: Path
    mem_governance_log: Path


def get_runtime_layout(home: str | Path | None = None) -> RuntimeLayout:
    """Return canonical runtime targets without creating filesystem entries."""
    resolved_home = Path(home) if home is not None else get_VoidCube_home()
    runtime_root = resolved_home / "runtime"
    memory_root = runtime_root / "memory"
    body_root = runtime_root / "body"
    return RuntimeLayout(
        home=resolved_home,
        runtime_root=runtime_root,
        memory_root=memory_root,
        memory_db=memory_root / "memory.db",
        supervisor_root=runtime_root / "supervisor",
        supervisor_governance_log=(
            runtime_root / "supervisor" / "mem_governance.jsonl"
        ),
        body_root=body_root,
        body_slots_root=body_root / "slots",
        body_registry=body_root / "registry.json",
        body_active_pointer=body_root / "active.json",
        session_db=resolved_home / "state.db",
    )


def get_legacy_project_runtime_layout(
    project_root: str | Path,
) -> LegacyProjectRuntimeLayout:
    """Return pre-M2 project-root locations used as migration sources only."""
    root = Path(project_root)
    return LegacyProjectRuntimeLayout(
        project_root=root,
        memory_db=root / "memory.db",
        supervisor_root=root / ".soul-runtime",
        body_slots_root=root / ".body-slots",
        body_registry=root / ".body-registry.json",
        body_active_pointer=root / ".body-active.json",
        mem_governance_log=root / "mem_governance.jsonl",
    )
