from __future__ import annotations

import argparse

from .management import cmd_memory, cmd_tools
from .operations import cmd_config, cmd_debug, cmd_serve


def register_operations_commands(subparsers: argparse._SubParsersAction) -> None:
    # =========================================================================
        # serve command — integrated system launcher (Phase 1 multi-process)
        # =========================================================================
        serve_parser = subparsers.add_parser(
            "serve",
            help="Start or manage VoidCube background services (gateway + supervisor)",
            description="Launch the full VoidCube multi-process system: gateway, supervisor.",
        )
        serve_subparsers = serve_parser.add_subparsers(dest="serve_action")
        serve_start = serve_subparsers.add_parser("start", help="Start all services in background")
        serve_start.add_argument("--foreground", action="store_true", help="Run in foreground (single-process)")
        serve_stop = serve_subparsers.add_parser("stop", help="Stop all running services")
        serve_status = serve_subparsers.add_parser("status", help="Show service status")
        serve_parser.set_defaults(func=cmd_serve)

        # =========================================================================
        # debug command
        # =========================================================================
        debug_parser = subparsers.add_parser(
            "debug",
            help="Debug tools — upload logs and system info for support",
            description="Debug utilities for Voidcube Agent. Use 'VoidCube debug share' to "
                        "upload a debug report (system info + recent logs) to a paste "
                        "service and get a shareable URL.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""\
    Examples:
        VoidCube debug share              Upload debug report and print URL
        VoidCube debug share --lines 500  Include more log lines
        VoidCube debug share --expire 30  Keep paste for 30 days
        VoidCube debug share --local      Print report locally (no upload)
    """,
        )
        debug_sub = debug_parser.add_subparsers(dest="debug_command")
        share_parser = debug_sub.add_parser(
            "share",
            help="Upload debug report to a paste service and print a shareable URL",
        )
        share_parser.add_argument(
            "--lines", type=int, default=200,
            help="Number of log lines to include per log file (default: 200)",
        )
        share_parser.add_argument(
            "--expire", type=int, default=7,
            help="Paste expiry in days (default: 7)",
        )
        share_parser.add_argument(
            "--local", action="store_true",
            help="Print the report locally instead of uploading",
        )
        debug_parser.set_defaults(func=cmd_debug)

        # =========================================================================
        # config command
        # =========================================================================
        config_parser = subparsers.add_parser(
            "config",
            help="View and edit configuration",
            description="Manage Voidcube Agent configuration"
        )
        config_subparsers = config_parser.add_subparsers(dest="config_command")

        # config show (default)
        config_subparsers.add_parser("show", help="Show current configuration")

        # config edit
        config_subparsers.add_parser("edit", help="Open config file in editor")

        # config set
        config_set = config_subparsers.add_parser("set", help="Set a configuration value")
        config_set.add_argument("key", nargs="?", help="Configuration key (e.g., model, terminal.backend)")
        config_set.add_argument("value", nargs="?", help="Value to set")

        # config path
        config_subparsers.add_parser("path", help="Print config file path")

        # config env-path
        config_subparsers.add_parser("env-path", help="Print .env file path")

        # config check
        config_subparsers.add_parser("check", help="Check for missing/outdated config")

        # config migrate
        config_subparsers.add_parser("migrate", help="Update config with new options")

        config_parser.set_defaults(func=cmd_config)

        # =========================================================================
        # memory command
        # =========================================================================
        memory_parser = subparsers.add_parser(
            "memory",
            help="Show or initialize canonical Mem",
            description=(
                "VoidCube uses one shared Mem service for recall, durable memory, "
                "identity experiences, and self-narrative."
            ),
        )
        memory_sub = memory_parser.add_subparsers(dest="memory_command")
        memory_sub.add_parser("setup", help="Initialize and show canonical Mem")
        memory_sub.add_parser("status", help="Show canonical Mem status")
        redaction_parser = memory_sub.add_parser(
            "redaction",
            help="Enable or disable redaction for Memory persistence and recall",
        )
        redaction_parser.add_argument(
            "state",
            choices=("on", "off", "status"),
            nargs="?",
            default="status",
            help="on, off, or status (default: status)",
        )


        memory_parser.set_defaults(func=cmd_memory)

        # =========================================================================
        # tools command
        # =========================================================================
        tools_parser = subparsers.add_parser(
            "tools",
            help="Configure which tools are enabled per platform",
            description=(
                "Enable, disable, or list tools.\n\n"
                "Built-in toolsets use plain names (e.g. web, memory).\n"
                "MCP tools use server:tool notation (e.g. github:create_issue).\n\n"
                "Run 'VoidCube tools' with no subcommand for the interactive configuration UI."
            ),
        )
        tools_parser.add_argument(
            "--summary",
            action="store_true",
            help="Print a summary of enabled tools per platform and exit"
        )
        tools_sub = tools_parser.add_subparsers(dest="tools_action")

        # VoidCube tools list [--platform cli]
        tools_list_p = tools_sub.add_parser(
            "list",
            help="Show all tools and their enabled/disabled status",
        )
        tools_list_p.add_argument(
            "--platform", default="cli",
            help="Platform to show (default: cli)",
        )

        # VoidCube tools disable <name...> [--platform cli]
        tools_disable_p = tools_sub.add_parser(
            "disable",
            help="Disable toolsets or MCP tools",
        )
        tools_disable_p.add_argument(
            "names", nargs="+", metavar="NAME",
            help="Toolset name (e.g. web) or MCP tool in server:tool form",
        )
        tools_disable_p.add_argument(
            "--platform", default="cli",
            help="Platform to apply to (default: cli)",
        )

        # VoidCube tools enable <name...> [--platform cli]
        tools_enable_p = tools_sub.add_parser(
            "enable",
            help="Enable toolsets or MCP tools",
        )
        tools_enable_p.add_argument(
            "names", nargs="+", metavar="NAME",
            help="Toolset name or MCP tool in server:tool form",
        )
        tools_enable_p.add_argument(
            "--platform", default="cli",
            help="Platform to apply to (default: cli)",
        )


        tools_parser.set_defaults(func=cmd_tools)
