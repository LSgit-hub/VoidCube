from __future__ import annotations

import argparse
import sys

from .operations import (
    _coalesce_session_name_args,
    cmd_version,
)
from .session import (
    _exec_in_container,
    _resolve_session_by_name_or_id,
    cmd_chat,
)


def dispatch_cli(parser: argparse.ArgumentParser, argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``VoidCube -c Pokemon Agent Dev`` → ``VoidCube -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
    from ....infrastructure.config.configuration import get_container_exec_info
    container_info = get_container_exec_info()
    if container_info:
        try:
            _exec_in_container(container_info, raw_argv)
        except OSError as exc:
            print(f"Error: cannot exec into container: {exc}", file=sys.stderr)
            sys.exit(1)
        # Unreachable: os.execvp replaces the process on success.
        sys.exit(1)

    _processed_argv = _coalesce_session_name_args(raw_argv)
    args = parser.parse_args(_processed_argv)

    # Fix -c / --continue greedy consumption of subcommand names.
    # When ``VoidCube -c chat`` is typed, nargs="?" on -c consumes "chat"
    # as the session name rather than recognising it as a subcommand.
    # Detect this and swap: treat the value as the subcommand instead.
    # Only swap when NO session with that name exists — a real session
    # name that happens to match a subcommand takes priority.
    _KNOWN_COMMANDS = {
        "chat", "model", "gateway", "login", "logout",
        "body", "agent", "serve", "status", "autonomous", "doctor", "config", "tools",
        "mcp", "sessions", "insights", "version", "api", "acp", "logs",
        "memory", "profile", "update", "uninstall",
    }
    continue_val = getattr(args, "continue_last", None)
    if (
        continue_val is not None
        and continue_val is not True  # ``-c`` with no argument → const=True
        and isinstance(continue_val, str)
        and continue_val.lower() in _KNOWN_COMMANDS
        and args.command is None
    ):
        # Only swap if NO session with that name actually exists
        resolved = _resolve_session_by_name_or_id(continue_val)
        if resolved is None:
            args.command = continue_val.lower()
            args.continue_last = None
        else:
            # Session exists — keep as session name, route to chat
            args.command = "chat"
            args.continue_last = continue_val

    # Handle --version flag
    if args.version:
        cmd_version(args)
        return

    # Handle top-level --resume / --continue as shortcut to chat
    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"
        args.query = None
        args.model = None
        args.provider = None
        args.toolsets = None
        args.verbose = False
        if not hasattr(args, "worktree"):
            args.worktree = False
        cmd_chat(args)
        return

    # Default to chat if no command specified
    if args.command is None:
        args.query = None
        args.model = None
        args.provider = None
        args.toolsets = None
        args.verbose = False
        args.resume = None
        args.continue_last = None
        if not hasattr(args, "worktree"):
            args.worktree = False
        cmd_chat(args)
        return

    # Execute the command
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
