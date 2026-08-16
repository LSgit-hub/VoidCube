"""Runtime paths owned by the standalone Mem package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MemRuntimeLayout:
    home: Path
    runtime_root: Path
    memory_root: Path
    memory_db: Path
    governance_log: Path


def get_mem_runtime_layout(home: str | Path | None = None) -> MemRuntimeLayout:
    """Resolve Mem state without importing a particular application host."""
    configured_home = (
        home
        or os.getenv("MEMAI_HOME")
        or os.getenv("VOIDCUBE_HOME")
        or Path.home() / ".VoidCube"
    )
    resolved_home = Path(configured_home)
    runtime_root = resolved_home / "runtime"
    memory_root = runtime_root / "memory"
    return MemRuntimeLayout(
        home=resolved_home,
        runtime_root=runtime_root,
        memory_root=memory_root,
        memory_db=memory_root / "memory.db",
        governance_log=runtime_root / "supervisor" / "mem_governance.jsonl",
    )


def get_legacy_memory_db(project_root: str | Path) -> Path:
    """Return the pre-runtime-layout database location for one-time migration."""
    return Path(project_root) / "memory.db"
