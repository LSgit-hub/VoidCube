from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import CancellationToken
from VoidCube_cli.app import VoidcubeCLI
from VoidCube_cli.autonomous_execution_host import AutonomousExecutionHost


def _host(*, submit=lambda _host, _payload: True) -> AutonomousExecutionHost:
    scheduler = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(autonomous_gate=True),
    )
    runtime = SimpleNamespace(
        scheduler=scheduler,
        enable_autonomous=lambda: None,
        submit_autonomous=submit,
    )
    return AutonomousExecutionHost(
        session_id="auto-session",
        session_start=datetime(2026, 8, 8),
        model="model",
        provider="provider",
        session_db=None,
        scheduler_runtime=runtime,
        execute_turn=lambda owner, request, token: (owner, request, token),
        invalidate=lambda: None,
        tool_event_sink=lambda _owner, _event: None,
        panel_event_ports=lambda: SimpleNamespace(),
    )


def test_autonomous_execution_host_is_not_a_tui_host() -> None:
    host = _host()

    assert not isinstance(host, VoidcubeCLI)
    assert host._should_emit_scrollback_output() is False
    for name in (
        "run",
        "chat",
        "_tui_prompt_runtime",
        "_enter_keybinding_runtime",
        "_voice_keybinding_runtime",
    ):
        assert not hasattr(host, name)


def test_autonomous_execution_host_submits_only_supervisor_lane() -> None:
    calls = []
    host = _host(submit=lambda owner, payload: calls.append((owner, payload)) or True)

    assert host._execute_pending_input("research") is True
    assert calls == [(host, ("research", None))]


def test_autonomous_execution_host_delegates_admitted_turn_execution() -> None:
    host = _host()
    request = TurnRequest(
        request_id="auto-1",
        lane=TurnLane.SUPERVISOR_TASK,
        session_id=host.session_id,
        prompt=("research", None),
    )
    token = CancellationToken()

    assert host._execute_agent_turn_request(request, token) == (host, request, token)


def test_autonomous_execution_host_owns_history_independently() -> None:
    parent_history = [{"role": "user", "content": "parent"}]
    host = _host()

    host.conversation_history = [
        {"role": "user", "content": "autonomous"},
        {"role": "assistant", "content": "result"},
    ]

    assert parent_history == [{"role": "user", "content": "parent"}]
    assert host.conversation_history[-1]["content"] == "result"


def test_cli_creates_and_reuses_narrow_autonomous_owner(monkeypatch) -> None:
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_execution_host = None
    cli._session_db = None
    cli.session_id = "user-session"
    cli.session_start = datetime(2026, 8, 8)
    cli.model = "model"
    cli.provider = "provider"
    cli._invalidate = lambda *args, **kwargs: None
    cli._scheduler_runtime = lambda: SimpleNamespace()
    monkeypatch.setattr(
        "VoidCube_cli.app._ensure_supervisor_task_session_view",
        lambda _host, **_kwargs: None,
    )

    owner = cli._ensure_autonomous_execution_host()

    assert isinstance(owner, AutonomousExecutionHost)
    assert owner.session_id != cli.session_id
    assert cli._ensure_autonomous_execution_host() is owner
