"""Timezone-aware application clock.

The clock is an infrastructure service because timezone configuration comes
from process environment or the user's config file. Domain code can depend on
the returned datetime without knowing how configuration is resolved.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from ..config.runtime_paths import get_config_path

logger = logging.getLogger(__name__)
_cached_tz: Optional[ZoneInfo] = None
_cached_tz_name: Optional[str] = None
_cache_resolved = False


def _resolve_timezone_name() -> str:
    configured = os.getenv("VOIDCUBE_TIMEZONE", "").strip()
    if configured:
        return configured
    try:
        config_path = get_config_path()
        if config_path.exists():
            with config_path.open(encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
            timezone_name = config.get("timezone", "")
            if isinstance(timezone_name, str) and timezone_name.strip():
                return timezone_name.strip()
    except Exception:
        logger.debug("Unable to resolve configured timezone", exc_info=True)
    return ""


def _get_zoneinfo(name: str) -> Optional[ZoneInfo]:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, ZoneInfoNotFoundError) as exc:
        logger.warning("Invalid timezone '%s': %s. Falling back to server local time.", name, exc)
        return None


def get_timezone() -> Optional[ZoneInfo]:
    global _cached_tz, _cached_tz_name, _cache_resolved
    if not _cache_resolved:
        _cached_tz_name = _resolve_timezone_name()
        _cached_tz = _get_zoneinfo(_cached_tz_name)
        _cache_resolved = True
    return _cached_tz


def reset_cache() -> None:
    """Forget the resolved timezone after configuration changes."""
    global _cached_tz, _cached_tz_name, _cache_resolved
    _cached_tz = None
    _cached_tz_name = None
    _cache_resolved = False


def now() -> datetime:
    timezone = get_timezone()
    return datetime.now(timezone) if timezone is not None else datetime.now().astimezone()


from zoneinfo import ZoneInfoNotFoundError

__all__ = ["get_timezone", "now", "reset_cache"]
