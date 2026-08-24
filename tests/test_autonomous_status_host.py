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


def test_failed_supervisor_refresh_keeps_last_valid_snapshot(monkeypatch):
    class _FailedThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(status_host.threading, "Thread", _FailedThread)
    monkeypatch.setattr(
        status_host.urllib.request,
        "urlopen",
        Mock(side_effect=OSError("supervisor unavailable")),
    )
    host = SimpleNamespace(
        _supervisor_state_cache={"scene": "planning", "mem_usage": {"request_count": 1}},
        _supervisor_state_ts=0.0,
        _supervisor_state_refreshing=False,
        _supervisor_url="http://127.0.0.1:6002/ui/state",
    )

    status_host.refresh_supervisor_status(host)

    assert status_host.fetch_supervisor_status(host) == {
        "scene": "planning",
        "mem_usage": {"request_count": 1},
    }


def test_supervisor_refresh_accepts_slow_authoritative_ui_snapshot(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"scene":"maintenance","mem_usage":{}}'

    calls = []
    monkeypatch.setattr(
        status_host.urllib.request,
        "urlopen",
        lambda _request, *, timeout: calls.append(timeout) or _Response(),
    )
    host = SimpleNamespace(
        _supervisor_state_cache={},
        _supervisor_state_ts=0.0,
        _supervisor_state_refreshing=False,
        _supervisor_url="http://127.0.0.1:6002/ui/state",
        _autonomous_activation_pending=False,
        _autonomous_gate_active=False,
        _autonomous_panel_event_ports=lambda: SimpleNamespace(),
    )

    thread = Mock()
    thread.start.side_effect = lambda: thread._target()

    def _thread_factory(*, target, **_kwargs):
        thread._target = target
        return thread

    monkeypatch.setattr(status_host.threading, "Thread", _thread_factory)
    monkeypatch.setattr(
        status_host,
        "sync_autonomous_supervisor_event",
        lambda *_args, **_kwargs: None,
    )

    status_host.refresh_supervisor_status(host)

    assert calls == [15]
    assert status_host.fetch_supervisor_status(host)["scene"] == "maintenance"
