from __future__ import annotations

import argparse

from VoidCube_cli.entrypoints.parser_chat import register_chat_command
from VoidCube_cli.entrypoints.parser_core import register_core_commands
from VoidCube_cli.entrypoints.parser_management import register_management_commands
from VoidCube_cli.entrypoints.parser_operations import register_operations_commands
from VoidCube_cli.entrypoints.parser_platform import register_platform_commands


def build_parser() -> argparse.ArgumentParser:
    """Build the canonical CLI parser and command registry."""
    try:
        from VoidCube_cli.i18n import init_i18n

        init_i18n()
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="VoidCube",
        description="Voidcube Agent - AI assistant with tool-calling capabilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    VoidCube                        Start interactive chat
    VoidCube chat -q "Hello"        Single query mode
    VoidCube -c                     Resume the most recent session
    VoidCube -c "my project"        Resume a session by name (latest in lineage)
    VoidCube --resume <session_id>  Resume a specific session by ID
    VoidCube logout                 Clear stored authentication
    VoidCube model                  Select default model
    VoidCube config                 View configuration
    VoidCube config edit            Edit config in $EDITOR
    VoidCube config set model gpt-4 Set a config value
    VoidCube -s VoidCube-agent-dev,github-auth
    VoidCube -w                     Start in isolated git worktree
    VoidCube sessions list          List past sessions
    VoidCube sessions browse        Interactive session picker
    VoidCube sessions rename ID T   Rename/title a session
    VoidCube logs                   View agent.log (last 50 lines)
    VoidCube logs -f                Follow agent.log in real time
    VoidCube logs errors            View errors.log
    VoidCube logs --since 1h        Lines from the last hour
    VoidCube debug share            Upload debug report for support
    VoidCube update                 Update to latest version

For more help on a command:
    VoidCube <command> --help
""",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit",
    )
    parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION",
        default=None,
        help="Resume a previous session by ID or title",
    )
    parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=None,
        metavar="SESSION_NAME",
        help="Resume a session by name, or the most recent if no name given",
    )
    parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=False,
        help="Run in an isolated git worktree (for parallel agents)",
    )
    parser.add_argument(
        "--skills",
        "-s",
        action="append",
        default=None,
        help=(
            "Preload one or more skills for the session "
            "(repeat flag or comma-separate)"
        ),
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        default=False,
        help="Bypass all dangerous command approval prompts (use at your own risk)",
    )
    parser.add_argument(
        "--pass-session-id",
        action="store_true",
        default=False,
        help="Include the session ID in the agent's system prompt",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    register_chat_command(subparsers)
    register_core_commands(subparsers)
    register_operations_commands(subparsers)
    register_management_commands(subparsers)
    register_platform_commands(subparsers)
    return parser
