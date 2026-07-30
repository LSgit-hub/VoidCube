"""Read-only deployment preset catalog.

Preset files describe potentially destructive host operations.  This module owns
catalog loading and deliberately does not execute them until a separate
approved execution contract exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml


_PRESET_DIRECTORY = Path(__file__).with_name("presets")


def list_presets() -> list[dict[str, Any]]:
    """Return the available preset summaries in deterministic order."""
    if not _PRESET_DIRECTORY.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(_PRESET_DIRECTORY.glob("*.yaml")):
        preset = _read_preset(path)
        if preset is None:
            continue
        records.append(
            {
                "file": path.stem,
                "name": str(preset.get("name") or path.stem),
                "description": str(preset.get("description") or ""),
                "steps_count": len(_steps(preset)),
            }
        )
    return records


def load_preset(name: str) -> dict[str, Any] | None:
    """Load one catalog preset by its file stem, without path traversal."""
    normalized = str(name or "").strip().lower()
    if not normalized or any(token in normalized for token in ("/", "\\", "..")):
        return None
    path = _PRESET_DIRECTORY / f"{normalized.removesuffix('.yaml')}.yaml"
    return _read_preset(path)


def apply_preset(name: str) -> dict[str, Any]:
    """Report that deployment execution needs an explicit approved runtime."""
    preset = load_preset(name)
    if preset is None:
        return {
            "success": False,
            "reason": "preset_not_found",
            "results": [],
        }
    return {
        "success": False,
        "reason": "execution_not_available",
        "results": [],
        "preset": preset,
    }


def _read_preset(path: Path) -> dict[str, Any] | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)


def _steps(preset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_steps = preset.get("steps")
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, Mapping)]
