from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voidcube.interfaces.cli.config_validator as config_validator
from voidcube.interfaces.cli.config_validator import AgentCheck, ConfigIssue, Severity
from voidcube.interfaces.cli.main import main as cli_main


@pytest.mark.unit
def test_validate_all_includes_agent_checks_and_error_state(monkeypatch):
    config_issue = ConfigIssue(
        severity=Severity.WARNING,
        key_path="providers.demo",
        message="demo warning",
        suggestion="fix provider",
    )
    agent_check = AgentCheck(
        severity=Severity.ERROR,
        name="tool_call_smoke",
        message="tool smoke failed",
        suggestion="repair tool chain",
    )

    monkeypatch.setattr(config_validator, "load_config", lambda: {"terminal": {"backend": "local"}})
    monkeypatch.setattr(config_validator, "validate_config", lambda: [config_issue])
    monkeypatch.setattr(config_validator, "collect_agent_diagnostics", lambda cfg: [agent_check])

    report = config_validator.validate_all()

    assert report["config_issues"] == [config_issue]
    assert report["agent_checks"] == [agent_check]
    assert report["has_errors"] is True
    assert report["has_warnings"] is True


@pytest.mark.unit
def test_print_diagnosis_renders_agent_checks(monkeypatch, capsys):
    report = {
        "config_issues": [],
        "invalid_aliases": [],
        "agent_checks": [
            AgentCheck(
                severity=Severity.WARNING,
                name="docker_runtime",
                message="docker version failed",
                suggestion="start Docker Desktop",
                details="daemon unavailable",
            ),
            AgentCheck(
                severity=Severity.INFO,
                name="tool_call_smoke",
                message="tool smoke ok",
                details="write -> patch -> search -> read",
            ),
        ],
        "has_errors": False,
        "has_warnings": True,
    }

    monkeypatch.setattr(config_validator, "validate_all", lambda: report)

    config_validator.print_diagnosis()

    output = capsys.readouterr().out
    assert "Agent 工具链检查" in output
    assert "[docker_runtime] docker version failed" in output
    assert "daemon unavailable" in output
    assert "start Docker Desktop" in output
    assert "[tool_call_smoke] tool smoke ok" in output


@pytest.mark.unit
def test_cli_doctor_command_invokes_print_diagnosis(monkeypatch):
    printer = Mock()
    monkeypatch.setattr("voidcube.interfaces.cli.config_validator.print_diagnosis", printer)
    monkeypatch.setattr(sys, "argv", ["VoidCube", "doctor"])

    cli_main()

    printer.assert_called_once_with()


@pytest.mark.unit
def test_doctor_requires_podman_when_it_is_the_non_fallback_backend(monkeypatch):
    monkeypatch.setattr(config_validator.shutil, "which", lambda name: None)

    check = config_validator._diagnose_podman(
        {"terminal": {"backend": "podman", "fallback_to_local": False}}
    )

    assert check.severity == Severity.ERROR
    assert "未检测到 podman" in check.message


@pytest.mark.unit
def test_doctor_does_not_warn_for_an_unused_container_runtime(monkeypatch):
    monkeypatch.setattr(config_validator.shutil, "which", lambda name: None)
    cfg = {"terminal": {"backend": "local", "fallback_to_local": False}}

    docker_check = config_validator._diagnose_docker(cfg)
    podman_check = config_validator._diagnose_podman(cfg)

    assert docker_check.severity == Severity.INFO
    assert podman_check.severity == Severity.INFO


@pytest.mark.unit
def test_doctor_requires_the_configured_podman_image(monkeypatch):
    calls = []
    monkeypatch.setattr(config_validator.shutil, "which", lambda name: "podman")

    def fake_run(command, timeout=5):
        calls.append(command)
        return (True, "ok") if command[-1] == "version" else (False, "missing")

    monkeypatch.setattr(config_validator, "_run_command", fake_run)
    check = config_validator._diagnose_podman(
        {
            "terminal": {
                "backend": "podman",
                "fallback_to_local": False,
                "podman_image": "localhost/test-sandbox:latest",
            }
        }
    )

    assert check.severity == Severity.ERROR
    assert check.data["podman_image"] == "localhost/test-sandbox:latest"
    assert calls[-1] == ["podman", "image", "exists", "localhost/test-sandbox:latest"]


