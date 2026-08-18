"""Canonical process-home paths used by infrastructure adapters."""

from __future__ import annotations

import os
from pathlib import Path


def get_VoidCube_home() -> Path:
    """Return the configured VoidCube runtime home without creating it."""
    return Path(os.getenv("VOIDCUBE_HOME", Path.home() / ".VoidCube"))


def get_default_VoidCube_root() -> Path:
    """Return the profile root used by profile-level operations."""
    native_home = Path.home() / ".VoidCube"
    env_home = os.environ.get("VOIDCUBE_HOME", "")
    if not env_home:
        return native_home
    env_path = Path(env_home)
    try:
        env_path.resolve().relative_to(native_home.resolve())
        return native_home
    except ValueError:
        pass
    if env_path.parent.name == "profiles":
        return env_path.parent.parent
    return env_path


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """Return the optional skills directory, honoring package overrides."""
    override = os.getenv("VOIDCUBE_OPTIONAL_SKILLS", "").strip()
    if override:
        return Path(override)
    if default is not None:
        return default
    return get_VoidCube_home() / "optional-skills"


def get_cache_dir(name: str) -> Path:
    """Return a named directory below the canonical cache tree."""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"Cache directory name must be one path component: {name!r}")
    return get_VoidCube_home() / "cache" / name


def display_VoidCube_home() -> str:
    """Return a user-facing representation of the configured home path."""
    home = get_VoidCube_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


def get_subprocess_home() -> str | None:
    """Return the profile HOME for subprocesses when explicitly provisioned."""
    configured_home = os.getenv("VOIDCUBE_HOME")
    if not configured_home:
        return None
    profile_home = os.path.join(configured_home, "home")
    return profile_home if os.path.isdir(profile_home) else None


def get_config_path() -> Path:
    return get_VoidCube_home() / "config.yaml"


def get_skills_dir() -> Path:
    return get_VoidCube_home() / "skills"


def get_logs_dir() -> Path:
    return get_VoidCube_home() / "logs"


def get_env_path() -> Path:
    return get_VoidCube_home() / ".env"


__all__ = [
    "display_VoidCube_home",
    "get_VoidCube_home",
    "get_cache_dir",
    "get_config_path",
    "get_default_VoidCube_root",
    "get_env_path",
    "get_logs_dir",
    "get_optional_skills_dir",
    "get_skills_dir",
    "get_subprocess_home",
]
