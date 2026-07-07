from __future__ import annotations

from typing import Any

from VoidCube_cli.autonomous_executor import AutonomousExecutorRuntime


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
        runtime = AutonomousExecutorRuntime(
            host,
            push_cli_agent_scene=push_cli_agent_scene,
            git_head_commit=git_head_commit,
            git_improvement_diff=git_improvement_diff,
            cprint=cprint,
        )
        host._autonomous_executor_runtime_instance = runtime
    return runtime
