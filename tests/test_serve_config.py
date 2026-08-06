from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from systems.config import SystemConfig, load_config_from_env

from VoidCube_cli.ops.serve import (
    _build_service_config,
    _service_python_executable,
    _service_python_path_entries,
    _verify_canonical_mem_import_source,
)


def test_serve_supervisor_config_honors_lm_generation_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_GENERATION_ENABLED", "0")

    config = _build_service_config("supervisor", 6123)

    assert config.port == 6123
    assert config.service_runtime.endogenous_drive_lm_task_generation_enabled is False


def test_memory_timing_policy_honors_environment_overrides(monkeypatch):
    monkeypatch.setenv("MEMORY_TIER1_RETENTION_DAYS", "11")
    monkeypatch.setenv("MEMORY_TIER2_BATCH_SIZE", "17")
    monkeypatch.setenv("MEMORY_TIER2_SCOPE_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("MEMORY_LIFECYCLE_CADENCE_DAYS", "9")
    monkeypatch.setenv("MEMORY_EVENT_TO_SCENE_DAYS", "21")

    config = load_config_from_env().memory

    assert config.tier1_retention_days == 11
    assert config.tier2_batch_size == 17
    assert config.tier2_scope_timeout_seconds == 120
    assert config.lifecycle_cadence_days == 9
    assert config.lifecycle_event_to_scene_days == 21


def test_serve_supervisor_config_copies_system_config_before_port_override():
    system_config = SystemConfig()
    original_port = system_config.supervisor.port

    config = _build_service_config("supervisor", 6123, system_config=system_config)

    assert config.port == 6123
    assert system_config.supervisor.port == original_port



def test_supervisor_lm_task_generation_is_enabled_by_default():
    config = _build_service_config("supervisor", 6123, system_config=SystemConfig())

    assert config.service_runtime.endogenous_drive_lm_task_generation_enabled is True


def test_supervisor_reminder_policy_loads_from_canonical_config_before_env_overrides(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "supervisor": {
                    "service_runtime": {
                        "companion_proactive_reminder_enabled": False,
                        "companion_proactive_reminder_tts_enabled": False,
                        "companion_proactive_reminder_cooldown_seconds": 1200,
                        "companion_proactive_dnd_start": "21:30",
                        "companion_proactive_dnd_end": "07:15",
                    }
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.setenv("SUPERVISOR_COMPANION_PROACTIVE_REMINDER_ENABLED", "1")
    monkeypatch.setenv(
        "SUPERVISOR_COMPANION_PROACTIVE_REMINDER_COOLDOWN_SECONDS",
        "300",
    )

    config = load_config_from_env().supervisor.service_runtime

    assert config.companion_proactive_reminder_enabled is True
    assert config.companion_proactive_reminder_tts_enabled is False
    assert config.companion_proactive_reminder_cooldown_seconds == 300
    assert config.companion_proactive_dnd_start == "21:30"
    assert config.companion_proactive_dnd_end == "07:15"


def test_service_subprocess_python_path_includes_canonical_mem_source():
    entries = _service_python_path_entries()

    assert len(entries) == 2
    assert entries[1].endswith("Mem\\src") or entries[1].endswith("Mem/src")


def test_service_python_prefers_repository_venv_on_windows(tmp_path):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()

    resolved = _service_python_executable(
        tmp_path,
        current_executable=str(tmp_path / "system-python.exe"),
        platform="win32",
    )

    assert resolved == str(project_python.resolve())


def test_service_python_falls_back_to_current_interpreter_without_repository_venv(
    tmp_path,
):
    current_python = tmp_path / "installed-python.exe"

    resolved = _service_python_executable(
        tmp_path,
        current_executable=str(current_python),
        platform="win32",
    )

    assert resolved == str(current_python.resolve())


def test_background_service_uses_resolved_service_python(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    service = serve.SERVICES["supervisor"]
    selected_python = str(tmp_path / ".venv" / "Scripts" / "python.exe")
    popen_calls = []

    monkeypatch.setattr(service, "pid_file", str(tmp_path / "supervisor.pid"))
    monkeypatch.setattr(service, "log_file", str(tmp_path / "supervisor.log"))
    monkeypatch.setattr(serve, "_read_pid", lambda path: None)
    monkeypatch.setattr(serve, "_port_listening", lambda port: False)
    monkeypatch.setattr(serve, "_service_python_executable", lambda: selected_python)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        serve.subprocess,
        "Popen",
        lambda args, **kwargs: popen_calls.append((args, kwargs))
        or SimpleNamespace(pid=4321),
    )

    process = serve.start_service("supervisor", foreground=False)

    assert process.pid == 4321
    assert popen_calls[0][0][0] == selected_python
    assert (tmp_path / "supervisor.pid").read_text() == "4321"


def test_foreground_start_reexecs_with_service_python(monkeypatch):
    from VoidCube_cli.ops import serve

    calls = []
    monkeypatch.setattr(serve, "_running_with_service_python", lambda: False)
    monkeypatch.setattr(
        serve,
        "_restart_foreground_with_service_python",
        lambda: calls.append("restart"),
    )
    monkeypatch.setattr(
        serve,
        "_sync_canonical_mem_binding_before_start",
        lambda: calls.append("sync"),
    )

    serve.start_all(foreground=True)

    assert calls == ["restart"]


def test_start_service_adopts_existing_voidcube_process_when_pid_file_is_missing(
    monkeypatch,
    tmp_path,
):
    from VoidCube_cli.ops import serve

    service = serve.SERVICES["supervisor"]
    pid_file = tmp_path / "supervisor.pid"
    monkeypatch.setattr(service, "pid_file", str(pid_file))
    monkeypatch.setattr(serve, "_read_pid", lambda path: None)
    monkeypatch.setattr(serve, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(serve, "_port_listening", lambda port: True)
    monkeypatch.setattr(serve, "_port_owner_pid", lambda port: 8924)
    monkeypatch.setattr(serve, "_process_is_service", lambda pid, name: True)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    assert serve.start_service("supervisor", foreground=False) is None
    assert pid_file.read_text(encoding="utf-8") == "8924"


def test_canonical_mem_import_source_matches_repository_source(tmp_path, monkeypatch):
    source = tmp_path / "Mem" / "src"
    expected = source / "memai" / "model_config.py"
    expected.parent.mkdir(parents=True)
    expected.write_text(
        "def resolve_mem_llm_client(): pass\n"
        "def _resolve_mem_api_key(): pass\n",
        encoding="utf-8",
    )
    for required in (
        source / "memai" / "__init__.py",
        source / "memai" / "identity" / "founding_memory.json",
        source / "memai" / "identity" / "founding_story.md",
    ):
        required.parent.mkdir(parents=True, exist_ok=True)
        required.write_text(
            "def resolve_mem_llm_client(): pass\ndef _resolve_mem_api_key(): pass\n"
            if required.name == "model_config.py" else "{}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "systems.config.get_config",
        lambda: SimpleNamespace(
            supervisor=SimpleNamespace(
                execution=SimpleNamespace(git_repo_path=str(tmp_path))
            )
        ),
    )
    model_config = ModuleType("memai.model_config")
    model_config.__file__ = str(expected)
    memai = ModuleType("memai")
    memai.model_config = model_config
    monkeypatch.setitem(sys.modules, "memai", memai)
    monkeypatch.setitem(sys.modules, "memai.model_config", model_config)

    result = _verify_canonical_mem_import_source()

    assert result == {"expected": str(expected), "loaded": str(expected)}


def test_canonical_mem_import_source_rejects_shadowed_package(tmp_path, monkeypatch):
    source = tmp_path / "Mem" / "src"
    expected = source / "memai" / "model_config.py"
    expected.parent.mkdir(parents=True)
    expected.write_text(
        "def resolve_mem_llm_client(): pass\n"
        "def _resolve_mem_api_key(): pass\n",
        encoding="utf-8",
    )
    for required in (
        source / "memai" / "__init__.py",
        source / "memai" / "identity" / "founding_memory.json",
        source / "memai" / "identity" / "founding_story.md",
    ):
        required.parent.mkdir(parents=True, exist_ok=True)
        required.write_text(
            "def resolve_mem_llm_client(): pass\ndef _resolve_mem_api_key(): pass\n"
            if required.name == "model_config.py" else "{}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "systems.config.get_config",
        lambda: SimpleNamespace(
            supervisor=SimpleNamespace(
                execution=SimpleNamespace(git_repo_path=str(tmp_path))
            )
        ),
    )
    model_config = ModuleType("memai.model_config")
    model_config.__file__ = str(tmp_path / "shadow" / "memai" / "model_config.py")
    memai = ModuleType("memai")
    memai.model_config = model_config
    monkeypatch.setitem(sys.modules, "memai", memai)
    monkeypatch.setitem(sys.modules, "memai.model_config", model_config)

    with pytest.raises(RuntimeError, match="does not match the canonical shared binding"):
        _verify_canonical_mem_import_source()


def test_start_all_starts_gateway_before_memory_and_waits_for_registration(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(
        serve,
        "_sync_canonical_mem_binding_before_start",
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


def test_stop_service_on_windows_terminates_venv_process_tree(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    service = serve.SERVICES["supervisor"]
    pid_file = tmp_path / "supervisor.pid"
    pid_file.write_text("4321\n", encoding="ascii")
    calls = []

    monkeypatch.setattr(service, "pid_file", str(pid_file))
    monkeypatch.setattr(serve.sys, "platform", "win32")
    monkeypatch.setattr(serve, "_pid_alive", lambda pid: pid == 4321)
    monkeypatch.setattr(
        serve.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )

    assert serve.stop_service("supervisor", silent=True) is True
    assert calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert not pid_file.exists()


def test_ensure_running_restarts_healthy_unregistered_memory(monkeypatch, tmp_path):
    from VoidCube_cli.ops import serve

    calls = []

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(serve, "_sync_canonical_mem_binding_before_start", lambda: None)
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
    monkeypatch.setattr(serve, "_sync_canonical_mem_binding_before_start", lambda: None)
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
