from __future__ import annotations

import sys

from VoidCube_cli import __version__
from VoidCube_cli.entrypoint_startup import PROJECT_ROOT


def cmd_doctor(args):
    """Run configuration and agent runtime diagnostics."""
    from VoidCube_cli.config_validator import print_diagnosis

    print_diagnosis()


def _print_ops_json(payload) -> None:
    import json as _json
    print(_json.dumps(payload, ensure_ascii=False, indent=2))


def _executor_ops_client(args):
    from VoidCube_cli.ops.executor import ExecutorOpsClient, default_gateway_url

    return ExecutorOpsClient(
        gateway_url=getattr(args, "gateway_url", None) or default_gateway_url(),
        timeout=float(getattr(args, "timeout", 30.0) or 30.0),
    )


def _exit_executor_ops_error(command_name: str, exc: Exception) -> None:
    import requests

    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    detail = str(exc).strip()

    if isinstance(exc, requests.HTTPError):
        if status_code is not None:
            print(f"{command_name} failed: executor returned HTTP {status_code}.", file=sys.stderr)
        else:
            print(f"{command_name} failed: executor request failed.", file=sys.stderr)
    else:
        print(f"{command_name} failed: executor route unavailable.", file=sys.stderr)

    if detail:
        print(detail, file=sys.stderr)

    print("Check the gateway / executor chain and try again.", file=sys.stderr)
    raise SystemExit(1)


def cmd_body(args):
    """Body lifecycle operations routed through gateway executor."""
    import requests

    client = _executor_ops_client(args)
    action = getattr(args, "body_action", None)

    try:
        if action == "status":
            result = {
                "registry": client.get_body_registry(),
                "active_target": client.get_active_body_target(),
            }
            _print_ops_json(result)
            return

        if action == "upgrade":
            payload = {
                "body_version": getattr(args, "body_version", None),
                "watch_window_seconds": getattr(args, "watch_window_seconds", None),
            }
            payload = {key: value for key, value in payload.items() if value is not None}
            result = client.execute_body_upgrade(payload)
            _print_ops_json(result)
            return

        if action == "consent":
            payload = {
                "slot_id": getattr(args, "slot_id", None),
                "approved": True,
                "watch_window_seconds": getattr(args, "watch_window_seconds", None),
            }
            payload = {key: value for key, value in payload.items() if value is not None}
            result = client.confirm_body_switch(payload)
            _print_ops_json(result)
            return
    except requests.RequestException as exc:
        _exit_executor_ops_error("Body command", exc)

    print("Unknown body command. Use: VoidCube body --help")


def cmd_serve(args):
    """Integrated system launcher — start/stop/status of gateway + supervisor."""
    from VoidCube_cli.ops.serve import start_all, stop_all, print_status

    action = getattr(args, "serve_action", None) or "status"
    if action == "start":
        foreground = getattr(args, "foreground", False)
        start_all(foreground=foreground)
    elif action == "stop":
        stop_all()
    else:
        print_status()


def cmd_debug(args):
    """Debug tools (share report, etc.)."""
    from VoidCube_cli.debug import run_debug
    run_debug(args)


def cmd_config(args):
    """Configuration management."""
    from VoidCube_cli.config_commands import config_command
    config_command(args)






def cmd_version(args):
    """Show version."""
    print(f"Voidcube Agent v{__version__}")
    print(f"Project: {PROJECT_ROOT}")
    
    # Show Python version
    print(f"Python: {sys.version.split()[0]}")
    
    # Check for key dependencies
    try:
        import openai
        print(f"OpenAI SDK: {openai.__version__}")
    except ImportError:
        print("OpenAI SDK: Not installed")

def _coalesce_session_name_args(argv: list) -> list:
    """Join unquoted multi-word session names after -c/--continue and -r/--resume.

    When a user types ``VoidCube -c Pokemon Agent Dev`` without quoting the
    session name, argparse sees three separate tokens.  This function merges
    them into a single argument so argparse receives
    ``['-c', 'Pokemon Agent Dev']`` instead.

    Tokens are collected after the flag until we hit another flag (``-*``)
    or a known top-level subcommand.
    """
    _SUBCOMMANDS = {
        "chat", "model", "gateway", "login", "logout",
        "body", "agent", "serve",
        "status", "doctor", "config", "tools",
        "mcp", "sessions", "insights", "version",
        "api", "acp", "logs", "memory", "profile", "update", "uninstall",
    }
    _SESSION_FLAGS = {"-c", "--continue", "-r", "--resume"}

    result = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _SESSION_FLAGS:
            result.append(token)
            i += 1
            # Collect subsequent non-flag, non-subcommand tokens as one name
            parts: list = []
            while i < len(argv) and not argv[i].startswith("-") and argv[i] not in _SUBCOMMANDS:
                parts.append(argv[i])
                i += 1
            if parts:
                result.append(" ".join(parts))
        else:
            result.append(token)
            i += 1
    return result




def cmd_logs(args):
    """View and filter Voidcube log files."""
    from VoidCube_cli.logs import tail_log, list_logs

    log_name = getattr(args, "log_name", "agent") or "agent"

    if log_name == "list":
        list_logs()
        return

    tail_log(
        log_name,
        num_lines=getattr(args, "lines", 50),
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        session=getattr(args, "session", None),
        since=getattr(args, "since", None),
        component=getattr(args, "component", None),
    )
