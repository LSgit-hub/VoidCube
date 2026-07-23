from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from systems.config import SystemConfig

from VoidCube_cli.ops.serve import (
    _build_service_config,
    _service_python_path_entries,
    _verify_active_mem_import_source,
)


def test_serve_supervisor_config_honors_lm_generation_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_GENERATION_ENABLED", "0")

    config = _build_service_config("supervisor", 6123)

    assert config.port == 6123
    assert config.service_runtime.endogenous_drive_lm_task_generation_enabled is False


def test_serve_supervisor_config_copies_system_config_before_port_override():
    system_config = SystemConfig()
    original_port = system_config.supervisor.port

    config = _build_service_config("supervisor", 6123, system_config=system_config)

    assert config.port == 6123
    assert system_config.supervisor.port == original_port



def test_supervisor_lm_task_generation_is_enabled_by_default():
    config = _build_service_config("supervisor", 6123, system_config=SystemConfig())

    assert config.service_runtime.endogenous_drive_lm_task_generation_enabled is True


def test_service_subprocess_python_path_uses_only_repo_root():
    entries = _service_python_path_entries()

    assert len(entries) == 1
    assert not entries[0].endswith("Mem\\src")
    assert not entries[0].endswith("Mem/src")


def test_active_mem_import_source_matches_audited_binding(tmp_path, monkeypatch):
    source = tmp_path / "slot-B" / "Mem" / "src"
    expected = source / "memai" / "model_config.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("# active Mem\n", encoding="utf-8")
    (tmp_path / "mem-editable-binding.json").write_text(
        json.dumps({"source_path": str(source)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.config.get_config",
        lambda: SimpleNamespace(
            supervisor=SimpleNamespace(
                body_runtime=SimpleNamespace(state_root=str(tmp_path))
            )
        ),
    )
    model_config = ModuleType("memai.model_config")
    model_config.__file__ = str(expected)
    memai = ModuleType("memai")
    memai.model_config = model_config
    monkeypatch.setitem(sys.modules, "memai", memai)
    monkeypatch.setitem(sys.modules, "memai.model_config", model_config)

    result = _verify_active_mem_import_source()

    assert result == {"expected": str(expected), "loaded": str(expected)}


def test_active_mem_import_source_rejects_shadowed_package(tmp_path, monkeypatch):
    source = tmp_path / "slot-B" / "Mem" / "src"
    expected = source / "memai" / "model_config.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("# active Mem\n", encoding="utf-8")
    (tmp_path / "mem-editable-binding.json").write_text(
        json.dumps({"source_path": str(source)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.config.get_config",
        lambda: SimpleNamespace(
            supervisor=SimpleNamespace(
                body_runtime=SimpleNamespace(state_root=str(tmp_path))
            )
        ),
    )
    model_config = ModuleType("memai.model_config")
    model_config.__file__ = str(tmp_path / "shadow" / "memai" / "model_config.py")
    memai = ModuleType("memai")
    memai.model_config = model_config
    monkeypatch.setitem(sys.modules, "memai", memai)
    monkeypatch.setitem(sys.modules, "memai.model_config", model_config)

    with pytest.raises(RuntimeError, match="does not match the active Body binding"):
        _verify_active_mem_import_source()


def test_start_all_starts_gateway_before_memory_and_waits_for_registration(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(
        serve,
        "_sync_active_mem_binding_before_start",
        lambda: calls.append(("sync", "mem")),
    )
    monkeypatch.setattr(serve, "start_service", lambda name, foreground=False: calls.append(("start", name)))
    monkeypatch.setattr(serve, "_wait_for_health", lambda name, port: calls.append(("wait", name)) or True)
    monkeypatch.setattr(serve, "_wait_for_gateway_service_type", lambda service_type, timeout=20.0: calls.append(("registered", service_type)) or True)
    monkeypatch.setattr(serve, "print_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    serve.start_all(foreground=False)

    assert calls[:10] == [
        ("sync", "mem"),
        ("start", "gateway"),
        ("wait", "gateway"),
        ("start", "memory"),
        ("wait", "memory"),
        ("registered", "memory"),
        ("start", "supervisor"),
        ("wait", "supervisor"),
        ("registered", "supervisor"),
        ("registered", "executor"),
    ]


def test_ensure_running_restarts_healthy_unregistered_memory(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(serve, "_sync_active_mem_binding_before_start", lambda: None)
    monkeypatch.setattr(serve, "_read_pid", lambda path: 123)
    monkeypatch.setattr(serve, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(serve, "_health_check", lambda port: True)
    monkeypatch.setattr(serve, "_gateway_has_service_type", lambda service_type: False if service_type == "memory" else True)
    monkeypatch.setattr(serve, "stop_service", lambda name, silent=False: calls.append(("stop", name)) or True)
    monkeypatch.setattr(serve, "start_service", lambda name, foreground=False: calls.append(("start", name)) or object())
    monkeypatch.setattr(serve, "_wait_for_health", lambda name, port, timeout=30.0: calls.append(("wait", name)) or True)
    monkeypatch.setattr(serve, "_wait_for_gateway_service_type", lambda service_type, timeout=20.0: calls.append(("registered", service_type)) or True)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    result = serve.ensure_running(silent=True)

    assert ("stop", "memory") in calls
    assert ("start", "memory") in calls
    assert ("registered", "memory") in calls
    assert result["memory"]["registered"] is True


def test_ensure_running_restarts_supervisor_when_executor_registration_is_missing(
    monkeypatch,
    tmp_path,
):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(serve, "_sync_active_mem_binding_before_start", lambda: None)
    monkeypatch.setattr(serve, "_read_pid", lambda path: 123)
    monkeypatch.setattr(serve, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(serve, "_health_check", lambda port: True)
    monkeypatch.setattr(
        serve,
        "_gateway_has_service_type",
        lambda service_type: service_type != "executor",
    )
    monkeypatch.setattr(
        serve,
        "stop_service",
        lambda name, silent=False: calls.append(("stop", name)) or True,
    )
    monkeypatch.setattr(
        serve,
        "start_service",
        lambda name, foreground=False: calls.append(("start", name)) or object(),
    )
    monkeypatch.setattr(
        serve,
        "_wait_for_health",
        lambda name, port, timeout=30.0: calls.append(("wait", name)) or True,
    )
    monkeypatch.setattr(
        serve,
        "_wait_for_gateway_service_type",
        lambda service_type, timeout=20.0: calls.append(("registered", service_type)) or True,
    )
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    result = serve.ensure_running(silent=True)

    assert ("stop", "supervisor") in calls
    assert ("start", "supervisor") in calls
    assert ("registered", "supervisor") in calls
    assert ("registered", "executor") in calls
    assert result["supervisor"]["registered"] is True
