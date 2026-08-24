from __future__ import annotations

import pytest

from voidcube.infrastructure.gateway import daemon_runtime


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def setup_function() -> None:
    daemon_runtime.clear_daemons_auto_started()


def teardown_function() -> None:
    daemon_runtime.clear_daemons_auto_started()


def test_auto_start_records_ownership_only_when_a_daemon_started(monkeypatch, capsys):
    calls: list[object] = []

    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.ensure_running",
        lambda *, silent: calls.append(("ensure", silent))
        or {"gateway": {"started": True}},
    )
    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.print_status",
        lambda: calls.append(("status",)),
    )

    daemon_runtime.auto_start_daemons()

    assert daemon_runtime.daemons_auto_started() is True
    assert calls == [("ensure", False), ("status",)]
    assert "Gateway -> Memory -> Supervisor" in capsys.readouterr().out


def test_auto_start_does_not_claim_ownership_when_services_are_already_running(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.ensure_running",
        lambda *, silent: {"gateway": {"started": False}},
    )
    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.print_status",
        lambda: pytest.fail("status should not be printed when nothing started"),
    )

    daemon_runtime.auto_start_daemons()

    assert daemon_runtime.daemons_auto_started() is False


def test_maybe_stop_releases_ownership_after_shutdown(monkeypatch):
    calls: list[bool] = []
    daemon_runtime.mark_daemons_auto_started()
    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.service_launcher.stop_all",
        lambda *, force: calls.append(force),
    )

    daemon_runtime.maybe_stop_daemons_on_exit(force=True)

    assert calls == [True]
    assert daemon_runtime.daemons_auto_started() is False
