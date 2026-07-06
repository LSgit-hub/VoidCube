from __future__ import annotations

from typing import Any, Dict

from VoidCube_cli.autonomous_executor import AutonomousExecutorRuntime


def autonomous_executor_runtime(
    host: Any,
    *,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> AutonomousExecutorRuntime:
    runtime = getattr(host, "_autonomous_executor_runtime_instance", None)
    if runtime is None:
        runtime = AutonomousExecutorRuntime(
            host,
            push_cli_agent_scene=push_cli_agent_scene,
            git_improvement_diff=git_improvement_diff,
            cprint=cprint,
        )
        host._autonomous_executor_runtime_instance = runtime
    return runtime


def find_owned_running_autonomous_task(
    host: Any,
    *,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> Dict[str, Any] | None:
    return autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).find_owned_running_task()


def enqueue_autonomous_task_prompt(
    host: Any,
    task: Dict[str, Any],
    execution_kind: str,
    *,
    recovered: bool = False,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> bool:
    return autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).enqueue_task_prompt(
        task,
        execution_kind,
        recovered=recovered,
    )


def clear_current_autonomous_task_state(
    host: Any,
    *,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> None:
    autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).clear_current_task_state()


def report_current_autonomous_task_timeout_if_needed(
    host: Any,
    *,
    gateway_base: str = "http://127.0.0.1:6000",
    timeout: float = 15,
    now: float | None = None,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> bool:
    return autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).report_current_task_timeout_if_needed(
        gateway_base=gateway_base,
        timeout=timeout,
        now=now,
    )


def post_autonomous_task_decision(
    host: Any,
    task_id: str,
    *,
    decision: str,
    reason: str,
    context: Dict[str, Any] | None = None,
    final_response: str = "",
    timeout: float = 15,
    gateway_base: str = "http://127.0.0.1:6000",
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> bool:
    return autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).post_task_decision(
        task_id,
        decision=decision,
        reason=reason,
        context=context,
        final_response=final_response,
        timeout=timeout,
        gateway_base=gateway_base,
    )


def interrupt_current_autonomous_task(
    host: Any,
    *,
    reason: str,
    source: str,
    timeout: float = 5,
    gateway_base: str = "http://127.0.0.1:6000",
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> bool:
    return autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).interrupt_current_task(
        reason=reason,
        source=source,
        timeout=timeout,
        gateway_base=gateway_base,
    )


def poll_autonomous_workflow(
    host: Any,
    *,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> None:
    autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).poll_workflow()


def submit_body_improvement_report(
    host: Any,
    task: Dict[str, Any],
    task_id: str,
    gateway_base: str,
    *,
    improvement_description: str,
    push_cli_agent_scene: Any,
    git_improvement_diff: Any,
    cprint: Any,
) -> None:
    autonomous_executor_runtime(
        host,
        push_cli_agent_scene=push_cli_agent_scene,
        git_improvement_diff=git_improvement_diff,
        cprint=cprint,
    ).submit_body_improvement_report(
        task,
        task_id,
        gateway_base,
        improvement_description=improvement_description,
    )
