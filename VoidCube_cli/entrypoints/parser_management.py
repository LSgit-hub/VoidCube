from __future__ import annotations

import argparse

from VoidCube_cli.entrypoints.management import (
    cmd_acp, cmd_insights, cmd_mcp, cmd_sessions,
)
from VoidCube_cli.entrypoints.operations import cmd_logs, cmd_version


def register_management_commands(subparsers: argparse._SubParsersAction) -> None:
    # =========================================================================
        # mcp command — manage MCP server connections
        # =========================================================================
        mcp_parser = subparsers.add_parser(
            "mcp",
            help="Manage MCP servers and run Voidcube as an MCP server",
            description=(
                "Manage MCP server connections and run Voidcube as an MCP server.\n\n"
                "MCP servers provide additional tools via the Model Context Protocol.\n"
                "Use 'VoidCube mcp add' to connect to a new server, or\n"
                "'VoidCube mcp serve' to expose Voidcube conversations over MCP."
            ),
        )
        mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

        mcp_serve_p = mcp_sub.add_parser(
            "serve",
            help="Run Voidcube as an MCP server (expose conversations to other agents)",
        )
        mcp_serve_p.add_argument(
            "-v", "--verbose", action="store_true",
            help="Enable verbose logging on stderr",
        )

        mcp_add_p = mcp_sub.add_parser("add", help="Add an MCP server (discovery-first install)")
        mcp_add_p.add_argument("name", help="Server name (used as config key)")
        mcp_add_p.add_argument("--url", help="HTTP/SSE endpoint URL")
        mcp_add_p.add_argument("--command", help="Stdio command (e.g. npx)")
        mcp_add_p.add_argument("--args", nargs="*", default=[], help="Arguments for stdio command")
        mcp_add_p.add_argument("--auth", choices=["oauth", "header"], help="Auth method")
        mcp_add_p.add_argument("--preset", help="Known MCP preset name")
        mcp_add_p.add_argument("--env", nargs="*", default=[], help="Environment variables for stdio servers (KEY=VALUE)")

        mcp_rm_p = mcp_sub.add_parser("remove", help="Remove an MCP server")
        mcp_rm_p.add_argument("name", help="Server name to remove")

        mcp_sub.add_parser("list", help="List configured MCP servers")

        mcp_test_p = mcp_sub.add_parser("test", help="Test MCP server connection")
        mcp_test_p.add_argument("name", help="Server name to test")

        mcp_cfg_p = mcp_sub.add_parser("configure", help="Toggle tool selection")
        mcp_cfg_p.add_argument("name", help="Server name to configure")


        mcp_parser.set_defaults(func=cmd_mcp)

        # =========================================================================
        # sessions command
        # =========================================================================
        sessions_parser = subparsers.add_parser(
            "sessions",
            help="Manage session history (list, rename, export, prune, delete)",
            description="View and manage the SQLite session store"
        )
        sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

        sessions_list = sessions_subparsers.add_parser("list", help="List recent sessions")
        sessions_list.add_argument("--source", help="Filter by source (cli, etc.)")
        sessions_list.add_argument("--limit", type=int, default=20, help="Max sessions to show")

        sessions_export = sessions_subparsers.add_parser("export", help="Export sessions to a JSONL file")
        sessions_export.add_argument("output", help="Output JSONL file path (use - for stdout)")
        sessions_export.add_argument("--source", help="Filter by source")
        sessions_export.add_argument("--session-id", help="Export a specific session")

        sessions_delete = sessions_subparsers.add_parser("delete", help="Delete a specific session")
        sessions_delete.add_argument("session_id", help="Session ID to delete")
        sessions_delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

        sessions_prune = sessions_subparsers.add_parser("prune", help="Delete old sessions")
        sessions_prune.add_argument("--older-than", type=int, default=90, help="Delete sessions older than N days (default: 90)")
        sessions_prune.add_argument("--source", help="Only prune sessions from this source")
        sessions_prune.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

        sessions_subparsers.add_parser("stats", help="Show session store statistics")

        sessions_rename = sessions_subparsers.add_parser("rename", help="Set or change a session's title")
        sessions_rename.add_argument("session_id", help="Session ID to rename")
        sessions_rename.add_argument("title", nargs="+", help="New title for the session")

        sessions_browse = sessions_subparsers.add_parser(
            "browse",
            help="Interactive session picker — browse, search, and resume sessions",
        )
        sessions_browse.add_argument("--source", help="Filter by source (cli, etc.)")
        sessions_browse.add_argument("--limit", type=int, default=50, help="Max sessions to load (default: 50)")



        sessions_parser.set_defaults(
            func=cmd_sessions,
            command_help=sessions_parser.print_help,
        )

        # =========================================================================
        # insights command
        # =========================================================================
        insights_parser = subparsers.add_parser(
            "insights",
            help="Show usage insights and analytics",
            description="Analyze session history to show token usage, costs, tool patterns, and activity trends"
        )
        insights_parser.add_argument("--days", type=int, default=30, help="Number of days to analyze (default: 30)")
        insights_parser.add_argument("--source", help="Filter by platform (cli, telegram, discord, etc.)")


        insights_parser.set_defaults(func=cmd_insights)

        # =========================================================================
        # version command
        # =========================================================================
        version_parser = subparsers.add_parser(
            "version",
            help="Show version information"
        )
        version_parser.set_defaults(func=cmd_version)

        # =========================================================================
        # acp command
        # =========================================================================
        acp_parser = subparsers.add_parser(
            "acp",
            help="Run Voidcube Agent as an ACP (Agent Client Protocol) server",
            description="Start Voidcube Agent in ACP mode for editor integration (VS Code, Zed, JetBrains)",
        )


        acp_parser.set_defaults(func=cmd_acp)

        # =========================================================================
        # logs command
        # =========================================================================
        logs_parser = subparsers.add_parser(
            "logs",
            help="View and filter Voidcube log files",
            description="View, tail, and filter agent.log / errors.log / gateway.log",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""\
    Examples:
        VoidCube logs                    Show last 50 lines of agent.log
        VoidCube logs -f                 Follow agent.log in real time
        VoidCube logs errors             Show last 50 lines of errors.log
        VoidCube logs gateway -n 100     Show last 100 lines of gateway.log
        VoidCube logs --level WARNING    Only show WARNING and above
        VoidCube logs --session abc123   Filter by session ID
        VoidCube logs --component tools  Only show tool-related lines
        VoidCube logs --since 1h         Lines from the last hour
        VoidCube logs --since 30m -f     Follow, starting from 30 min ago
        VoidCube logs list               List available log files with sizes
    """,
        )
        logs_parser.add_argument(
            "log_name", nargs="?", default="agent",
            help="Log to view: agent (default), errors, gateway, or 'list' to show available files",
        )
        logs_parser.add_argument(
            "-n", "--lines", type=int, default=50,
            help="Number of lines to show (default: 50)",
        )
        logs_parser.add_argument(
            "-f", "--follow", action="store_true",
            help="Follow the log in real time (like tail -f)",
        )
        logs_parser.add_argument(
            "--level", metavar="LEVEL",
            help="Minimum log level to show (DEBUG, INFO, WARNING, ERROR)",
        )
        logs_parser.add_argument(
            "--session", metavar="ID",
            help="Filter lines containing this session ID substring",
        )
        logs_parser.add_argument(
            "--since", metavar="TIME",
            help="Show lines since TIME ago (e.g. 1h, 30m, 2d)",
        )
        logs_parser.add_argument(
            "--component", metavar="NAME",
            help="Filter by component: gateway, agent, tools, cli",
        )
        logs_parser.set_defaults(func=cmd_logs)
