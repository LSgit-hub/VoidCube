from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import tools.file_tools as file_tools_module
import tools.terminal_tool as terminal_tool_module
from tools.task_execution import (
    TaskExecutionBlocked,
    TaskExecutionContract,
    begin_task_execution,
    clear_task_execution_state,
    configure_task_execution,
    ensure_task_execution_path,
    ensure_task_execution_request,
    get_task_execution_state,
    mark_task_execution_ready,
    release_task_execution,
    validate_task_environment_manifest,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.fixture(autouse=True)
def _clear_execution_state():
    clear_task_execution_state()
    yield
    clear_task_execution_state()
    file_tools_module.clear_file_ops_cache()


def _container_contract(task_id: str, **updates: object) -> TaskExecutionContract:
    values = {
        "task_id": task_id,
        "backend": "podman",
        "validation_scope": "container",
        "host_workspace_path": r"F:\repo\candidate",
        "execution_workspace_path": "/workspace",
        "allowed_execution_paths": ("/workspace",),
        "allowed_environment_variables": (),
        "command_timeout_seconds": 60,
        "max_output_chars": 10_000,
        "required_tools": ("git", "python", "pytest"),
        "required_platforms": ("linux",),
    }
    values.update(updates)
    return TaskExecutionContract.model_validate(values)


def test_task_execution_state_lifecycle_is_explicit():
    contract = _container_contract("state-task")

    assert configure_task_execution(contract).status == "configured"
    assert begin_task_execution(contract.task_id).status == "starting"
    ready = mark_task_execution_ready(contract.task_id, active_backend="podman")
    assert ready is not None and ready.status == "ready"
    assert ready.active_backend == "podman"
    released = release_task_execution(contract.task_id)
    assert released is not None and released.status == "released"


def test_strict_task_rejects_local_fallback_and_records_block():
    contract = _container_contract("fallback-task")
    configure_task_execution(contract)

    with pytest.raises(TaskExecutionBlocked) as captured:
        ensure_task_execution_request(
            contract.task_id,
            requested_backend="podman",
            workdir="/workspace",
            timeout_seconds=30,
            fallback_to_local=True,
        )

    assert captured.value.code == "backend_fallback_forbidden"
    state = get_task_execution_state(contract.task_id)
    assert state is not None and state.status == "blocked"


def test_task_paths_are_limited_to_declared_workspace():
    contract = _container_contract("path-task")
    configure_task_execution(contract)

    ensure_task_execution_path(contract.task_id, "/workspace/src/module.py")
    with pytest.raises(TaskExecutionBlocked) as captured:
        ensure_task_execution_path(contract.task_id, "/etc/passwd")

    assert captured.value.code == "path_outside_allowed_paths"


def test_environment_probe_blocks_when_required_tool_is_missing():
    contract = _container_contract("probe-task")
    configure_task_execution(contract)
    manifest = {
        "backend": "podman",
        "validation_scope": "container",
        "execution_workspace_path": "/workspace",
        "validated_platforms": ("linux",),
        "tools": (
            {"scope": "execution", "name": "git", "available": True},
            {"scope": "execution", "name": "python", "available": True},
            {"scope": "execution", "name": "pytest", "available": False},
        ),
    }

    with pytest.raises(TaskExecutionBlocked) as captured:
        validate_task_environment_manifest(contract.task_id, manifest)

    assert captured.value.code == "required_tool_unavailable"
    assert "pytest" in captured.value.reason


def test_terminal_start_failure_is_blocked_without_local_fallback(monkeypatch):
    task_id = "strict-terminal-task"
    configure_task_execution(
        _container_contract(
            task_id,
            required_tools=(),
            allowed_environment_variables=(),
        )
    )
    attempts: list[str] = []

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", _podman_config)
    monkeypatch.setattr(
        terminal_tool_module,
        "_check_tirith_security",
        lambda _command: {"action": "allow", "findings": []},
    )
    monkeypatch.setattr(
        terminal_tool_module,
        "_check_all_guards",
        lambda _command, _backend: {
            "allowed": True,
            "approval_required": False,
            "approval_status": "approved",
        },
    )
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)

    def fail_backend(env_type, *args, **kwargs):
        attempts.append(env_type)
        raise RuntimeError("podman unavailable")

    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fail_backend)

    payload = json.loads(
        terminal_tool_module.terminal_tool("git status", task_id=task_id, timeout=30)
    )

    assert payload["status"] == "blocked"
    assert payload["block_code"] == "environment_start_failed"
    assert attempts == ["podman"]


def test_file_and_terminal_tools_share_one_task_environment(monkeypatch, tmp_path):
    task_id = "shared-environment-task"
    contract = TaskExecutionContract(
        task_id=task_id,
        backend="local",
        validation_scope="host",
        host_workspace_path=str(tmp_path),
        execution_workspace_path=str(tmp_path),
        allowed_execution_paths=(str(tmp_path),),
        command_timeout_seconds=30,
        max_output_chars=10_000,
    )
    configure_task_execution(contract)
    created: list[object] = []

    class FakeEnvironment:
        cwd = str(tmp_path)
        env = {}

        def execute(self, _command, **_kwargs):
            return {"output": "ok\n", "returncode": 0}

        def cleanup(self):
            return None

    environment = FakeEnvironment()

    def local_config():
        return {
            **_podman_config(),
            "env_type": "local",
            "cwd": str(tmp_path),
            "host_cwd": str(tmp_path),
            "fallback_to_local": False,
        }

    def create_once(*_args, **_kwargs):
        created.append(environment)
        return environment

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", local_config)
    monkeypatch.setattr(
        terminal_tool_module,
        "_check_tirith_security",
        lambda _command: {"action": "allow", "findings": []},
    )
    monkeypatch.setattr(
        terminal_tool_module,
        "_check_all_guards",
        lambda _command, _backend: {
            "allowed": True,
            "approval_required": False,
            "approval_status": "approved",
        },
    )
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", create_once)

    try:
        file_ops = file_tools_module._get_file_ops(task_id)
        payload = json.loads(terminal_tool_module.terminal_tool("pwd", task_id=task_id))
    finally:
        terminal_tool_module.cleanup_vm(task_id)

    assert payload["output"] == "ok"
    assert file_ops.env is environment
    assert created == [environment]
    state = get_task_execution_state(task_id)
    assert state is not None and state.status == "ready"


def _podman_config() -> dict[str, object]:
    return {
        "env_type": "podman",
        "fallback_to_local": True,
        "docker_image": "ignored",
        "podman_image": "ignored",
        "singularity_image": "ignored",
        "modal_image": "ignored",
        "daytona_image": "ignored",
        "cwd": "/workspace",
        "host_cwd": None,
        "timeout": 30,
        "container_cpu": 1,
        "container_memory": 5120,
        "container_disk": 51200,
        "container_persistent": True,
        "modal_mode": "auto",
        "docker_volumes": [],
        "docker_mount_cwd_to_workspace": False,
        "docker_forward_env": [],
        "docker_env": {},
        "local_persistent": False,
    }
