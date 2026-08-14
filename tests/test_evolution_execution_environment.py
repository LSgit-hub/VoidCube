from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from systems.evolution_evaluation import (
    ExecutionEnvironmentManifest,
    build_container_environment_manifest,
    capture_host_environment_manifest,
    dependency_fingerprint,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_dependency_fingerprint_tracks_declared_inputs_only(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("pydantic==2.11.0\n", encoding="utf-8")
    initial = dependency_fingerprint(tmp_path)

    (tmp_path / "ordinary.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert dependency_fingerprint(tmp_path) == initial

    (tmp_path / "requirements.txt").write_text("pydantic==2.12.0\n", encoding="utf-8")
    assert dependency_fingerprint(tmp_path) != initial


def test_host_manifest_binds_virtualenv_toolchain_and_workspace():
    root = Path(__file__).parents[1]
    manifest = capture_host_environment_manifest(
        root,
        repository_head="a" * 40,
    )

    assert manifest.validation_scope == "host"
    assert manifest.validated_platforms == ("windows",)
    assert manifest.host_workspace_path == str(root.resolve())
    assert manifest.execution_workspace_path == str(root.resolve())
    python = next(
        tool
        for tool in manifest.tools
        if tool.scope == "host" and tool.name == "python"
    )
    pytest_tool = next(
        tool
        for tool in manifest.tools
        if tool.scope == "host" and tool.name == "pytest"
    )
    assert python.available is True
    assert ".venv" in python.executable
    assert pytest_tool.available is True
    assert "-m pytest" in pytest_tool.executable


def test_container_manifest_keeps_host_and_execution_identities_distinct(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    manifest = build_container_environment_manifest(
        tmp_path,
        backend="podman",
        execution_workspace_path="/workspace",
        probe={
            "os_name": "Linux",
            "os_release": "6.8.0",
            "architecture": "x86_64",
            "repository_head": "b" * 40,
            "tools": {
                "git": {
                    "executable": "/usr/bin/git",
                    "version": "git version 2.45.0",
                },
                "python": {
                    "executable": "/usr/bin/python3",
                    "version": "Python 3.12.0",
                },
            },
        },
    )

    assert manifest.validation_scope == "container"
    assert manifest.validated_platforms == ("linux",)
    assert manifest.execution_workspace_path == "/workspace"
    assert {tool.scope for tool in manifest.tools} == {"host", "execution"}
    sandbox_pytest = next(
        tool
        for tool in manifest.tools
        if tool.scope == "execution" and tool.name == "pytest"
    )
    assert sandbox_pytest.available is False


def test_manifest_content_address_rejects_tampering():
    manifest = capture_host_environment_manifest(
        Path(__file__).parents[1],
        repository_head="c" * 40,
    )
    payload = manifest.model_dump(mode="json")
    payload["backend"] = "forged"

    with pytest.raises(ValidationError, match="content_hash"):
        ExecutionEnvironmentManifest.model_validate(payload)


def test_manifest_cannot_claim_a_platform_different_from_execution_os():
    manifest = capture_host_environment_manifest(
        Path(__file__).parents[1],
        repository_head="d" * 40,
    )
    payload = manifest.content_payload()
    payload["execution_os"] = "Linux 6.8"

    with pytest.raises(ValidationError, match="execution operating system"):
        ExecutionEnvironmentManifest.create(**payload)
