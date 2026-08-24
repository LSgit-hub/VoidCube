import json
import subprocess
from types import SimpleNamespace

import pytest

import voidcube.infrastructure.execution.terminal_tool as terminal_tool_module
import voidcube.infrastructure.execution.tirith_security as tirith_security_module


def _allow_approval(command, env_type):
    return {
        "allowed": True,
        "approval_required": False,
        "approval_status": "approved",
    }


def _local_config():
    return {
        "env_type": "local",
        "fallback_to_local": True,
        "cwd": ".",
        "host_cwd": None,
        "timeout": 30,
        "local_persistent": False,
    }


@pytest.mark.unit
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_tirith_block_prevents_backend_initialization(monkeypatch, background, force):
    touched = []

    def fail_if_called():
        touched.append("config")
        raise AssertionError("backend configuration must not be loaded")

    monkeypatch.setattr(
        terminal_tool_module,
        "_check_tirith_security",
        lambda command: {
            "action": "block",
            "scanner_status": "available",
            "summary": "pipe-to-interpreter detected",
            "findings": [{"rule": "pipe-shell"}],
        },
    )
    monkeypatch.setattr(terminal_tool_module, "_get_env_config", fail_if_called)

    payload = json.loads(
        terminal_tool_module.terminal_tool(
            "download through interpreter",
            background=background,
            force=force,
        )
    )

    assert touched == []
    assert payload["status"] == "blocked"
    assert payload["security_scanner"] == "tirith"
    assert payload["security_scanner_status"] == "available"
    assert payload["security_findings"] == [{"rule": "pipe-shell"}]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("action", "expected_warning"),
    [("allow", None), ("warn", "suspicious URL")],
)
def test_tirith_allow_and_warn_execute_foreground(
    monkeypatch,
    action,
    expected_warning,
):
    task_id = f"tirith-{action}-test"
    executed = []
    env = SimpleNamespace(
        cwd=".",
        _voidcube_disk_quota_status="unsupported",
        execute=lambda command, **kwargs: (
            executed.append((command, kwargs))
            or {"output": "ok", "returncode": 0}
        ),
    )

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", _local_config)
    monkeypatch.setattr(
        terminal_tool_module,
        "_check_tirith_security",
        lambda command: {
            "action": action,
            "scanner_status": "available",
            "summary": "suspicious URL" if action == "warn" else "",
            "findings": [{"rule": "homograph"}] if action == "warn" else [],
        },
    )
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", _allow_approval)
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool_module, "_create_environment", lambda **kwargs: env)

    try:
        payload = json.loads(
            terminal_tool_module.terminal_tool("fetch example", task_id=task_id)
        )
    finally:
        terminal_tool_module.cleanup_vm(task_id)

    assert executed == [("fetch example", {"timeout": 30})]
    assert payload["output"] == "ok"
    assert payload["security_scanner_status"] == "available"
    assert payload["container_disk_quota_status"] == "unsupported"
    if expected_warning is None:
        assert "security_warning" not in payload
    else:
        assert payload["security_warning"] == expected_warning
        assert payload["security_findings"] == [{"rule": "homograph"}]


@pytest.mark.unit
def test_unknown_tirith_action_does_not_reach_backend(monkeypatch):
    touched = []

    monkeypatch.setattr(
        terminal_tool_module,
        "_check_tirith_security",
        lambda command: {"action": "invalid", "findings": [], "summary": ""},
    )
    monkeypatch.setattr(
        terminal_tool_module,
        "_get_env_config",
        lambda: touched.append("config"),
    )

    payload = json.loads(terminal_tool_module.terminal_tool("fetch example"))

    assert touched == []
    assert payload["status"] == "error"
    assert payload["security_scanner"] == "tirith"
    assert payload["security_scanner_status"] == "error"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "fail_open", "expected_action"),
    [
        ("spawn", True, "allow"),
        ("spawn", False, "block"),
        ("timeout", True, "allow"),
        ("timeout", False, "block"),
    ],
)
def test_tirith_operational_failure_respects_config(
    monkeypatch,
    failure,
    fail_open,
    expected_action,
):
    monkeypatch.setattr(
        tirith_security_module,
        "_load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith-test",
            "tirith_timeout": 3,
            "tirith_fail_open": fail_open,
        },
    )
    monkeypatch.setattr(
        tirith_security_module,
        "_resolve_tirith_path",
        lambda path: path,
    )

    def fail_run(*args, **kwargs):
        if failure == "spawn":
            raise OSError("scanner unavailable")
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(tirith_security_module.subprocess, "run", fail_run)

    result = tirith_security_module.check_command_security("fetch example")

    assert result["action"] == expected_action
    assert result["scanner_status"] in {"unavailable", "timeout"}