def _stub_body_system_config(monkeypatch, tmp_path: Path) -> None:
    supervisor = SimpleNamespace(
        execution=SimpleNamespace(git_repo_path=str(tmp_path)),
        body_runtime=SimpleNamespace(
            slot_a_name="slot-A",
            slot_b_name="slot-B",
            state_root=str(tmp_path),
        ),
    )
    monkeypatch.setattr(
        "voidcube.infrastructure.config.system.get_config",
        lambda: SimpleNamespace(supervisor=supervisor),
    )


@pytest.mark.unit
def test_doctor_reports_healthy_body_registry(monkeypatch, tmp_path):
    from voidcube.systems.body_registry import BodyRegistryManager

    BodyRegistryManager(tmp_path, state_root=tmp_path).initialize_layout()
    _stub_body_system_config(monkeypatch, tmp_path)

    check = config_validator._diagnose_body_registry()

    assert check.severity == Severity.INFO
    assert check.name == "body_registry"
    assert check.data["healthy"] is True


@pytest.mark.unit
def test_doctor_reports_broken_body_registry(monkeypatch, tmp_path):
    from voidcube.systems.body_registry import BodyRegistryManager

    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.slot_worktree_manifest_path("slot-A").unlink()
    _stub_body_system_config(monkeypatch, tmp_path)

    check = config_validator._diagnose_body_registry()

    assert check.severity == Severity.ERROR
    assert check.name == "body_registry"
    assert "slot_not_materialized" in check.details


def _valid_api_a_api_b_config() -> dict:
    return {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
                "auth_mode": "stored",
            },
            "deepseek": {
                "label": "DeepSeek",
                "selected_model": "deepseek-v4-flash",
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url": "https://api.deepseek.com/v1",
                "auth_mode": "env",
            },
        },
        "memory": {
            "provider": "mem",
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            },
        },
    }


def _stub_api_b_key_sources(monkeypatch, *, env_value: str = "") -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("voidcube.infrastructure.config.configuration.get_env_value", lambda key: env_value)
    monkeypatch.setattr("voidcube.infrastructure.config.configuration.load_env", lambda: {})
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.credentials.resolve_api_key_provider_credentials",
        lambda provider: {"api_key": "", "access_token": ""},
    )
    monkeypatch.setattr("voidcube.infrastructure.providers.credentials.read_credential_pool", lambda provider=None: [])
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.credential_pool.load_pool",
        lambda provider: _EmptyCredentialPool(),
    )


class _EmptyCredentialPool:
    def has_credentials(self) -> bool:
        return False

    def select(self):
        return None


@pytest.mark.unit
def test_validate_config_reports_missing_api_b_deepseek_key(monkeypatch):
    monkeypatch.setattr(config_validator, "load_config", _valid_api_a_api_b_config)
    _stub_api_b_key_sources(monkeypatch)

    issues = config_validator.validate_config()

    assert any(issue.key_path == "memory.llm.api_key_env" for issue in issues)
    assert any("DEEPSEEK_API_KEY" in issue.message for issue in issues)


@pytest.mark.unit
def test_validate_config_does_not_use_api_a_agnes_key_for_api_b(monkeypatch):
    cfg = _valid_api_a_api_b_config()
    cfg["providers"]["agnes-ai"]["api_key"] = "sk-agnes-user-chat-token-abcdef"
    monkeypatch.setattr(config_validator, "load_config", lambda: cfg)
    _stub_api_b_key_sources(monkeypatch)

    issues = config_validator.validate_config()

    assert any(
        issue.key_path == "memory.llm.api_key_env"
        and "DEEPSEEK_API_KEY" in issue.message
        for issue in issues
    )


@pytest.mark.unit
def test_validate_config_treats_template_api_b_key_as_missing(monkeypatch):
    monkeypatch.setattr(config_validator, "load_config", _valid_api_a_api_b_config)
    _stub_api_b_key_sources(monkeypatch, env_value="sk-your-key-here")

    issues = config_validator.validate_config()

    assert any(issue.key_path == "memory.llm.api_key_env" for issue in issues)


