"""Helpers for loading VoidCube .env files consistently across entrypoints."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

# Track if we've already loaded the env to prevent duplicate loading
_env_loaded = False


def _load_dotenv_with_fallback(path: Path, *, override: bool) -> None:
    # Always use UTF-8 encoding for .env files.  Load manually so template
    # placeholders copied from example files never mask a real process/user env.
    values = dotenv_values(dotenv_path=path, encoding="utf-8")
    for key, value in values.items():
        if not key or value is None:
            continue
        if is_placeholder_secret(value):
            continue
        if override or key not in os.environ:
            os.environ[key] = value


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


def load_VoidCube_dotenv(
    *,
    VoidCube_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
    force_reload: bool = False,
) -> list[Path]:
    """Load VoidCube environment files with user config taking precedence.

    Behavior:
    - `~/.VoidCube/.env` (user config) takes highest priority
    - project `.env` acts as a dev fallback and only fills missing values when
      the user env exists
    - if no user env exists, the project `.env` also overrides stale shell vars
    - if user env doesn't exist, create it from project .env.local or .env.example
    - Prevents duplicate loading unless force_reload is True
    """
    global _env_loaded
    
    # Skip loading if already loaded and not forcing reload
    if _env_loaded and not force_reload:
        return []
    
    loaded: list[Path] = []
    user_env_loaded = False

    home_path = Path(VoidCube_home or os.getenv("VOIDCUBE_HOME", Path.home() / ".VoidCube"))
    user_env = home_path / ".env"
    project_env_path = Path(project_env) if project_env else None

    # Create user env from fallback if it doesn't exist
    if not user_env.exists():
        # Try to find a fallback env file to copy from
        fallback_sources = []
        if project_env_path and project_env_path.exists():
            fallback_sources.append(project_env_path)
        
        # Also check for common fallback files
        for fallback_name in [".env.local", ".env.example"]:
            if project_env_path:
                fallback = project_env_path.parent / fallback_name
                if fallback.exists():
                    fallback_sources.append(fallback)
        
        # Copy the first available fallback
        for source in fallback_sources:
            try:
                home_path.mkdir(parents=True, exist_ok=True)
                content = source.read_text(encoding="utf-8")
                user_env.write_text(content, encoding="utf-8")
                break
            except Exception:
                continue

    if user_env.exists():
        _load_dotenv_with_fallback(user_env, override=True)
        loaded.append(user_env)
        user_env_loaded = True

    if project_env_path and project_env_path.exists():
        # Only override if user env is not loaded, otherwise just fill missing
        override_mode = not user_env_loaded
        _load_dotenv_with_fallback(project_env_path, override=override_mode)
        loaded.append(project_env_path)

    # Mark as loaded
    _env_loaded = True

    return loaded
