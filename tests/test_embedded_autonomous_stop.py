from VoidCube_cli.embedded_autonomous_stop import (
    EmbeddedAutonomousStopPorts,
    stop_embedded_autonomous_component,
)


def test_stop_component_deactivates_then_interrupts_and_signals() -> None:
    calls: list[str] = []
    ports = EmbeddedAutonomousStopPorts(
        deactivate_component_host=lambda: calls.append("deactivate") or True,
        interrupt_running_agent=lambda: calls.append("agent"),
        interrupt_current_task=lambda: calls.append("task"),
        signal_stop=lambda: calls.append("signal"),
    )

    stop_embedded_autonomous_component(ports, interrupt=True)

    assert calls == ["deactivate", "agent", "task", "signal"]


def test_stop_component_without_child_only_signals_loop() -> None:
    calls: list[str] = []
    ports = EmbeddedAutonomousStopPorts(
        deactivate_component_host=lambda: calls.append("deactivate") or False,
        interrupt_running_agent=lambda: calls.append("agent"),
        interrupt_current_task=lambda: calls.append("task"),
        signal_stop=lambda: calls.append("signal"),
    )

    stop_embedded_autonomous_component(ports, interrupt=True)

    assert calls == ["deactivate", "signal"]
