from __future__ import annotations

import json
import pytest

from voidcube.interfaces.desktop import desktop_control


def _status(*, gateway: str, memory: str, supervisor: str):
    states = {
        "gateway": gateway,
        "memory": memory,
        "supervisor": supervisor,
    }
    return {
        name: {
            "name": name,
            "port": 6000 + index,
            "pid": 4100 + index if state != "stopped" else None,
            "running": state != "stopped",
            "healthy": state == "healthy",
        }
        for index, (name, state) in enumerate(states.items())
    }


def test_snapshot_exposes_only_stable_desktop_service_fields(monkeypatch):
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: _status(
            gateway="healthy",
            memory="unhealthy",
            supervisor="stopped",
        ),
    )

    result = desktop_control.snapshot("status")

    assert result["schemaVersion"] == 1
    assert result["ok"] is False
    assert result["services"] == [
        {"name": "gateway", "port": 6000, "pid": 4100, "state": "healthy"},
        {"name": "memory", "port": 6001, "pid": 4101, "state": "unhealthy"},
        {"name": "supervisor", "port": 6002, "pid": None, "state": "stopped"},
    ]
    goal = next(plugin for plugin in result["plugins"] if plugin["name"] == "goal_manager")
    assert goal["displayName"] == "目标管理器"
    assert goal["uiPath"] == "/ui/goal-manager/"
    assert goal["service"]["port"] == 6003


def test_snapshot_marks_empty_service_registry_as_unavailable(monkeypatch):
    monkeypatch.setattr(desktop_control, "status_all", lambda: {})

    result = desktop_control.snapshot("status")

    assert result["ok"] is False
    assert result["services"] == []
    assert result["error"] == "No managed services are configured"


def test_snapshot_marks_unregistered_healthy_service_unavailable(monkeypatch):
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: {
            "gateway": {
                "name": "gateway",
                "port": 6000,
                "pid": 4100,
                "running": True,
                "healthy": True,
            },
            "memory": {
                "name": "memory",
                "port": 6001,
                "pid": 4101,
                "running": True,
                "healthy": True,
                "registered": False,
                "control_plane_healthy": False,
            },
        },
    )

    result = desktop_control.snapshot("status")

    assert result["ok"] is False
    assert result["services"][1]["registered"] is False


def test_snapshot_marks_gateway_unhealthy_registration_unavailable(monkeypatch):
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: {
            "gateway": {
                "name": "gateway",
                "port": 6000,
                "pid": 4100,
                "running": True,
                "healthy": True,
            },
            "memory": {
                "name": "memory",
                "port": 6001,
                "pid": 4101,
                "running": True,
                "healthy": True,
                "registered": True,
                "control_plane_healthy": False,
            },
        },
    )

    result = desktop_control.snapshot("status")

    assert result["ok"] is False
    assert result["services"][1]["controlPlaneHealthy"] is False


def test_stop_snapshot_does_not_require_gateway_registration(monkeypatch):
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: {
            "gateway": {
                "name": "gateway",
                "port": 6000,
                "pid": None,
                "running": False,
                "healthy": False,
            },
            "memory": {
                "name": "memory",
                "port": 6001,
                "pid": None,
                "running": False,
                "healthy": False,
                "registered": False,
                "control_plane_healthy": False,
            },
        },
    )

    result = desktop_control.snapshot("stop")

    assert result["ok"] is True


def test_snapshot_rejects_invalid_managed_service_ports(monkeypatch):
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: {
            "gateway": {
                "name": "gateway",
                "port": 0,
                "pid": 4100,
                "running": True,
                "healthy": True,
            }
        },
    )

    result = desktop_control.snapshot("status")

    assert result["ok"] is False
    assert result["services"] == []
    assert result["error"] == "Invalid managed service configuration: gateway"


def test_snapshot_omits_invalid_plugin_service_port(monkeypatch):
    descriptor = type("Descriptor", (), {
        "name": "sample",
        "capabilities": ("service",),
        "manifest": {"display_name": "Sample", "service": {"port": 0}},
    })()
    monkeypatch.setattr(desktop_control, "status_all", lambda: {})
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.discover_plugin_manifests",
        lambda: [descriptor],
    )
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.find_plugin_web_uis",
        lambda: [],
    )
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.is_plugin_enabled",
        lambda _descriptor: True,
    )

    result = desktop_control.snapshot("status")

    assert result["plugins"][0]["service"] is None


