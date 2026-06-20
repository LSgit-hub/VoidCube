"""Path Utilities for Cross-Platform Compatibility.

This module provides consistent path handling across Windows and WSL2 environments.
"""

import os
import re
import platform
from pathlib import Path
from typing import Optional, Union

from VoidCube_core.constants import is_wsl


def is_windows() -> bool:
    """Check if running on Windows (native, not WSL).

    Returns:
        True if running on Windows
    """
    return platform.system() == 'Windows' and not is_wsl()


def wsl_path_to_windows(wsl_path: str) -> str:
    """Convert WSL path to Windows path.

    Args:
        wsl_path: WSL path like /mnt/c/Users/...

    Returns:
        Windows path like C:\\Users\\...
    """
    # Match /mnt/[drive_letter]/... pattern
    match = re.match(r'^/mnt/([a-z])(/.*)?$', wsl_path)
    if match:
        drive = match.group(1).upper()
        rest = match.group(2) or ''
        rest = rest.replace('/', '\\')
        return f"{drive}:{rest}"
    return wsl_path


def windows_path_to_wsl(win_path: str) -> str:
    """Convert Windows path to WSL path.

    Args:
        win_path: Windows path like C:\\Users\\... or C:/Users/...

    Returns:
        WSL path like /mnt/c/Users/...
    """
    # Handle drive letter format (C:\... or C:/...)
    match = re.match(r'^([a-zA-Z]):[/\\](.*)?$', win_path)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2) or ''
        rest = rest.replace('\\', '/')
        return f"/mnt/{drive}/{rest}"
    return win_path.replace('\\', '/')


def normalize_path(path: Union[str, Path]) -> str:
    """Normalize path for current environment.

    Args:
        path: Path string or Path object

    Returns:
        Normalized path string
    """
    path_str = str(path)

    # On WSL, convert Windows paths to WSL format
    if is_wsl():
        if ':' in path_str and not path_str.startswith('/mnt/'):
            return windows_path_to_wsl(path_str)
        return path_str.replace('\\', '/')

    # On Windows, ensure Windows format
    if is_windows():
        if path_str.startswith('/mnt/'):
            return wsl_path_to_windows(path_str)
        return path_str.replace('/', '\\')

    # On other platforms, just normalize slashes
    return path_str.replace('\\', '/')


def to_platform_path(path: Union[str, Path]) -> Path:
    """Convert path to native Path object for current platform.

    Args:
        path: Path string or Path object

    Returns:
        Native Path object
    """
    normalized = normalize_path(path)
    return Path(normalized)


def ensure_posix_path(path: Union[str, Path]) -> str:
    """Ensure path is in POSIX format with forward slashes.

    Useful for paths that need to be consistent across environments
    (e.g., for config files, memory stores).

    Args:
        path: Path string or Path object

    Returns:
        Path in POSIX format
    """
    path_str = str(path)

    # Convert Windows drive letter to /mnt/ format first if needed
    if re.match(r'^[a-zA-Z]:[/\\]', path_str):
        path_str = windows_path_to_wsl(path_str)

    return path_str.replace('\\', '/')


def get_relative_to_cwd(path: Union[str, Path]) -> Optional[Path]:
    """Get path relative to current working directory.

    Args:
        path: Path string or Path object

    Returns:
        Relative Path or None if not under CWD
    """
    try:
        return Path(path).relative_to(Path.cwd())
    except ValueError:
        return None


def expand_user_path(path: Union[str, Path]) -> Path:
    """Expand user path (~ and ~user).

    Args:
        path: Path string or Path object

    Returns:
        Expanded Path object
    """
    path_str = str(path)

    # Handle ~ at start
    if path_str.startswith('~'):
        # Expand ~
        expanded = os.path.expanduser(path_str)
        return to_platform_path(expanded)

    return to_platform_path(path)


def safe_join(*parts: Union[str, Path]) -> Path:
    """Safely join path parts, normalizing for current platform.

    Args:
        *parts: Path parts to join

    Returns:
        Joined and normalized Path
    """
    joined = Path(*parts)
    return to_platform_path(joined)


def is_path_safe(path: Union[str, Path]) -> bool:
    """Check if a path is considered safe (not trying to escape).

    Args:
        path: Path to check

    Returns:
        True if path is safe
    """
    path_obj = Path(path)
    try:
        # Check that path doesn't escape current directory
        resolved = path_obj.resolve()
        cwd = Path.cwd()

        # Also consider safe if it's an absolute path that doesn't escape parent
        return not (path_obj.is_absolute() and str(resolved).startswith(str(cwd.parent))) or resolved.is_relative_to(cwd)
    except Exception:
        return False


def get_display_path(path: Union[str, Path]) -> str:
    """Get a human-friendly display path.

    Args:
        path: Path to display

    Returns:
        Display string with ~ for user's home
    """
    try:
        path_obj = Path(path)
        home = Path.home()

        if path_obj.is_relative_to(home):
            return f"~/{path_obj.relative_to(home)}"
        return str(path_obj)
    except Exception:
        return str(path)
