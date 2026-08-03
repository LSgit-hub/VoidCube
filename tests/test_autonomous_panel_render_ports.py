from types import SimpleNamespace

import VoidCube_cli.autonomous_panel as panel_module
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
