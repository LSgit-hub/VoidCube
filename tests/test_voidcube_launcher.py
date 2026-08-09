from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voidcube


pytestmark = pytest.mark.smoke


@pytest.mark.unit
def test_auto_start_daemons_reports_actual_startup_order(monkeypatch, capsys):
    calls = []

    def fake_ensure_running(*, silent: bool):
        calls.append({"silent": silent})
        return {}

    monkeypatch.setattr(
        "VoidCube_cli.ops.serve.ensure_running",
        fake_ensure_running,
    )
    monkeypatch.delenv("VOIDCUBE_DAEMONS_STARTED", raising=False)

    voidcube._auto_start_daemons()

    output = capsys.readouterr().out
    assert "Gateway -> Memory -> Supervisor" in output
    assert "Memory -> Gateway -> Supervisor" not in output
    assert calls == [{"silent": False}]


@pytest.mark.unit
def test_desktop_managed_cli_skips_wrapper_daemon_ownership(monkeypatch):
    calls = []
    monkeypatch.setenv("VOIDCUBE_DESKTOP_MANAGED_SERVICES", "1")
    monkeypatch.setattr(
        voidcube,
        "_auto_start_daemons",
        lambda: pytest.fail("desktop-managed CLI attempted daemon startup"),
    )
    monkeypatch.setattr(
        "VoidCube_cli.main.main",
        lambda: calls.append("cli"),
    )

    assert voidcube.main([]) == 0
    assert calls == ["cli"]
