from __future__ import annotations

from typing import Any, Dict


EXECUTION_ROUTE_HINTS: tuple[dict[str, Any], ...] = (
    {
        "interface_id": "memory.compress",
        "method": "POST",
        "path": "/memory/compress",
        "implemented_by": "MemoryMaintenanceExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/memory/compress",
            "executor_path": "/executor/memory/compress",
        },
    },
    {
        "interface_id": "body.prepare",
        "method": "POST",
        "path": "/body/slots/{slot_id}/prepare",
        "implemented_by": "BodyLifecycleExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/slots/{slot_id}/prepare",
            "executor_path": "/executor/body/slots/{slot_id}/prepare",
        },
    },
    {
        "interface_id": "body.candidate",
        "method": "POST",
        "path": "/body/slots/{slot_id}/candidate",
        "implemented_by": "BodyLifecycleExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/slots/{slot_id}/candidate",
            "executor_path": "/executor/body/slots/{slot_id}/candidate",
        },
    },
    {
        "interface_id": "body.upgrade.execute",
        "method": "POST",
        "path": "/body/upgrade/execute",
        "implemented_by": "BodyUpgradeExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/upgrade/execute",
            "executor_path": "/executor/body/upgrade/execute",
        },
    },
    {
        "interface_id": "body.probe.report",
        "method": "POST",
        "path": "/body/probe/report",
        "implemented_by": "BodyLifecycleExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/probe/report",
            "executor_path": "/executor/body/probe/report",
        },
    },
    {
        "interface_id": "body.probe.run",
        "method": "POST",
        "path": "/body/probe/run",
        "implemented_by": "BodyLifecycleExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/probe/run",
            "executor_path": "/executor/body/probe/run",
        },
    },
    {
        "interface_id": "body.watch-window.status",
        "method": "GET",
        "path": "/body/watch-window/status",
        "implemented_by": "WatchWindowExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/watch-window/status",
            "executor_path": "/executor/body/watch-window/status",
        },
    },
    {
        "interface_id": "body.watch-window.evaluate",
        "method": "POST",
        "path": "/body/watch-window/evaluate",
        "implemented_by": "WatchWindowExecutionAdapter",
        "preferred_entrypoint": {
            "gateway_path": "/api/executor/body/watch-window/evaluate",
            "executor_path": "/executor/body/watch-window/evaluate",
        },
    },
)


def build_execution_route_hint(interface_id: str) -> Dict[str, Any]:
    entry = next(
        (item for item in EXECUTION_ROUTE_HINTS if item["interface_id"] == interface_id),
        None,
    )
    if entry is None:
        return {"interface_id": interface_id}
    return dict(entry)


def attach_execution_route_hint(payload: Dict[str, Any], interface_id: str) -> Dict[str, Any]:
    result = dict(payload)
    result["execution_route_hint"] = build_execution_route_hint(interface_id)
    return result
