import json
import os
from types import SimpleNamespace

import pytest

import tools.terminal_tool as terminal_tool_module


@pytest.mark.unit
def test_terminal_runtime_reads_canonical_config_when_env_is_absent(monkeypatch):
    for name in (
        "TERMINAL_ENV",
        "TERMINAL_PODMAN_IMAGE",
        "TERMINAL_FALLBACK_TO_LOCAL",
        "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
        "TERMINAL_CWD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "VoidCube_app.config.load_config",
        lambda: {
            "terminal": {
                "backend": "podman",
                "podman_image": "localhost/test-sandbox:latest",
                "fallback_to_local": False,
                "docker_mount_cwd_to_workspace": True,
                "cwd": ".",
            }
        },
    )

    config = terminal_tool_module._get_env_config()

    assert config["env_type"] == "podman"
    assert config["podman_image"] == "localhost/test-sandbox:latest"
    assert config["fallback_to_local"] is False
    assert config["host_cwd"] == os.getcwd()
    assert config["cwd"] == "/workspace"


@pytest.mark.unit
def test_terminal_process_env_overrides_canonical_config(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(
        "VoidCube_app.config.load_config",
        lambda: {"terminal": {"backend": "podman"}},
    )

    assert terminal_tool_module._get_env_config()["env_type"] == "local"


@pytest.mark.unit
def test_create_environment_falls_back_to_local(monkeypatch):
    original = terminal_tool_module._create_environment_once

    def fake_create_once(env_type, *args, **kwargs):
        if env_type == "docker":
            raise RuntimeError("docker daemon unavailable")
        return original(env_type, *args, **kwargs)

    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)

    env = terminal_tool_module._create_environment(
        env_type="docker",
        image="ignored",
        cwd="/root",
        timeout=5,
        task_id="fallback-test-1",
    )

    assert env._voidcube_requested_backend == "docker"
    assert env._voidcube_active_backend == "local"
    assert "docker daemon unavailable" in env._voidcube_backend_warning


@pytest.mark.unit
def test_create_environment_respects_disabled_fallback(monkeypatch):
    def fake_create_once(env_type, *args, **kwargs):
        raise RuntimeError(f"{env_type} down")

    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)

    with pytest.raises(RuntimeError, match="docker down"):
        terminal_tool_module._create_environment(
            env_type="docker",
            image="ignored",
            cwd="/root",
            timeout=5,
            task_id="fallback-test-2",
            fallback_to_local=False,
        )


@pytest.mark.unit
def test_terminal_tool_reports_backend_fallback(monkeypatch):
    task_id = "fallback-test-3"

    def fake_get_env_config():
        return {
            "env_type": "docker",
            "fallback_to_local": True,
            "docker_image": "ignored",
            "singularity_image": "ignored",
            "modal_image": "ignored",
            "daytona_image": "ignored",
            "cwd": "/root",
            "host_cwd": None,
            "timeout": 30,
            "container_cpu": 1,
            "container_memory": 5120,
            "container_disk": 51200,
            "container_persistent": True,
            "modal_mode": "auto",
            "docker_volumes": [],
            "docker_mount_cwd_to_workspace": False,
            "local_persistent": False,
        }

    def fake_check_all_guards(command, env_type):
        return {"allowed": True, "approval_required": False, "approval_status": "approved"}

    def fake_create_once(env_type, *args, **kwargs):
        if env_type == "docker":
            raise RuntimeError("docker unavailable")
        return SimpleNamespace(
            cwd=".",
            execute=lambda command, **kw: {"output": "ok\n", "returncode": 0},
        )

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", fake_get_env_config)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", fake_check_all_guards)
    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)

    try:
        payload = json.loads(terminal_tool_module.terminal_tool("echo ok", task_id=task_id))
    finally:
        terminal_tool_module.cleanup_vm(task_id)

    assert payload["output"] == "ok"
    assert payload["requested_backend"] == "docker"
    assert payload["active_backend"] == "local"
    assert "docker unavailable" in payload["_warning"]


@pytest.mark.unit
def test_create_environment_podman_falls_back_to_local(monkeypatch):
    original = terminal_tool_module._create_environment_once

    def fake_create_once(env_type, *args, **kwargs):
        if env_type == "podman":
            raise RuntimeError("podman machine unavailable")
        return original(env_type, *args, **kwargs)

    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)

    env = terminal_tool_module._create_environment(
        env_type="podman",
        image="ignored",
        cwd="/root",
        timeout=5,
        task_id="fallback-test-podman-1",
    )

    assert env._voidcube_requested_backend == "podman"
    assert env._voidcube_active_backend == "local"
    assert "podman machine unavailable" in env._voidcube_backend_warning


@pytest.mark.unit
def test_terminal_tool_reports_podman_backend_fallback(monkeypatch):
    task_id = "fallback-test-podman-2"

    def fake_get_env_config():
        return {
            "env_type": "podman",
            "fallback_to_local": True,
            "docker_image": "ignored",
            "podman_image": "ignored",
            "singularity_image": "ignored",
            "modal_image": "ignored",
            "daytona_image": "ignored",
            "cwd": "/root",
            "host_cwd": None,
            "timeout": 30,
            "container_cpu": 1,
            "container_memory": 5120,
            "container_disk": 51200,
            "container_persistent": True,
            "modal_mode": "auto",
            "docker_volumes": [],
            "docker_mount_cwd_to_workspace": False,
            "local_persistent": False,
        }

    def fake_check_all_guards(command, env_type):
        return {"allowed": True, "approval_required": False, "approval_status": "approved"}

    def fake_create_once(env_type, *args, **kwargs):
        if env_type == "podman":
            raise RuntimeError("podman unavailable")
        return SimpleNamespace(
            cwd=".",
            execute=lambda command, **kw: {"output": "ok\n", "returncode": 0},
        )

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", fake_get_env_config)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", fake_check_all_guards)
    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)

    try:
        payload = json.loads(terminal_tool_module.terminal_tool("echo ok", task_id=task_id))
    finally:
        terminal_tool_module.cleanup_vm(task_id)

    assert payload["output"] == "ok"
    assert payload["requested_backend"] == "podman"
    assert payload["active_backend"] == "local"
    assert "podman unavailable" in payload["_warning"]
