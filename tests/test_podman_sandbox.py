from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import podman_sandbox


ROOT = Path(__file__).parents[1]


@pytest.mark.unit
def test_build_image_uses_packaged_containerfile(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(podman_sandbox.subprocess, "run", fake_run)
    podman_sandbox.build_image("localhost/test:latest", executable="podman")

    command, kwargs = calls[0]
    assert command[:4] == ["podman", "build", "--tag", "localhost/test:latest"]
    assert command[4] == "--file"
    assert command[5].endswith("tools\\containerfiles\\podman-agent.Containerfile") or command[5].endswith(
        "tools/containerfiles/podman-agent.Containerfile"
    )
    assert kwargs["check"] is True


@pytest.mark.unit
def test_status_reports_missing_image(monkeypatch, capsys):
    monkeypatch.setattr(podman_sandbox, "find_podman", lambda: "podman")
    monkeypatch.setattr(podman_sandbox, "image_exists", lambda image, executable: False)

    result = podman_sandbox.main(["status"])

    assert result == 1
    assert "Podman sandbox image missing" in capsys.readouterr().out


@pytest.mark.unit
def test_build_image_rejects_context_without_project_manifest(tmp_path):
    with pytest.raises(ValueError, match="pyproject.toml"):
        podman_sandbox.build_image("localhost/test:latest", executable="podman", context=tmp_path)


@pytest.mark.unit
def test_inspect_image_digest_requires_immutable_id(monkeypatch):
    monkeypatch.setattr(podman_sandbox.shutil, "which", lambda _: "podman")
    monkeypatch.setattr(
        podman_sandbox.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="latest\n", stderr=""),
    )
    from tools.environments import docker

    with pytest.raises(RuntimeError, match="immutable digest"):
        docker.inspect_image_digest("localhost/test:latest", runtime="podman")


@pytest.mark.unit
def test_inspect_image_digest_normalizes_podman_image_id(monkeypatch):
    from tools.environments import docker

    monkeypatch.setattr(docker, "find_container_executable", lambda _: "podman")
    monkeypatch.setattr(
        docker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="a" * 64 + "\n", stderr=""
        ),
    )

    assert docker.inspect_image_digest("localhost/test:latest") == "sha256:" + "a" * 64


@pytest.mark.unit
def test_project_containerfile_installs_declared_test_toolchains():
    content = (ROOT / "tools/containerfiles/podman-agent.Containerfile").read_text(
        encoding="utf-8"
    )

    assert "node:22.22.0-bookworm-slim" in content
    assert 'pip install --no-cache-dir "/opt/voidcube[dev]"' in content
    assert "npm ci --ignore-scripts" in content
    assert "desktop/package-lock.json" in content
