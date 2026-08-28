from __future__ import annotations

import pytest

from voidcube.infrastructure.gateway import daemon_runtime
import voidcube.infrastructure.gateway.service_launcher as service_launcher


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


def test_stop_all_uses_snapshot_when_service_registry_changes(monkeypatch):
    original_services = dict(service_launcher.SERVICES)
    service_launcher.SERVICES.clear()
    service_launcher.SERVICES.update(
        {
            "gateway": service_launcher.ServiceInfo(
                name="gateway",
                port=6000,
                module="gateway",
                pid_file="gateway.pid",
                log_file="gateway.log",
            ),
            "memory": service_launcher.ServiceInfo(
                name="memory",
                port=6001,
                module="memory",
                pid_file="memory.pid",
                log_file="memory.log",
            ),
        }
    )
    calls: list[str] = []

    monkeypatch.setattr(service_launcher, "register_plugin_services", lambda *, force: None)

    def stop_service(name: str, silent: bool = False) -> bool:
        calls.append(name)
        service_launcher.SERVICES.pop("memory", None)
        return True

    monkeypatch.setattr(service_launcher, "stop_service", stop_service)

    try:
        service_launcher.stop_all(force=True)
    finally:
        service_launcher.SERVICES.clear()
        service_launcher.SERVICES.update(original_services)

    assert calls == ["gateway", "memory"]
