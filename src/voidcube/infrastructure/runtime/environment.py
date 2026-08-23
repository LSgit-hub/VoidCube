"""Runtime environment detection without application imports."""

from __future__ import annotations

import os


def is_placeholder_secret(value: str) -> bool:
    """Return whether a value is a template secret rather than a credential."""
    normalized = str(value or "").strip().strip('"\'').lower()
    if not normalized:
        return False
    return (
        normalized in {
            "sk-your-key-here",
            "sk-or-your-key-here",
            "your-key-here",
            "your-api-key",
            "your_api_key",
            "changeme",
            "change-me",
            "placeholder",
            "***",
        }
        or "your-key" in normalized
        or "your_api_key" in normalized
        or normalized.endswith("-your-key-here")
    )

_wsl_detected: bool | None = None
_container_detected: bool | None = None


def is_termux() -> bool:
    prefix = os.getenv("PREFIX", "")
    return bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)


def is_wsl() -> bool:
    global _wsl_detected
    if _wsl_detected is not None:
        return _wsl_detected
    try:
        with open("/proc/version", "r") as handle:
            _wsl_detected = "microsoft" in handle.read().lower()
    except (FileNotFoundError, PermissionError, OSError):
        _wsl_detected = False
    return _wsl_detected


def is_container() -> bool:
    global _container_detected
    if _container_detected is not None:
        return _container_detected
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        _container_detected = True
        return True
    try:
        with open("/proc/1/cgroup", "r") as handle:
            cgroup = handle.read()
        _container_detected = any(marker in cgroup for marker in ("docker", "podman", "/lxc/"))
    except OSError:
        _container_detected = False
    return _container_detected


__all__ = ["is_container", "is_placeholder_secret", "is_termux", "is_wsl"]
