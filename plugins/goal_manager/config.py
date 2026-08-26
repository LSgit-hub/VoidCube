"""Goal Service configuration normalization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from voidcube.infrastructure.config.runtime_paths import get_VoidCube_home


def resolve_db_path(config: dict[str, Any]) -> Path:
    configured = config.get("db_path")
    if configured:
        return Path(str(configured)).expanduser().resolve()
    return (get_VoidCube_home() / "runtime" / "goals" / "goals.db").resolve()


def service_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(config or {})
    raw["db_path"] = str(resolve_db_path(raw))
    raw["name"] = str(raw.get("name") or "goal_manager")
    raw["service_port"] = int(raw.get("service_port") or raw.get("port") or 6003)
    raw["request_timeout_seconds"] = max(
        0.1, min(60.0, float(raw.get("request_timeout_seconds", 5.0)))
    )
    return raw
