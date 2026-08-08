from __future__ import annotations

from VoidCube_cli.cli_idle_maintenance_runtime import (
    CliIdleMaintenancePorts,
    CliIdleMaintenanceRuntime,
)


def test_idle_maintenance_skips_all_work_while_agent_is_running() -> None:
    calls: list[str] = []
    runtime = CliIdleMaintenanceRuntime(
        CliIdleMaintenancePorts(
            agent_running=lambda: True,
            check_config_changes=lambda: calls.append("config"),
            refresh_observation_surfaces=lambda: calls.append("observation"),
            autonomous_gate_active=lambda: calls.append("gate") or True,
            start_autonomous_execution=lambda: calls.append("start"),
            application_ready=lambda: True,
            invalidate=lambda _interval: calls.append("invalidate"),
            enqueue_pending_input=lambda _value: calls.append("enqueue"),
        )
    )

    runtime.run_once()

    assert calls == []


def test_idle_maintenance_runs_ordered_ports_and_starts_autonomous_execution() -> None:
    calls: list[str] = []
    runtime = CliIdleMaintenanceRuntime(
        CliIdleMaintenancePorts(
            agent_running=lambda: False,
            check_config_changes=lambda: calls.append("config"),
            refresh_observation_surfaces=lambda: calls.append("observation"),
            autonomous_gate_active=lambda: calls.append("gate") or True,
            start_autonomous_execution=lambda: calls.append("start"),
            application_ready=lambda: True,
            invalidate=lambda interval: calls.append(f"invalidate:{interval}"),
            enqueue_pending_input=lambda _value: calls.append("enqueue"),
        )
    )

    runtime.run_once()

    assert calls[:5] == ["config", "observation", "gate", "start", "invalidate:0.5"]
