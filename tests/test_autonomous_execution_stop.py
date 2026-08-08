from VoidCube_app.autonomous_execution_runtime import (
    AutonomousExecutionStopPorts,
    stop_autonomous_execution,
)


def test_stop_execution_deactivates_then_interrupts_and_signals() -> None:
    calls: list[str] = []
    ports = AutonomousExecutionStopPorts(
        deactivate_execution_host=lambda: calls.append("deactivate") or True,
        interrupt_running_agent=lambda: calls.append("agent"),
        interrupt_current_task=lambda: calls.append("task"),
        signal_stop=lambda: calls.append("signal"),
    )

    stop_autonomous_execution(ports, interrupt=True)

    assert calls == ["deactivate", "agent", "task", "signal"]


def test_stop_execution_without_host_only_signals_loop() -> None:
    calls: list[str] = []
    ports = AutonomousExecutionStopPorts(
        deactivate_execution_host=lambda: calls.append("deactivate") or False,
        interrupt_running_agent=lambda: calls.append("agent"),
        interrupt_current_task=lambda: calls.append("task"),
        signal_stop=lambda: calls.append("signal"),
    )

    stop_autonomous_execution(ports, interrupt=True)

    assert calls == ["deactivate", "signal"]
