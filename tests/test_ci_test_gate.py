from __future__ import annotations

import subprocess
from pathlib import Path
import tomllib

import pytest
import yaml

from scripts import run_ci_tests as gate


pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[1]


def test_ci_gate_runs_full_pytest_with_thirty_minute_timeout(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    assert gate.run_ci_tests() == 0
    assert captured["command"][-3:] == ["tests", "Mem/tests", "-q"]
    assert captured["cwd"] == gate.ROOT
    assert captured["check"] is False
    assert captured["timeout"] == 30 * 60


def test_ci_gate_rejects_shorter_timeout(monkeypatch):
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("pytest must not start"),
    )

    with pytest.raises(ValueError, match="at least 1800 seconds"):
        gate.run_ci_tests(timeout_seconds=1799)


def test_ci_gate_returns_standard_timeout_exit_code(monkeypatch, capsys):
    def expire(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(gate.subprocess, "run", expire)

    assert gate.run_ci_tests(["tests/test_memory_recall_benchmark.py"]) == 124
    assert "exceeded 1800 seconds" in capsys.readouterr().err


def test_gitee_ci_uses_python_314_and_unified_gate():
    workflow = yaml.safe_load(
        (ROOT / ".workflow" / "voidcube-ci.yml").read_text(encoding="utf-8")
    )

    assert workflow["version"] == "1.0"
    assert workflow["triggers"]["push"]["branches"]["include"] == ["master"]
    assert workflow["triggers"]["pr"]["branches"]["include"] == ["master"]

    stages = workflow["stages"]
    assert len(stages) == 1
    steps = stages[0]["steps"]
    assert len(steps) == 1
    assert steps[0]["step"] == "build@python"
    assert steps[0]["pythonVersion"] == "3.14"
    assert steps[0]["commands"] == [
        "python3 -m pip install --upgrade pip",
        'python3 -m pip install -e ".[all,dev]"',
        "python3 -m ruff check .",
        "python3 -m ruff check . --select F821 --exit-zero",
        "python3 -m pip_audit --vulnerability-service osv --progress-spinner off",
        "python3 scripts/run_ci_tests.py -- tests Mem/tests -q --cov "
        "--cov-report=term --cov-report=xml:coverage.xml",
    ]


def test_root_and_mem_packages_are_fixed_to_python_314():
    root_project = yaml.safe_load(
        (ROOT / ".workflow" / "voidcube-ci.yml").read_text(encoding="utf-8")
    )
    assert root_project["stages"][0]["steps"][0]["pythonVersion"] == "3.14"

    root_config = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    mem_config = tomllib.loads(
        (ROOT / "Mem" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert root_config["project"]["requires-python"] == ">=3.14,<3.15"
    assert mem_config["project"]["requires-python"] == ">=3.14,<3.15"
