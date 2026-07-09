from __future__ import annotations

from systems.config import SystemConfig

from VoidCube_cli.ops.serve import _build_service_config


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


def test_start_all_waits_for_memory_after_gateway(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(serve, "start_service", lambda name, foreground=False: calls.append(("start", name)))
    monkeypatch.setattr(serve, "_wait_for_health", lambda name, port: calls.append(("wait", name)) or True)
    monkeypatch.setattr(serve, "print_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    serve.start_all(foreground=False)

    assert calls[:5] == [
        ("start", "memory"),
        ("start", "gateway"),
        ("wait", "gateway"),
        ("wait", "memory"),
        ("start", "supervisor"),
    ]