def test_execute_lifecycle_actions_delegate_to_canonical_service_owner(monkeypatch):
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        desktop_control,
        "ensure_running",
        lambda silent: calls.append(("start", silent)) or {},
    )
    monkeypatch.setattr(
        desktop_control,
        "stop_all",
        lambda force: calls.append(("stop", force)),
    )
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: _status(
            gateway="healthy",
            memory="healthy",
            supervisor="healthy",
        ),
    )

    result = desktop_control.execute("restart")

    assert calls == [("stop", True), ("start", True)]
    assert result["action"] == "restart"
    assert result["ok"] is True


def test_execute_reports_restart_blocked_as_failure(monkeypatch):
    monkeypatch.setattr(desktop_control, "ensure_running", lambda silent: {
        "supervisor": {"restart_blocked": "port_release_timeout"},
    })
    monkeypatch.setattr(desktop_control, "stop_all", lambda force: None)

    with pytest.raises(RuntimeError, match="port release timeout"):
        desktop_control.execute("restart")


def test_execute_plugin_rejects_healthy_service_without_gateway_registration(monkeypatch):
    descriptor = type("Descriptor", (), {
        "name": "sample",
        "capabilities": ("service",),
        "manifest": {"display_name": "Sample", "service": {"port": 6010}},
    })()
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.discover_plugin_manifests",
        lambda: [descriptor],
    )
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.is_plugin_enabled",
        lambda _descriptor: True,
    )
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: {"sample": {"name": "sample", "port": 6010, "running": False, "healthy": False}},
    )
    monkeypatch.setattr(desktop_control, "start_service", lambda _name: object())
    monkeypatch.setattr(desktop_control, "_wait_for_health", lambda *_args: True)
    monkeypatch.setattr(desktop_control, "_wait_for_gateway_service_type", lambda *_args: False)
    monkeypatch.setattr(desktop_control, "stop_service", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.SERVICES",
        {"sample": type("Service", (), {"gateway_service_type": "goal"})()},
    )

    with pytest.raises(RuntimeError, match="plugin registration failed: sample"):
        desktop_control.execute_plugin("sample", "start")


def test_execute_plugin_start_is_idempotent_for_existing_runtime_process(monkeypatch):
    descriptor = type("Descriptor", (), {
        "name": "sample",
        "capabilities": ("service",),
        "manifest": {"display_name": "Sample", "service": {"port": 6010}},
    })()
    service_info = type("Service", (), {
        "gateway_service_type": None,
        "pid_file": "sample.pid",
    })()
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.discover_plugin_manifests",
        lambda: [descriptor],
    )
    monkeypatch.setattr(
        "voidcube.extensions.plugins.registry.is_plugin_enabled",
        lambda _descriptor: True,
    )
    monkeypatch.setattr(
        desktop_control,
        "status_all",
        lambda: {"sample": {"name": "sample", "port": 6010, "running": True, "healthy": True}},
    )
    monkeypatch.setattr(desktop_control, "start_service", lambda _name: None)
    monkeypatch.setattr(desktop_control, "_read_pid", lambda _path: 4321)
    monkeypatch.setattr(desktop_control, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(desktop_control, "_process_belongs_to_runtime", lambda _pid: True)
    monkeypatch.setattr(desktop_control, "_wait_for_health", lambda *_args: True)
    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.SERVICES",
        {"sample": service_info},
    )
    monkeypatch.setattr(desktop_control, "snapshot", lambda action: {"action": action, "ok": True})

    result = desktop_control.execute_plugin("sample", "start")

    assert result["ok"] is True


def test_main_prints_one_json_document_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(
        desktop_control,
        "execute",
        lambda action: {
            "schemaVersion": 1,
            "action": action,
            "ok": True,
            "generatedAt": "2026-08-09T00:00:00+00:00",
            "services": [],
        },
    )

    assert desktop_control.main(["status"]) == 0

    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "schemaVersion": 1,
        "action": "status",
        "ok": True,
        "generatedAt": "2026-08-09T00:00:00+00:00",
        "services": [],
    }
    assert output.out.count("\n") == 1


def test_main_plugin_failure_keeps_current_snapshot(monkeypatch, capsys):
    snapshot = {
        "schemaVersion": 1,
        "action": "start",
        "ok": True,
        "generatedAt": "2026-09-30T00:00:00+00:00",
        "services": [],
        "plugins": [{"name": "sample", "service": {"state": "unhealthy"}}],
    }
    monkeypatch.setattr(desktop_control, "execute_plugin", lambda *_args: (_ for _ in ()).throw(RuntimeError("registration failed")))
    monkeypatch.setattr(desktop_control, "snapshot", lambda _action: snapshot.copy())

    assert desktop_control.main(["plugin", "sample", "start"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "registration failed"
    assert payload["plugins"] == snapshot["plugins"]
