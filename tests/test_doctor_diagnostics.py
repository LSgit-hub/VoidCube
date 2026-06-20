from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import VoidCube_cli.config_validator as config_validator
from VoidCube_cli.config_validator import AgentCheck, ConfigIssue, Severity
from VoidCube_cli.main import main as cli_main


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
    monkeypatch.setattr("VoidCube_cli.config_validator.print_diagnosis", printer)
    monkeypatch.setattr(sys, "argv", ["VoidCube", "doctor"])

    cli_main()

    printer.assert_called_once_with()


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
