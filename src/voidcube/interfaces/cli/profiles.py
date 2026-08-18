"""
Profile management for VoidCube CLI.

Profiles allow users to maintain isolated VoidCube configurations
under ``~/.VoidCube/profiles/<name>/``.  The ``default`` profile maps
to the standard ``~/.VoidCube/`` directory.

Usage::

    VoidCube --profile work         # Use the "work" profile
    VoidCube -p personal            # Use the "personal" profile

The active profile can also be set persistently via the
``~/.VoidCube/active_profile`` file (written by ``VoidCube profile use <name>``).
"""

from __future__ import annotations

from pathlib import Path
from ...infrastructure.config.runtime_paths import get_default_VoidCube_root


def resolve_profile_env(profile_name: str) -> str:
    """Resolve a profile name to its VOIDCUBE_HOME directory.

    Args:
        profile_name: The profile name (e.g. ``"work"``, ``"personal"``).
            The special name ``"default"`` returns the standard root.

    Returns:
        Absolute path to the profile's config directory as a string.

    Raises:
        FileNotFoundError: If the profile directory does not exist
            (profiles must be created with ``VoidCube profile create`` first).
        ValueError: If *profile_name* is empty or contains invalid characters.
    """
    if not profile_name or not profile_name.strip():
        raise ValueError("Profile name must not be empty")

    # Profile names may only contain alphanumeric, hyphens, underscores, and dots
    name = profile_name.strip()
    invalid = set(name) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if invalid:
        raise ValueError(
            f"Invalid profile name '{name}': "
            f"contains forbidden characters: {''.join(sorted(invalid))}"
        )

    root = get_default_VoidCube_root()

    if name.lower() == "default":
        profile_dir = root
    else:
        profile_dir = root / "profiles" / name

    if not profile_dir.exists():
        raise FileNotFoundError(
            f"Profile '{name}' not found at {profile_dir}.\n"
            f"Create it with:  VoidCube profile create {name}"
        )

    return str(profile_dir.resolve())


def list_profiles() -> dict[str, str]:
    """List all available profiles.

    Returns:
        Dict mapping profile name → resolved path.  ``"default"`` is always
        included.
    """
    root = get_default_VoidCube_root()
    profiles: dict[str, str] = {"default": str(root.resolve())}

    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for entry in sorted(profiles_dir.iterdir()):
            if entry.is_dir():
                profiles[entry.name] = str(entry.resolve())

    return profiles


def create_profile(name: str) -> str:
    """Create a new profile directory and return its path.

    Args:
        name: Profile name (alphanumeric, hyphens, underscores, dots).

    Returns:
        Absolute path to the new profile directory.

    Raises:
        ValueError: If the name is invalid.
        FileExistsError: If the profile already exists.
    """
    if not name or not name.strip():
        raise ValueError("Profile name must not be empty")

    name = name.strip()
    if name.lower() == "default":
        raise ValueError("Cannot create a profile named 'default'")

    invalid = set(name) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if invalid:
        raise ValueError(
            f"Invalid profile name '{name}': "
            f"contains forbidden characters: {''.join(sorted(invalid))}"
        )

    root = get_default_VoidCube_root()
    profile_dir = root / "profiles" / name

    # Ensure the root VoidCube home is set up with secure permissions and
    # canonical subdirectories before creating the profile.
    try:
        from ...infrastructure.config.configuration import ensure_VoidCube_home
        ensure_VoidCube_home()
    except Exception:
        # ensure_VoidCube_home may fail due to permissions, managed-mode
        # restrictions, or a missing import.  Fall back to basic mkdir.
        root.mkdir(parents=True, exist_ok=True)

    if profile_dir.exists():
        raise FileExistsError(f"Profile '{name}' already exists at {profile_dir}")

    profile_dir.mkdir(parents=True, exist_ok=False)
    return str(profile_dir.resolve())


def delete_profile(name: str) -> bool:
    """Delete a profile directory.

    Args:
        name: Profile name to delete.  ``"default"`` cannot be deleted.

    Returns:
        True if the profile was deleted, False if it didn't exist.

    Raises:
        ValueError: If attempting to delete the default profile.
    """
    if not name or name.strip().lower() == "default":
        raise ValueError("Cannot delete the default profile")

    root = get_default_VoidCube_root()
    profile_dir = root / "profiles" / name.strip()

    if not profile_dir.exists():
        return False

    import shutil
    shutil.rmtree(profile_dir)
    return True


def get_active_profile_name() -> str | None:
    """Read the sticky active profile name from ``~/.VoidCube/active_profile``.

    Returns:
        The profile name, or ``None`` if no sticky profile is set
        (i.e. the default profile is active).
    """
    root = get_default_VoidCube_root()
    active_path = root / "active_profile"
    try:
        if active_path.exists():
            name = active_path.read_text().strip()
            if name and name.lower() != "default":
                return name
    except (OSError, UnicodeDecodeError):
        pass
    return None


def set_active_profile(name: str | None) -> None:
    """Persist the active profile name to ``~/.VoidCube/active_profile``.

    Args:
        name: Profile name, or ``None`` / ``"default"`` to clear the sticky default.
    """
    root = get_default_VoidCube_root()
    active_path = root / "active_profile"

    if name is None or name.strip().lower() == "default":
        # Remove the sticky file so the default profile is used
        try:
            active_path.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        root.mkdir(parents=True, exist_ok=True)
        active_path.write_text(name.strip())
