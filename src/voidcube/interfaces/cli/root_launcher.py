#!/usr/bin/env python3
"""
voidcube — Unified launcher for the VoidCube Agent system.

This is the SINGLE entry point.  It orchestrates:
  1. Auto-start daemon services (Gateway → Memory → Supervisor)
  2. Delegate to the canonical CLI command dispatcher

Usage:
    voidcube                     Interactive chat (auto-starts daemons)
    voidcube chat -q "question"  Single query (skips daemon startup)
    voidcube stop                Stop all daemon services
    voidcube status              Show daemon service status
    voidcube -v / --version      Show version and exit

All other subcommands (model, config, sessions, logs, debug, body,
agent, memory, tools, mcp, doctor, …) are delegated to the full CLI.
"""

from __future__ import annotations

import sys
import os


def _is_daemon_lifecycle_command(args: list[str]) -> bool:
    """Return True when the user asked for a daemon lifecycle shortcut."""
    if not args:
        return False
    cmd = args[0].lower()
    return cmd in {"stop", "status"}


def _is_fast_path(args: list[str]) -> bool:
    """Return True for fast paths that should NOT auto-start daemons."""
    if not args:
        return False
    fast_flags = {"-v", "--version", "-V", "-h", "--help"}
    if any(a in fast_flags for a in args):
        return True
    # Subcommands that never need daemons (they either manage daemons
    # themselves or are purely local operations).
    fast_cmds = {
        "stop", "status", "serve", "model", "config", "logout", "login",
        "sessions", "logs", "tools", "mcp", "version", "doctor",
        "debug", "update", "memory", "insights",
    }
    cmd = args[0].lower()
    if cmd in fast_cmds:
        return True
    # "chat -q" single-query mode also skips daemons
    if cmd == "chat" and ("-q" in args or "--query" in args):
        return True
    return False


def _handle_daemon_lifecycle(args: list[str]) -> bool:
    """Handle ``stop`` / ``status`` shortcuts.  Returns True if handled."""
    cmd = args[0].lower()

    if cmd == "stop":
        try:
            from ...infrastructure.gateway.service_launcher import stop_all
        except ImportError as exc:
            print(f"Failed to import serve module: {exc}")
            return True
        stop_all()
        return True

    if cmd == "status":
        watch = "--watch" in args or "-w" in args
        full = "--full" in args or "-f" in args or watch  # watch implies full

        if watch:
            try:
                from .ops.dashboard import watch_dashboard
            except ImportError as exc:
                print(f"Failed to import dashboard: {exc}")
                return True
            # Show daemon status first, then enter watch loop
            try:
                from ...infrastructure.gateway.service_launcher import print_status
                print_status()
            except ImportError:
                pass
            watch_dashboard()
            return True

        try:
            from ...infrastructure.gateway.service_launcher import print_status
        except ImportError as exc:
            print(f"Failed to import serve module: {exc}")
            return True
        print_status()
        if full:
            try:
                from .ops.dashboard import print_dashboard

                print_dashboard()
            except Exception as exc:
                print(f"  Dashboard unavailable: {exc}")
                print("    Is the supervisor running?  http://127.0.0.1:6002/ui")
        return True

    return False


def _auto_start_daemons() -> None:
    """Start Gateway → Memory → Supervisor if not already running.

    The gateway comes up first so Memory and Supervisor can register
    themselves during startup.
    Sets ``VOIDCUBE_DAEMONS_STARTED`` so that downstream CLI code
    (cli.py) does not attempt a second auto-start.
    """
    try:
        from ...infrastructure.gateway.service_launcher import ensure_running
    except ImportError:
        return

    print("\n  Auto-starting VoidCube daemons (Gateway -> Memory -> Supervisor)...\n")
    ensure_running(silent=False)
    print()
    os.environ["VOIDCUBE_DAEMONS_STARTED"] = "1"


def main(
    argv: list[str] | None = None,
    *,
    cli_main: object | None = None,
    auto_start_daemons: object | None = None,
) -> int:
    """Unified entry point.  Returns exit code."""
    args = argv if argv is not None else sys.argv[1:]

    # ── Daemon lifecycle shortcuts ─────────────────────────────────
    if _is_daemon_lifecycle_command(args):
        return 0 if _handle_daemon_lifecycle(args) else 1

    # ── Auto-start daemons (interactive / non-fast paths) ──────────
    desktop_manages_services = (
        os.environ.get("VOIDCUBE_DESKTOP_MANAGED_SERVICES") == "1"
    )
    startup = auto_start_daemons or _auto_start_daemons
    if not _is_fast_path(args) and not desktop_manages_services:
        try:
            startup()
        except Exception as exc:
            # Best-effort: don't block the CLI, but log the failure so
            # the user knows daemons may be unavailable.
            import logging
            logging.getLogger("voidcube").warning(
                "Daemon auto-start failed: %s. "
                "Supervisor UI and /auto will be unavailable. "
                "Run 'voidcube serve start' to retry.",
                exc,
            )

    # ── Delegate to the full CLI ───────────────────────────────────
    if cli_main is None:
        from .main import main as cli_main

    # The canonical CLI main parses sys.argv directly.
    # We need to restore the original argv so arg parsing works.
    # The first element should be the program name.
    original_argv = sys.argv[:]
    try:
        sys.argv = ["voidcube"] + args
        cli_main()
    finally:
        sys.argv = original_argv

    return 0


if __name__ == "__main__":
    sys.exit(main())
