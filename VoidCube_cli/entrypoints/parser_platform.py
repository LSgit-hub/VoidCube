from __future__ import annotations

import argparse

from VoidCube_cli.entrypoints.management import (
    cmd_api, cmd_gateway, cmd_profile, cmd_uninstall, cmd_update,
)


def register_platform_commands(subparsers: argparse._SubParsersAction) -> None:
    # =========================================================================
        # api command
        # =========================================================================
        api_parser = subparsers.add_parser(
            "api",
            help="Configure API settings for inference providers",
            description="Interactive wizard for adding and configuring inference providers",
        )


        api_parser.set_defaults(func=cmd_api)

        # =========================================================================
        # gateway command
        # =========================================================================
        gateway_parser = subparsers.add_parser(
            "gateway",
            help="Manage the VoidCube gateway service",
            description="Start, stop, or check the internal gateway and supervisor services",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""\
    Examples:
        VoidCube gateway              Show gateway + supervisor status
        VoidCube gateway start        Start background services
        VoidCube gateway stop         Stop all running services
    """,
        )
        gateway_sub = gateway_parser.add_subparsers(dest="gateway_action")
        gateway_sub.add_parser("start", help="Start gateway and supervisor in background")
        gateway_sub.add_parser("stop", help="Stop all running services")
        gateway_sub.add_parser("status", help="Show service status")


        gateway_parser.set_defaults(func=cmd_gateway)

        # =========================================================================
        # profile command
        # =========================================================================
        profile_parser = subparsers.add_parser(
            "profile",
            help="Manage VoidCube configuration profiles",
            description="Create, list, switch, and delete isolated VoidCube profiles",
        )
        profile_sub = profile_parser.add_subparsers(dest="profile_action")
        profile_sub.add_parser("list", help="List all profiles")
        profile_create = profile_sub.add_parser("create", help="Create a new profile")
        profile_create.add_argument("name", help="Profile name")
        profile_use = profile_sub.add_parser("use", help="Switch to a profile")
        profile_use.add_argument("name", help="Profile name")
        profile_delete = profile_sub.add_parser("delete", help="Delete a profile")
        profile_delete.add_argument("name", help="Profile name")
        profile_delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")


        profile_parser.set_defaults(
            func=cmd_profile,
            command_help=profile_parser.print_help,
        )

        # =========================================================================
        # update command
        # =========================================================================
        update_parser = subparsers.add_parser(
            "update",
            help="Update VoidCube to the latest version",
            description="Upgrade VoidCube Agent via pip",
        )


        update_parser.set_defaults(func=cmd_update)

        # =========================================================================
        # uninstall command
        # =========================================================================
        uninstall_parser = subparsers.add_parser(
            "uninstall",
            help="Uninstall VoidCube Agent",
            description="Remove VoidCube Agent from your system",
        )


        uninstall_parser.set_defaults(func=cmd_uninstall)

        # =========================================================================
        # =========================================================================
