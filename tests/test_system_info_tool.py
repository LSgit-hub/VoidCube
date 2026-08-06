from __future__ import annotations

import json

import pytest

from tools import ops_register
from tools.model_tools import get_tool_definitions, handle_function_call


@pytest.mark.unit
def test_system_info_reports_effective_podman_workspace(monkeypatch):
    monkeypatch.setattr(
        "tools.terminal_tool._get_env_config",
        lambda: {
            "env_type": "podman",
            "cwd": "/workspace",
            "host_cwd": r"F:\project",
            "docker_mount_cwd_to_workspace": True,
            "fallback_to_local": False,
        },
    )

    payload = json.loads(ops_register.system_info_tool())

    assert payload["terminal_backend"] == "podman"
    assert payload["terminal_cwd"] == "/workspace"
    assert payload["host_workspace"] == r"F:\project"
    assert payload["host_process_cwd"]
    assert payload["workspace_mounted"] is True
    assert payload["fallback_to_local"] is False


@pytest.mark.unit
def test_system_info_is_available_through_the_api_a_registry(monkeypatch):
    monkeypatch.setattr(
        "tools.terminal_tool._get_env_config",
        lambda: {
            "env_type": "local",
            "cwd": r"F:\project",
            "host_cwd": None,
            "docker_mount_cwd_to_workspace": False,
            "fallback_to_local": False,
        },
    )

    definitions = get_tool_definitions(enabled_toolsets=["system"], quiet_mode=True)
    names = {entry["function"]["name"] for entry in definitions}
    payload = json.loads(handle_function_call("system_info", {}))

    assert "system_info" in names
    assert payload["success"] is True
    assert payload["terminal_backend"] == "local"
    assert payload["host_workspace"] == r"F:\project"


@pytest.mark.unit
def test_system_info_does_not_claim_an_unmounted_host_workspace(monkeypatch):
    monkeypatch.setattr(
        "tools.terminal_tool._get_env_config",
        lambda: {
            "env_type": "ssh",
            "cwd": "~/work",
            "host_cwd": None,
            "docker_mount_cwd_to_workspace": False,
            "fallback_to_local": False,
        },
    )

    payload = json.loads(ops_register.system_info_tool())

    assert payload["terminal_cwd"] == "~/work"
    assert payload["host_process_cwd"]
    assert payload["host_workspace"] is None
    assert payload["workspace_mounted"] is False