@pytest.mark.unit
def test_validate_config_accepts_valid_api_b_env_key(monkeypatch):
    monkeypatch.setattr(config_validator, "load_config", _valid_api_a_api_b_config)
    _stub_api_b_key_sources(monkeypatch, env_value="sk-real-deepseek-token-123456789")

    issues = config_validator.validate_config()

    assert not [issue for issue in issues if issue.key_path.startswith("memory.llm.")]


@pytest.mark.unit
def test_validate_config_accepts_custom_api_b_provider(monkeypatch):
    cfg = _valid_api_a_api_b_config()
    cfg["memory"]["llm"].update(
        {
            "provider": "custom",
            "model": "memory-reasoner",
        }
    )
    cfg["providers"]["custom"] = {
        "base_url": "https://memory.example/v1",
        "api_key_env": "VOIDCUBE_MEMORY_CUSTOM_API_KEY",
        "auth_mode": "env",
    }
    monkeypatch.setattr(config_validator, "load_config", lambda: cfg)
    _stub_api_b_key_sources(monkeypatch, env_value="sk-real-memory-token-123456789")

    issues = config_validator.validate_config()

    assert not [issue for issue in issues if issue.key_path.startswith("memory.llm.")]


@pytest.mark.unit
def test_validate_config_rejects_custom_api_b_without_valid_base_url(monkeypatch):
    cfg = _valid_api_a_api_b_config()
    cfg["memory"]["llm"].update(
        {
            "provider": "custom",
            "model": "memory-reasoner",
        }
    )
    cfg["providers"]["custom"] = {
        "base_url": "memory.example/v1",
        "api_key_env": "VOIDCUBE_MEMORY_CUSTOM_API_KEY",
        "auth_mode": "env",
    }
    monkeypatch.setattr(config_validator, "load_config", lambda: cfg)
    _stub_api_b_key_sources(monkeypatch, env_value="sk-real-memory-token-123456789")

    issues = config_validator.validate_config()

    assert any(issue.key_path == "memory.llm.base_url" for issue in issues)


@pytest.mark.unit
def test_validate_config_rejects_unsupported_api_b_provider(monkeypatch):
    cfg = _valid_api_a_api_b_config()
    cfg["memory"]["llm"]["provider"] = "retired-provider"
    monkeypatch.setattr(config_validator, "load_config", lambda: cfg)
    _stub_api_b_key_sources(monkeypatch, env_value="sk-real-retired-token-123456789")

    issues = config_validator.validate_config()

    assert any(
        issue.key_path == "memory.llm.provider" and "retired-provider" in issue.message
        for issue in issues
    )


@pytest.mark.unit
def test_validate_config_rejects_api_b_gateway_loopback_base_url(monkeypatch):
    cfg = _valid_api_a_api_b_config()
    cfg["providers"]["deepseek"]["base_url"] = "http://127.0.0.1:6000/v1"
    monkeypatch.setattr(config_validator, "load_config", lambda: cfg)
    _stub_api_b_key_sources(monkeypatch, env_value="sk-real-deepseek-token-123456789")

    issues = config_validator.validate_config()

    assert any(issue.key_path == "memory.llm.base_url" for issue in issues)


@pytest.mark.unit
def test_docker_fix_suggestion_mentions_docker_desktop_for_windows_pipe_error():
    suggestion = config_validator._suggest_docker_fix(
        'error during connect: open //./pipe/docker_engine: The system cannot find the file specified.',
        requested_backend="docker",
        fallback_to_local=True,
    )

    assert "Docker Desktop" in suggestion
    assert "docker version" in suggestion or "named pipe" in suggestion


@pytest.mark.unit
def test_podman_fix_suggestion_mentions_machine_init_for_missing_machine():
    suggestion = config_validator._suggest_podman_fix(
        "Cannot connect to Podman. Try `podman machine init` and `podman machine start`."
    )

    assert "podman machine init" in suggestion
    assert "podman machine start" in suggestion
