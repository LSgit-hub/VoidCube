from __future__ import annotations

import json

from VoidCube_cli import desktop_control


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


def test_execute_lifecycle_actions_delegate_to_canonical_service_owner(monkeypatch):
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        desktop_control,
        "ensure_running",
        lambda silent: calls.append(("start", silent)),
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
