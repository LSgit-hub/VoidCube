from __future__ import annotations

import os
from pathlib import Path
import importlib
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

launcher = importlib.import_module("voidcube.interfaces.cli.root_launcher")


pytestmark = pytest.mark.smoke


@pytest.mark.unit
def test_auto_start_daemons_reports_actual_startup_order(monkeypatch, capsys):
    calls = []

    def fake_ensure_running(*, silent: bool):
        calls.append({"silent": silent})
        return {
            "gateway": {"started": False},
            "memory": {"started": False},
            "supervisor": {"started": False},
        }

    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.ensure_running",
        fake_ensure_running,
    )
    monkeypatch.setenv("VOIDCUBE_DAEMONS_STARTED", "1")

    launcher._auto_start_daemons()

    output = capsys.readouterr().out
    assert "Gateway -> Memory -> Supervisor" in output
    assert "Memory -> Gateway -> Supervisor" not in output
    assert calls == [{"silent": False}]
    assert os.environ.get("VOIDCUBE_DAEMONS_STARTED") is None


@pytest.mark.unit
def test_auto_start_daemons_claims_ownership_only_after_real_start(
    monkeypatch,
    capsys,
):
    def fake_ensure_running(*, silent: bool):
        assert silent is False
        return {
            "gateway": {"started": False},
            "memory": {"started": True},
            "supervisor": {"started": False},
        }

    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.ensure_running",
        fake_ensure_running,
    )
    monkeypatch.delenv("VOIDCUBE_DAEMONS_STARTED", raising=False)

    launcher._auto_start_daemons()

    capsys.readouterr()
    assert os.environ.get("VOIDCUBE_DAEMONS_STARTED") == "1"


@pytest.mark.unit
def test_desktop_managed_cli_skips_wrapper_daemon_ownership(monkeypatch):
    calls = []
    monkeypatch.setenv("VOIDCUBE_DESKTOP_MANAGED_SERVICES", "1")
    monkeypatch.setattr(
        launcher,
        "_auto_start_daemons",
        lambda: pytest.fail("desktop-managed CLI attempted daemon startup"),
    )
    assert launcher.main([], cli_main=lambda: calls.append("cli")) == 0
    assert calls == ["cli"]
