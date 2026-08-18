"""Structured service control protocol for the VoidCube desktop shell."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Literal

from ..cli.execution_context import (
    collect_execution_context,
    load_execution_context,
)
from ...infrastructure.gateway.service_launcher import (
    _pid_alive,
    ensure_running,
    status_all,
    stop_all,
)

ControlAction = Literal["status", "start", "stop", "restart"]
SCHEMA_VERSION = 1


def _service_state(info: dict[str, Any]) -> str:
    if info.get("healthy"):
        return "healthy"
    if info.get("running"):
        return "unhealthy"
    return "stopped"


def snapshot(action: ControlAction) -> dict[str, Any]:
    """Return the stable desktop-facing view of all managed services."""
    services = [
        {
            "name": name,
            "port": int(info["port"]),
            "pid": info.get("pid"),
            "state": _service_state(info),
        }
        for name, info in status_all().items()
    ]
    expected_state = "stopped" if action == "stop" else "healthy"
    execution_context = load_execution_context(pid_alive=_pid_alive)
    if execution_context is None:
        execution_context = collect_execution_context()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "action": action,
        "ok": all(service["state"] == expected_state for service in services),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "services": services,
        "executionContext": execution_context,
    }


def execute(action: ControlAction) -> dict[str, Any]:
    """Execute a lifecycle action and return its resulting service snapshot."""
    if action == "status":
        return snapshot(action)

    # Keep stdout reserved for the JSON protocol. Existing service lifecycle
    # messages remain available to Electron as diagnostic stderr.
    with contextlib.redirect_stdout(sys.stderr):
        if action == "stop":
            stop_all(force=True)
        elif action == "start":
            ensure_running(silent=True)
        elif action == "restart":
            stop_all(force=True)
            ensure_running(silent=True)
        else:
            raise ValueError(f"Unsupported desktop control action: {action}")
    return snapshot(action)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VoidCube desktop service control")
    parser.add_argument("action", choices=("status", "start", "stop", "restart"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    action: ControlAction = args.action
    try:
        payload = execute(action)
    except Exception as exc:
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "action": action,
            "ok": False,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "services": [],
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
