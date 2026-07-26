from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from systems.config import SystemConfig, load_config_from_env

from VoidCube_cli.ops.serve import (
    _build_service_config,
    _service_python_path_entries,
    _verify_canonical_mem_import_source,
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
