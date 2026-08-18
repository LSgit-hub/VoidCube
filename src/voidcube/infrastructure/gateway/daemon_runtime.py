"""CLI-owned daemon lifecycle operations.

The interactive CLI delegates Gateway/Memory/Supervisor process ownership to
this module.  It deliberately contains no TUI or ``VoidcubeCLI`` state so the
daemon lifecycle can be tested and reused by command entrypoints.
"""

_daemons_auto_started = False


def mark_daemons_auto_started(value: bool = True) -> None:
    """Record whether this CLI process owns daemon cleanup."""
    global _daemons_auto_started
    _daemons_auto_started = bool(value)


def daemons_auto_started() -> bool:
    """Return whether daemon cleanup is owned by this CLI process."""
    return _daemons_auto_started


def handle_serve_command(action: str | None) -> None:
    """Handle the CLI ``serve`` lifecycle action."""
    valid = {"start", "stop", "status", "foreground"}
    action_lower = action.strip().lower() if action else "status"

    if action_lower not in valid:
        print(f"Invalid serve action: {action!r}")
        print("Usage: voidcube <stop|status|start|foreground>")
        return

    try:
        from .service_launcher import start_all, stop_all, print_status
    except ImportError as exc:
        print(f"Failed to import serve module: {exc}")
        print("Ensure VoidCube is installed correctly (pip install -e .)")
        return

    if action_lower == "start":
        start_all(foreground=False)
    elif action_lower == "foreground":
        start_all(foreground=True)
    elif action_lower == "stop":
        stop_all()
    else:
        print_status()


def auto_start_daemons() -> None:
    """Start Gateway, Memory, and Supervisor for interactive CLI sessions."""
    try:
        from .service_launcher import ensure_running, print_status
    except ImportError:
        return

    print("\n  Auto-starting VoidCube daemons (Gateway -> Memory -> Supervisor)...\n")
    result = ensure_running(silent=False)
    print()

    any_started = any(info.get("started") for info in result.values())
    if any_started:
        print_status()
        mark_daemons_auto_started()


def maybe_stop_daemons_on_exit(*, force: bool = False) -> None:
    """Stop daemons started by this CLI process and release ownership."""
    del force  # daemon shutdown is intentionally non-interactive at exit
    if not daemons_auto_started():
        return

    try:
        from .service_launcher import stop_all
    except ImportError:
        return

    stop_all(force=True)
    mark_daemons_auto_started(False)


def clear_daemons_auto_started() -> None:
    """Clear daemon ownership without invoking a shutdown operation."""
    mark_daemons_auto_started(False)


__all__ = [
    "auto_start_daemons",
    "clear_daemons_auto_started",
    "daemons_auto_started",
    "handle_serve_command",
    "mark_daemons_auto_started",
    "maybe_stop_daemons_on_exit",
]
