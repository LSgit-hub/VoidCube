from __future__ import annotations

from systems.config import SystemConfig

from VoidCube_cli.ops.serve import _build_service_config, _service_python_path_entries


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


def test_service_subprocess_python_path_includes_mem_src():
    entries = _service_python_path_entries()

    assert any(entry.endswith("Mem\\src") or entry.endswith("Mem/src") for entry in entries)


def test_start_all_starts_gateway_before_memory_and_waits_for_registration(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(serve, "start_service", lambda name, foreground=False: calls.append(("start", name)))
    monkeypatch.setattr(serve, "_wait_for_health", lambda name, port: calls.append(("wait", name)) or True)
    monkeypatch.setattr(serve, "_wait_for_gateway_service_type", lambda service_type, timeout=20.0: calls.append(("registered", service_type)) or True)
    monkeypatch.setattr(serve, "print_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    serve.start_all(foreground=False)

    assert calls[:7] == [
        ("start", "gateway"),
        ("wait", "gateway"),
        ("start", "memory"),
        ("wait", "memory"),
        ("registered", "memory"),
        ("start", "supervisor"),
        ("wait", "supervisor"),
    ]


def test_ensure_running_restarts_healthy_unregistered_memory(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
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
