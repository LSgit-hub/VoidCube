"""Filesystem boundary for endogenous Supervisor state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...infrastructure.persistence.file_store import atomic_json_write


@dataclass(frozen=True, slots=True)
class EndogenousStatePaths:
    """Resolved locations for the Supervisor's endogenous state snapshots."""

    drive_history: Path
    governance_events: Path
    cognition_state: Path
    self_regulation: Path


class EndogenousStateRepository:
    """Read and atomically persist JSON objects without applying domain policy."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._paths = EndogenousStatePaths(
            drive_history=self._root / "endogenous_drive_history.json",
            governance_events=self._root / "endogenous_governance_events.json",
            cognition_state=self._root / "endogenous_cognition_state.json",
            self_regulation=self._root / "endogenous_self_regulation.json",
        )

    @property
    def root(self) -> Path:
        return self._root

    @property
    def paths(self) -> EndogenousStatePaths:
        return self._paths

    def read_object(self, path: Path) -> dict[str, Any] | None:
        """Return a JSON object, or ``None`` for absent or invalid persisted data."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return dict(raw) if isinstance(raw, dict) else None

    def write_object(self, path: Path, payload: dict[str, Any]) -> None:
        """Atomically replace a snapshot after creating its runtime root."""
        self._root.mkdir(parents=True, exist_ok=True)
        atomic_json_write(path, payload)


