from __future__ import annotations

import argparse

from .operations import cmd_body, cmd_doctor
from .provider import (
    cmd_login, cmd_logout, cmd_status,
)


def register_core_commands(subparsers: argparse._SubParsersAction) -> None:

    # =========================================================================
    # login command
    # =========================================================================
    login_parser = subparsers.add_parser(
        "login",
        help="Authenticate with an inference provider",
        description="Run OAuth device authorization flow for Voidcube CLI"
    )
    login_parser.add_argument(
        "--provider",
        choices=["nous"],
        default=None,
        help="Provider to authenticate with (default: nous)"
    )
    login_parser.add_argument(
        "--portal-url",
        help="Portal base URL (default: production portal)"
    )
    login_parser.add_argument(
        "--inference-url",
        help="Inference API base URL (default: production inference API)"
    )
    login_parser.add_argument(
        "--client-id",
        default=None,
        help="OAuth client id to use (default: VoidCube-cli)"
    )
    login_parser.add_argument(
        "--scope",
        default=None,
        help="OAuth scope to request"
    )
    login_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically"
    )
    login_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds (default: 15)"
    )
    login_parser.add_argument(
        "--ca-bundle",
        help="Path to CA bundle PEM file for TLS verification"
    )
    login_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (testing only)"
    )
    login_parser.set_defaults(func=cmd_login)

    # =========================================================================
    # logout command
    # =========================================================================
    logout_parser = subparsers.add_parser(
        "logout",
        help="Clear authentication for an inference provider",
        description="Remove stored credentials and reset provider config"
    )
    logout_parser.add_argument(
        "--provider",
        choices=["nous"],
        default=None,
        help="Provider to log out from (default: active provider)"
    )
    logout_parser.set_defaults(func=cmd_logout)

    # =========================================================================
    # status command
    # =========================================================================
    status_parser = subparsers.add_parser(
        "status",
        help="Show status of all components",
        description="Display status of Voidcube Agent components"
    )
    status_parser.add_argument(
        "--all",
        action="store_true",
        help="Show all details (redacted for sharing)"
    )
    status_parser.add_argument(
        "--deep",
        action="store_true",
        help="Run deep checks (may take longer)"
    )
    status_parser.set_defaults(func=cmd_status)

    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run configuration and agent runtime diagnostics",
        description="Check configuration, tool registration, backend health, and agent tool-call smoke paths",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    # =========================================================================
    # body command
    # =========================================================================
    body_parser = subparsers.add_parser(
        "body",
        help="Operate body lifecycle through gateway executor",
        description=(
            "Inspect or upgrade VoidCube body slots through gateway /api/executor."
        ),
    )
    body_parser.add_argument(
        "--gateway-url",
        default=None,
        help="Gateway base URL (default: configured Gateway address)",
    )
    body_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30)",
    )
    body_subparsers = body_parser.add_subparsers(dest="body_action")

    body_subparsers.add_parser(
        "status",
        help="Show body registry and active launch target",
    )
    body_upgrade = body_subparsers.add_parser(
        "upgrade",
        help="Run the executor body upgrade pipeline via the preferred gateway path",
    )
    body_upgrade.add_argument(
        "--body-version",
        default=None,
        help="Candidate body version label",
    )
    body_upgrade.add_argument(
        "--watch-window-seconds",
        type=int,
        default=None,
        help="Watch-window duration after switch",
    )
    body_consent = body_subparsers.add_parser(
        "consent",
        help="Approve activating a probe-passed body slot waiting at the user-consent gate",
    )
    body_consent.add_argument(
        "--slot-id",
        default=None,
        help="Body slot to activate; optional when exactly one slot is awaiting consent",
    )
    body_consent.add_argument(
        "--watch-window-seconds",
        type=int,
        default=None,
        help="Watch-window duration after switch",
    )
    body_parser.set_defaults(func=cmd_body)
