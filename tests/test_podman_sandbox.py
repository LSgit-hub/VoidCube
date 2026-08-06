from __future__ import annotations

import subprocess

import pytest

from tools import podman_sandbox


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
