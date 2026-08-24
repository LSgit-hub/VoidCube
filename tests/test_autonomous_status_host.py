from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import voidcube.interfaces.cli.autonomous.status_host as status_host
from voidcube.interfaces.cli.autonomous.status_host import (
    _sync_local_gate_with_supervisor,
)


def test_supervisor_restart_clears_stale_local_auto_gate():
    stop = Mock()
    host = SimpleNamespace(
        _autonomous_gate_active=True,
        _autonomous_activation_pending=False,
        _stop_autonomous_execution=stop,
    )

    _sync_local_gate_with_supervisor(
        host,
        {"stellar_mode": {"mode": "daily_companion"}},
    )

    assert host._autonomous_gate_active is False
    stop.assert_called_once_with(interrupt=False)


def test_supervisor_auto_mode_does_not_clear_local_gate():
    stop = Mock()
    host = SimpleNamespace(
        _autonomous_gate_active=True,
        _autonomous_activation_pending=False,
        _stop_autonomous_execution=stop,
    )

    _sync_local_gate_with_supervisor(
        host,
        {"stellar_mode": {"mode": "auto_evolution"}},
    )

    assert host._autonomous_gate_active is True
    stop.assert_not_called()


def test_supervisor_auto_mode_restores_local_scheduler_after_cli_restart():
    start = Mock()
    host = SimpleNamespace(
        _autonomous_gate_active=False,
        _autonomous_activation_pending=False,
        _start_autonomous_execution=start,
    )

    _sync_local_gate_with_supervisor(
        host,
        {"stellar_mode": {"mode": "auto_evolution"}},
    )

    assert host._autonomous_gate_active is True
    start.assert_called_once_with()


def test_refresh_supervisor_status_starts_background_fetch(monkeypatch):
    thread = Mock()
    monkeypatch.setattr(status_host.threading, "Thread", Mock(return_value=thread))
    host = SimpleNamespace(
        _supervisor_state_ts=0.0,
        _supervisor_state_refreshing=False,
    )

    status_host.refresh_supervisor_status(host)

    status_host.threading.Thread.assert_called_once()
    thread.start.assert_called_once_with()
