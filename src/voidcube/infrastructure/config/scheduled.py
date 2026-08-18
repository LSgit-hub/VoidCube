"""Scheduled execution configuration adapters."""

from __future__ import annotations

import logging
import os
from typing import Any


logger = logging.getLogger(__name__)


def scheduled_timeout_seconds(env_name: str, *, default: float) -> float:
    value: Any = os.getenv(env_name)
    if value in (None, ""):
        value = default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %.0f", env_name, value, default)
        parsed = default
    return max(0.1, min(parsed, 86400.0))


__all__ = ["scheduled_timeout_seconds"]
