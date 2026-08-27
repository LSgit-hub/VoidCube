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
    _wait_for_gateway_service_type,
    _wait_for_health,
    ensure_running,
    start_service,
    status_all,
    stop_service,
    stop_all,
)

ControlAction = Literal["status", "start", "stop", "restart"]
PluginAction = Literal["start", "stop", "restart"]
SCHEMA_VERSION = 1


def _service_state(info: dict[str, Any]) -> str:
    if info.get("healthy"):
        return "healthy"
    if info.get("running"):
        return "unhealthy"
    return "stopped"


def _plugin_snapshot(service_status: dict[str, Any]) -> list[dict[str, Any]]:
    """Project manifest metadata and live service state for the desktop shell."""
    from ...extensions.plugins.registry import (
        discover_plugin_manifests,
        find_plugin_web_uis,
        is_plugin_enabled,
    )

    web_paths = {
        item["name"]: f"{item['mount_path'].rstrip('/')}/"
        for item in find_plugin_web_uis()
    }
    records: list[dict[str, Any]] = []
    for descriptor in discover_plugin_manifests():
        service = (
            service_status.get(descriptor.name)
            if "service" in descriptor.capabilities
            else None
        )
        service_view = None
        if service is not None:
            service_view = {
                "port": int(service["port"]),
                "pid": service.get("pid"),
                "state": _service_state(service),
            }
        elif "service" in descriptor.capabilities:
            declared_service = descriptor.manifest.get("service")
            if isinstance(declared_service, dict):
                try:
                    port = int(declared_service.get("port") or 0)
                except (TypeError, ValueError):
                    port = 0
                service_view = {
                    "port": port,
                    "pid": None,
                    "state": "stopped",
                }

        records.append(
            {
                "name": descriptor.name,
                "displayName": (
                    str(descriptor.manifest.get("display_name") or "").strip()
                    or descriptor.name
                ),
                "version": str(descriptor.manifest.get("version") or ""),
                "description": str(descriptor.manifest.get("description") or ""),
                "enabled": is_plugin_enabled(descriptor),
                "capabilities": list(descriptor.capabilities),
                "uiPath": web_paths.get(descriptor.name),
                "service": service_view,
            }
        )
    return records


def snapshot(action: ControlAction) -> dict[str, Any]:
    """Return the stable desktop-facing view of all managed services."""
    service_status = status_all()
    services = [
        {
            "name": name,
            "port": int(info["port"]),
            "pid": info.get("pid"),
            "state": _service_state(info),
        }
        for name, info in service_status.items()
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
        "plugins": _plugin_snapshot(service_status),
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


def execute_plugin(name: str, action: PluginAction) -> dict[str, Any]:
    """Run one plugin service lifecycle action and return the full snapshot."""
    from ...extensions.plugins.registry import discover_plugin_manifests, is_plugin_enabled

    descriptor = next(
        (item for item in discover_plugin_manifests() if item.name == name),
        None,
    )
    if descriptor is None:
        raise ValueError(f"Unknown plugin: {name}")
    if not is_plugin_enabled(descriptor):
        raise ValueError(f"Plugin is disabled: {name}")

    service_status = status_all()
    service = service_status.get(name)
    if service is None:
        raise ValueError(f"Plugin does not provide a managed service: {name}")

    if action == "stop":
        stop_service(name, silent=True)
    else:
        if action == "restart":
            stop_service(name, silent=True)
        start_service(name)
        _wait_for_health(name, int(service["port"]))
        from ...infrastructure.gateway.service_launcher import SERVICES

        gateway_type = SERVICES[name].gateway_service_type
        if gateway_type:
            _wait_for_gateway_service_type(gateway_type)
    return snapshot(action)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VoidCube desktop service control")
    parser.add_argument("action", choices=("status", "start", "stop", "restart", "plugin"))
    parser.add_argument("plugin_name", nargs="?")
    parser.add_argument("plugin_action", choices=("start", "stop", "restart"), nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "plugin":
        if not args.plugin_name or not args.plugin_action:
            raise SystemExit("plugin requires <name> <start|stop|restart>")
        action = args.plugin_action
    else:
        action = args.action
    try:
        if args.action == "plugin":
            with contextlib.redirect_stdout(sys.stderr):
                payload = execute_plugin(args.plugin_name, action)
        else:
            payload = execute(action)
    except Exception as exc:
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "action": action,
            "ok": False,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "services": [],
            "plugins": [],
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
