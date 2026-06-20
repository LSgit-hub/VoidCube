"""Shared path validation helpers for tool implementations.

Extracts the ``resolve() + relative_to()`` and ``..`` traversal check
patterns previously duplicated across skill_manager_tool, skills_tool,
skills_hub, cronjob_tools, and credential_files.

Also consolidates file operation security checks from file_tools.py and
file_operations.py to provide a unified security validation interface.
"""

import errno
import logging
import os
from pathlib import Path
from typing import Optional, FrozenSet
from VoidCube_core.constants import get_VoidCube_home

logger = logging.getLogger(__name__)


def validate_within_dir(path: Path, root: Path) -> Optional[str]:
    """Ensure *path* resolves to a location within *root*.

    Returns an error message string if validation fails, or ``None`` if the
    path is safe.  Uses ``Path.resolve()`` to follow symlinks and normalize
    ``..`` components.

    Usage::

        error = validate_within_dir(user_path, allowed_root)
        if error:
            return json.dumps({"error": error})
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (ValueError, OSError) as exc:
        return f"Path escapes allowed directory: {exc}"
    return None


def has_traversal_component(path_str: str) -> bool:
    """Return True if *path_str* contains ``..`` traversal components.

    Quick check for obvious traversal attempts before doing full resolution.
    """
    parts = Path(path_str).parts
    return ".." in parts


# =============================================================================
# File Operation Security Checks (consolidated from file_tools.py and file_operations.py)
# =============================================================================

# Device path blocklist — reading these hangs the process (infinite output or blocking on input)
BLOCKED_DEVICE_PATHS: FrozenSet[str] = frozenset({
    # Infinite output — never reach EOF
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    # Blocks waiting for input
    "/dev/stdin", "/dev/tty", "/dev/console",
    # Nonsensical to read
    "/dev/stdout", "/dev/stderr",
    # fd aliases
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})

# System-sensitive path prefixes (from file_tools.py)
SENSITIVE_SYSTEM_PREFIXES = ("/etc/", "/boot/", "/usr/lib/systemd/")
SENSITIVE_SYSTEM_EXACT_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}

# User credential and config file deny list (from file_operations.py)
_HOME = str(Path.home())
WRITE_DENIED_PATHS = {
    os.path.realpath(p) for p in [
        os.path.join(_HOME, ".ssh", "authorized_keys"),
        os.path.join(_HOME, ".ssh", "id_rsa"),
        os.path.join(_HOME, ".ssh", "id_ed25519"),
        os.path.join(_HOME, ".ssh", "config"),
        str(get_VoidCube_home() / ".env"),
        os.path.join(_HOME, ".bashrc"),
        os.path.join(_HOME, ".zshrc"),
        os.path.join(_HOME, ".profile"),
        os.path.join(_HOME, ".bash_profile"),
        os.path.join(_HOME, ".zprofile"),
        os.path.join(_HOME, ".netrc"),
        os.path.join(_HOME, ".pgpass"),
        os.path.join(_HOME, ".npmrc"),
        os.path.join(_HOME, ".pypirc"),
        "/etc/sudoers",
        "/etc/passwd",
        "/etc/shadow",
    ]
}

WRITE_DENIED_PREFIXES = [
    os.path.realpath(p) + os.sep for p in [
        os.path.join(_HOME, ".ssh"),
        os.path.join(_HOME, ".aws"),
        os.path.join(_HOME, ".gnupg"),
        os.path.join(_HOME, ".kube"),
        "/etc/sudoers.d",
        "/etc/systemd",
        os.path.join(_HOME, ".docker"),
        os.path.join(_HOME, ".azure"),
        os.path.join(_HOME, ".config", "gh"),
    ]
]


def is_blocked_device(filepath: str) -> bool:
    """Return True if the path would hang the process (infinite output or blocking input).

    Uses the *literal* path — no symlink resolution — because the model
    specifies paths directly and realpath follows symlinks all the way
    through (e.g. /dev/stdin → /proc/self/fd/0 → /dev/pts/0), defeating
    the check.
    """
    normalized = os.path.expanduser(filepath)
    if normalized in BLOCKED_DEVICE_PATHS:
        return True
    # /proc/self/fd/0-2 and /proc/<pid>/fd/0-2 are Linux aliases for stdio
    if normalized.startswith("/proc/") and normalized.endswith(
        ("/fd/0", "/fd/1", "/fd/2")
    ):
        return True
    return False


def check_sensitive_system_path(filepath: str) -> Optional[str]:
    """Return an error message if the path targets a sensitive system location.
    
    This checks for system-critical paths like /etc/, /boot/, etc.
    """
    try:
        resolved = os.path.realpath(os.path.expanduser(filepath))
    except (OSError, ValueError):
        resolved = filepath
    for prefix in SENSITIVE_SYSTEM_PREFIXES:
        if resolved.startswith(prefix):
            return (
                f"Refusing to write to sensitive system path: {filepath}\n"
                "Use the terminal tool with sudo if you need to modify system files."
            )
    if resolved in SENSITIVE_SYSTEM_EXACT_PATHS:
        return (
            f"Refusing to write to sensitive system path: {filepath}\n"
            "Use the terminal tool with sudo if you need to modify system files."
        )
    return None


def get_safe_write_root() -> Optional[str]:
    """Return the resolved VOIDCUBE_WRITE_SAFE_ROOT path, or None if unset.

    When set, all write_file/patch operations are constrained to this
    directory tree.  Writes outside it are denied even if the target is
    not on the static deny list.  Opt-in hardening for gateway/messaging
    deployments that should only touch a workspace checkout.
    """
    root = os.getenv("VOIDCUBE_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def is_write_denied(path: str) -> bool:
    """Return True if path is on the write deny list.
    
    This checks for user credential files and configuration files that
    should not be modified directly.
    """
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    # 1) Static deny list
    if resolved in WRITE_DENIED_PATHS:
        return True
    for prefix in WRITE_DENIED_PREFIXES:
        if resolved.startswith(prefix):
            return True

    # 2) Optional safe-root sandbox
    safe_root = get_safe_write_root()
    if safe_root:
        if not (resolved == safe_root or resolved.startswith(safe_root + os.sep)):
            return True

    return False


def validate_file_write_path(filepath: str) -> Optional[str]:
    """Comprehensive validation for file write operations.
    
    Combines all security checks:
    - System-sensitive path check
    - User credential/config file check
    - Safe root sandbox check
    
    Returns an error message if validation fails, or None if the path is safe.
    """
    # Check system-sensitive paths
    system_err = check_sensitive_system_path(filepath)
    if system_err:
        return system_err
    
    # Check user credential/config files
    if is_write_denied(filepath):
        return (
            f"Refusing to write to protected path: {filepath}\n"
            "This path contains sensitive credentials or configuration."
        )
    
    return None


def is_expected_write_exception(exc: Exception) -> bool:
    """Return True if the exception is an expected write permission error."""
    if isinstance(exc, OSError):
        return exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    return False
