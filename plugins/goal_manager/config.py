"""Goal Service configuration normalization."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from voidcube.infrastructure.config.runtime_paths import get_VoidCube_home
from voidcube.infrastructure.security.review_sessions import resolve_review_session_path


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float, field: str) -> float:
    try:
        result = float(default if value is None else value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return max(minimum, min(maximum, result))


def resolve_db_path(config: dict[str, Any]) -> Path:
    configured = config.get("db_path")
    if configured:
        return Path(str(configured)).expanduser().resolve()
    return (get_VoidCube_home() / "runtime" / "goals" / "goals.db").resolve()


def service_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(config or {})
    raw["db_path"] = str(resolve_db_path(raw))
    raw["name"] = str(raw.get("name") or "goal_manager")
    raw["human_review_token"] = str(raw.get("human_review_token") or "").strip()
    raw["review_session_db_path"] = str(resolve_review_session_path(raw.get("review_session_db_path")))
    configured_port = raw.get("service_port", raw.get("port", 6003))
    if isinstance(configured_port, bool) or isinstance(configured_port, float):
        raise ValueError("service_port must be an integer")
    try:
        raw["service_port"] = int(configured_port)
    except (TypeError, ValueError):
        raise ValueError("service_port must be an integer") from None
    if not 1 <= raw["service_port"] <= 65535:
        raise ValueError("service_port must be between 1 and 65535")
    raw["request_timeout_seconds"] = _bounded_float(
        raw.get("request_timeout_seconds"),
        default=5.0,
        minimum=0.1,
        maximum=60.0,
        field="request_timeout_seconds",
    )
    raw["gateway_health_interval_seconds"] = _bounded_float(
        raw.get("gateway_health_interval_seconds"),
        default=30.0,
        minimum=5.0,
        maximum=3600.0,
        field="gateway_health_interval_seconds",
    )
    return raw
