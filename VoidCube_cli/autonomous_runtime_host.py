from __future__ import annotations

from typing import Any

from VoidCube_cli.autonomous_events import append_autonomous_execution_event
from VoidCube_cli.autonomous_executor import (
    AutonomousExecutorPorts,
    AutonomousExecutorRuntime,
)


def autonomous_executor_runtime(
    host: Any,
    *,
    push_cli_agent_scene: Any,
    git_head_commit: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> AutonomousExecutorRuntime:
    runtime = getattr(host, "_autonomous_executor_runtime_instance", None)
    if runtime is None:
        ports = AutonomousExecutorPorts(
            get_session_id=lambda: str(getattr(host, "session_id", "") or ""),
            get_current_task=lambda: getattr(host, "_current_autonomous_task", None),
            set_current_task=lambda task: setattr(host, "_current_autonomous_task", task),
            get_current_task_started_at=lambda: float(
                getattr(host, "_current_autonomous_task_started_at", 0.0) or 0.0
            ),
            set_current_task_started_at=lambda value: setattr(
                host, "_current_autonomous_task_started_at", float(value)
            ),
            set_current_task_run_id=lambda value: setattr(
                host, "_current_autonomous_task_run_id", str(value or "")
            ),
            get_last_agent_turn_result=lambda: getattr(
                host, "_last_agent_turn_result", None
            ),
            set_last_agent_turn_result=lambda value: setattr(
                host, "_last_agent_turn_result", value
            ),
            enqueue_pending_input=lambda prompt: host._pending_input.put(prompt),
            agent_running=lambda: bool(getattr(host, "_agent_running", False)),
            autonomous_gate_active=lambda: bool(
                getattr(host, "_autonomous_gate_active", False)
            ),
            append_execution_event=lambda message, *, tone="info", stage="": append_autonomous_execution_event(
                host,
                message,
                tone=tone,
                stage=stage,
            ),
        )
        runtime = AutonomousExecutorRuntime(
            ports,
            push_cli_agent_scene=push_cli_agent_scene,
            git_head_commit=git_head_commit,
            git_improvement_diff=git_improvement_diff,
            cprint=cprint,
        )
        host._autonomous_executor_runtime_instance = runtime
    return runtime
