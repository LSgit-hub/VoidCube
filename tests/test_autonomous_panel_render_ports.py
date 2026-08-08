from types import SimpleNamespace

import VoidCube_cli.autonomous_panel as panel_module
from VoidCube_cli.autonomous_events import (
    AutonomousPanelEventPorts,
    append_autonomous_execution_event,
    sync_autonomous_supervisor_event,
)
from VoidCube_cli.autonomous_panel import (
    AutonomousPanelRenderPorts,
    AutonomousPanelStatePorts,
)


def test_panel_fragments_use_explicit_render_ports(monkeypatch):
    captured = {}

    def fake_rows(_host, *, state_ports, render_ports):
        captured["state_ports"] = state_ports
        captured["ports"] = render_ports
        return [("class:auto-panel-text", "row")]

    monkeypatch.setattr(panel_module, "build_autonomous_execution_panel_rows", fake_rows)
    ports = AutonomousPanelRenderPorts(
        terminal_width=lambda: 40,
        trim_status_bar_text=lambda text, _width: text,
        pad_status_bar_text=lambda text, width: text.ljust(width, "."),
    )
    state_ports = AutonomousPanelStatePorts(
        gate_active=lambda: True,
        session_id=lambda: "test-session",
        current_task=lambda: None,
        current_task_started_at=lambda: 0.0,
        agent_running=lambda: False,
        last_agent_turn_result=lambda: None,
        pending_input_nonempty=lambda: False,
        execution_events=lambda: [],
        spinner_text=lambda: "",
    )

    result = panel_module.get_autonomous_execution_panel_fragments(
        SimpleNamespace(),
        state_ports=state_ports,
        render_ports=ports,
    )

    assert captured["ports"] is ports
    assert captured["state_ports"] is not None
    assert any(text.startswith("row") and len(text) == 36 for _, text in result)


def test_panel_events_use_explicit_event_ports():
    events = []
    last_key = [""]
    ports = AutonomousPanelEventPorts(
        gate_active=lambda: True,
        execution_events=lambda: list(events),
        set_execution_events=lambda value: events.__setitem__(slice(None), value),
        trim_status_bar_text=lambda text, _width: text,
        last_supervisor_event_key=lambda: last_key[0],
        set_last_supervisor_event_key=lambda value: last_key.__setitem__(0, value),
    )

    append_autonomous_execution_event(
        event_ports=ports,
        message="已接管任务",
        tone="success",
        stage="claim",
    )
    sync_autonomous_supervisor_event(
        {
            "timeline": [
                {
                    "created_at": "2026-08-03T10:00:00",
                    "event_type": "task_decided",
                    "summary": "已通过监督者裁决",
                }
            ]
        },
        event_ports=ports,
    )

    assert [event["stage"] for event in events] == ["claim", "supervisor"]
    assert last_key[0].endswith("已通过监督者裁决")


def test_panel_projects_scheduler_cancellation_and_request_id():
    snapshot = SimpleNamespace(
        active=SimpleNamespace(
            lane=SimpleNamespace(value="supervisor_task"),
            state=SimpleNamespace(value="cancelling"),
            request_id="auto-123456789",
        ),
        queued=(),
        autonomous_gate=True,
        blocked_reason="",
    )
    rows = panel_module._append_scheduler_rows
    output: list[tuple[str, str]] = []

    rows(
        output,
        snapshot,
        60,
        trim_status_bar_text=lambda text, _width: text,
        events=[
            {
                "kind": "cancel_requested",
                "request_id": "auto-123456789",
                "reason": "auto-q",
            }
        ],
    )

    rendered = "\n".join(text for _, text in output)
    assert "自主取消中" in rendered
    assert "#23456789" in rendered
    assert "cancel_requested" in rendered


def test_panel_fits_a_narrow_terminal_without_forcing_a_minimum_width(monkeypatch):
    monkeypatch.setattr(
        panel_module,
        "build_autonomous_execution_panel_rows",
        lambda _host, **_kwargs: [("class:auto-panel-text", "这是一个很长的自主执行状态")],
    )
    ports = AutonomousPanelRenderPorts(
        terminal_width=lambda: 20,
        trim_status_bar_text=lambda text, width: text[: max(0, width - 3)] + "..."
        if len(text) > width
        else text,
        pad_status_bar_text=lambda text, width: text[:width].ljust(width),
    )
    fragments = panel_module.get_autonomous_execution_panel_fragments(
        SimpleNamespace(),
        state_ports=AutonomousPanelStatePorts(
            gate_active=lambda: True,
            session_id=lambda: "session",
            current_task=lambda: None,
            current_task_started_at=lambda: 0.0,
            agent_running=lambda: False,
            last_agent_turn_result=lambda: None,
            pending_input_nonempty=lambda: False,
            execution_events=lambda: [],
            spinner_text=lambda: "",
        ),
        render_ports=ports,
    )

    for _style, text in fragments:
        for line in text.splitlines():
            assert len(line) <= 20
