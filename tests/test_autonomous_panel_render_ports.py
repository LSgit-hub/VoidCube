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
