"""Helpers for loading VoidCube .env files consistently across entrypoints."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Track if we've already loaded the env to prevent duplicate loading
_env_loaded = False


def _load_dotenv_with_fallback(path: Path, *, override: bool) -> None:
    # Always use UTF-8 encoding for .env files
    load_dotenv(dotenv_path=path, override=override, encoding="utf-8")


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
