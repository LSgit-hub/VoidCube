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
    _process_belongs_to_runtime,
    _read_pid,
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
            try:
                port = int(service["port"])
            except (KeyError, TypeError, ValueError):
                port = 0
            if 1 <= port <= 65535:
                service_view = {
                    "port": port,
                    "pid": service.get("pid"),
                    "state": _service_state(service),
                }
                if "registered" in service:
                    service_view["registered"] = bool(service["registered"])
                if "control_plane_healthy" in service:
                    service_view["controlPlaneHealthy"] = bool(
                        service["control_plane_healthy"]
                    )
                if service.get("restart_blocked"):
                    service_view["restartBlocked"] = str(service["restart_blocked"])
        elif "service" in descriptor.capabilities:
            declared_service = descriptor.manifest.get("service")
            if isinstance(declared_service, dict):
                try:
                    port = int(declared_service.get("port") or 0)
                except (TypeError, ValueError):
                    port = 0
                if 1 <= port <= 65535:
                    service_view = {
                        "port": port,
                        "pid": None,
                        "state": "stopped",
                        "registered": False,
                        "controlPlaneHealthy": False,
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
    services = []
    invalid_services: list[str] = []
    for name, info in service_status.items():
        try:
            port = int(info["port"])
        except (KeyError, TypeError, ValueError):
            invalid_services.append(str(name))
            continue
        if not 1 <= port <= 65535:
            invalid_services.append(str(name))
            continue
        service_view = {
            "name": str(name),
            "port": port,
            "pid": info.get("pid"),
            "state": _service_state(info),
        }
        if "registered" in info:
            service_view["registered"] = bool(info["registered"])
        if "control_plane_healthy" in info:
            service_view["controlPlaneHealthy"] = bool(info["control_plane_healthy"])
        if info.get("restart_blocked"):
            service_view["restartBlocked"] = str(info["restart_blocked"])
        services.append(service_view)
    expected_state = "stopped" if action == "stop" else "healthy"
    error = None
    if invalid_services:
        error = "Invalid managed service configuration: " + ", ".join(invalid_services)
    elif not services:
        error = "No managed services are configured"
    execution_context = load_execution_context(pid_alive=_pid_alive)
    if execution_context is None:
        execution_context = collect_execution_context()
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "action": action,
        "ok": bool(services)
        and not invalid_services
        and all(
            service["state"] == expected_state
            and (
                expected_state != "healthy"
                or (
                    service.get("registered") is not False
                    and service.get("controlPlaneHealthy") is not False
                )
            )
            for service in services
        ),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "services": services,
        "plugins": _plugin_snapshot(service_status),
        "executionContext": execution_context,
    }
    if error:
        payload["error"] = error
    return payload


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
            result = ensure_running(silent=True)
            if any(info.get("restart_blocked") for info in result.values()):
                raise RuntimeError("service restart blocked: port release timeout")
        elif action == "restart":
            stop_all(force=True)
            result = ensure_running(silent=True)
            if any(info.get("restart_blocked") for info in result.values()):
                raise RuntimeError("service restart blocked: port release timeout")
        else:
            raise ValueError(f"Unsupported desktop control action: {action}")
    return snapshot(action)


def execute_plugin(name: str, action: PluginAction) -> dict[str, Any]:
    """Run one plugin service lifecycle action and return the full snapshot."""
    from ...extensions.plugins.registry import discover_plugin_manifests, is_plugin_enabled
    from ...infrastructure.gateway.service_launcher import SERVICES

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
        if not stop_service(name, silent=True):
            raise RuntimeError(f"plugin stop blocked: {name}")
    else:
        if action == "restart":
            if not stop_service(name, silent=True):
                raise RuntimeError(f"plugin restart blocked: {name}")
        started = start_service(name)
        if started is None:
            service_info = SERVICES[name]
            pid = _read_pid(service_info.pid_file)
            if not (
                pid is not None
                and _pid_alive(pid)
                and _process_belongs_to_runtime(pid)
            ):
                raise RuntimeError(f"plugin start failed: {name}")
        if not _wait_for_health(name, int(service["port"])):
            raise RuntimeError(f"plugin health check failed: {name}")
        gateway_type = SERVICES[name].gateway_service_type
        if gateway_type and not _wait_for_gateway_service_type(gateway_type):
            # A healthy plugin without its Gateway registration cannot serve
            # the desktop control-plane contract.
            stopped = stop_service(name, silent=True)
            if not stopped:
                raise RuntimeError(
                    f"plugin registration failed and stop was blocked: {name}"
                )
            raise RuntimeError(f"plugin registration failed: {name}")
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
        # Keep the protocol shape stable and include the latest observable
        # state when a plugin operation fails after changing process state.
        try:
            current = snapshot(action)
            current["ok"] = False
            current["error"] = str(exc)
            print(json.dumps(current, ensure_ascii=False))
            return 1
        except Exception:
            pass
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
