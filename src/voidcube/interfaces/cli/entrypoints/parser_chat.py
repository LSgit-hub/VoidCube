from __future__ import annotations

import argparse

from .session import cmd_chat


def register_chat_command(subparsers: argparse._SubParsersAction) -> None:
    # =========================================================================
    # chat command
    # =========================================================================
    chat_parser = subparsers.add_parser(
        "chat",
        help="Interactive chat with the agent",
        description="Start an interactive chat session with Voidcube Agent"
    )
    chat_parser.add_argument(
        "-q", "--query",
        help="Single query (non-interactive mode)"
    )
    chat_parser.add_argument(
        "--image",
        help="Optional local image path to attach to a single query"
    )
    chat_parser.add_argument(
        "-m", "--model",
        help="Exact model ID returned by the selected provider API"
    )
    chat_parser.add_argument(
        "-t", "--toolsets",
        help="Comma-separated toolsets to enable"
    )
    chat_parser.add_argument(
        "-s", "--skills",
        action="append",
        default=argparse.SUPPRESS,
        help="Preload one or more skills for the session (repeat flag or comma-separate)"
    )
    chat_parser.add_argument(
        "--provider",
        default=None,
        metavar="PROVIDER",
        help="Inference provider ID or configured custom provider (default: auto)"
    )
    chat_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    chat_parser.add_argument(
        "-Q", "--quiet",
        action="store_true",
        help="Quiet mode for programmatic use: suppress banner, spinner, and tool previews. Only output the final response and session info."
    )
    chat_parser.add_argument(
        "--resume", "-r",
        metavar="SESSION_ID",
        default=argparse.SUPPRESS,
        help="Resume a previous session by ID (shown on exit)"
    )
    chat_parser.add_argument(
        "--continue", "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=argparse.SUPPRESS,
        metavar="SESSION_NAME",
        help="Resume a session by name, or the most recent if no name given"
    )
    chat_parser.add_argument(
        "--worktree", "-w",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run in an isolated git worktree (for parallel agents on the same repo)"
    )
    chat_parser.add_argument(
        "--checkpoints",
        action="store_true",
        default=False,
        help="Enable filesystem checkpoints before destructive file operations (use /rollback to restore)"
    )
    chat_parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="Maximum tool-calling iterations per conversation turn (default: 90, or agent.max_turns in config)"
    )
    chat_parser.add_argument(
        "--yolo",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Bypass all dangerous command approval prompts (use at your own risk)"
    )
    chat_parser.add_argument(
        "--pass-session-id",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Include the session ID in the agent's system prompt"
    )
    chat_parser.add_argument(
        "--source",
        default=None,
        help="Session source tag for filtering (default: cli). Use 'tool' for third-party integrations that should not appear in user session lists."
    )
    chat_parser.set_defaults(func=cmd_chat)
