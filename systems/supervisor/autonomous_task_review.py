"""Pure autonomous-chain task review policy.

The policy decides whether a task may move from API-B judgement into the
handoff lane. It does not read or write stores, call Gateway/Execution, or
inspect Supervisor state. Those effects remain with the runtime coordinator.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from systems.supervisor.autonomous_chain_store import AutonomousChainTask
from systems.supervisor.task_profile_policy import TaskProfilePolicy


_ACTIVE_SELF_LEARNING_STATUSES = frozenset({"planned", "approved", "running"})


def normalize_autonomous_chain_decision(
    decision: Optional[str],
) -> Optional[str]:
    if decision is None:
        return None
    normalized = str(decision).strip().lower()
    return {
        "planned": "planned",
        "approve": "approved",
        "approved": "approved",
        "defer": "deferred",
        "deferred": "deferred",
        "fail": "failed",
        "failed": "failed",
        "pause": "paused",
        "paused": "paused",
        "cancel": "cancelled",
        "cancelled": "cancelled",
        "run": "running",
        "running": "running",
        "complete": "completed",
        "completed": "completed",
        "auto": "auto",
    }.get(normalized)


def is_agent_pull_task(
    task: AutonomousChainTask,
    *,
    task_profile_policy: TaskProfilePolicy,
) -> bool:
    execution_kind = task_profile_policy.execution_kind(task)
    return (
        task_profile_policy.governance_type(task) == "self_learning"
        or execution_kind == "body_improvement"
    )


def has_pending_self_learning_prerequisite(
    tasks: Iterable[AutonomousChainTask],
    *,
    task_profile_policy: TaskProfilePolicy,
    body_task: Optional[AutonomousChainTask] = None,
) -> bool:
    backlog_self_learning_pending = False
    for task in tasks:
        if task_profile_policy.governance_type(task) != "self_learning":
            continue
        if task.status not in _ACTIVE_SELF_LEARNING_STATUSES:
            continue
        if task.status == "running":
            return True
        backlog_self_learning_pending = True
    if not backlog_self_learning_pending:
        return False
    if body_task is None:
        return True
    prior_self_learning_deferrals = sum(
        1
        for decision in body_task.decision_history
        if str(decision.status) == "deferred"
        and "self-learning tasks awaiting completion" in str(decision.reason)
    )
    return prior_self_learning_deferrals == 0


def calculate_learning_quality_score(
    tasks: Iterable[AutonomousChainTask],
    *,
    task_profile_policy: TaskProfilePolicy,
    now: datetime,
) -> float:
    completed_count = 0
    quality_sum = 0.0
    freshness_sum = 0.0
    current_time = now

    for task in tasks:
        if task_profile_policy.runtime_family(task) != "self_learning":
            continue
        completed_count += 1

        raw_quality = task.metadata.get("quality_score")
        try:
            task_quality = float(raw_quality)
        except (TypeError, ValueError):
            return 0.0
        if not 0.0 <= task_quality <= 1.0:
            return 0.0
        quality_sum += task_quality

        completed_at = task.metadata.get("completed_at")
        if completed_at:
            try:
                timestamp = datetime.fromisoformat(str(completed_at))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                age_days = (current_time - timestamp).days
                freshness_sum += max(0.0, 1.0 - age_days / 90.0)
            except Exception:
                freshness_sum += 0.5

    if completed_count == 0:
        return 0.0

    avg_quality = quality_sum / completed_count
    avg_freshness = freshness_sum / completed_count
    return max(0.0, min(100.0, avg_quality * 60 + avg_freshness * 40))


def body_improvement_learning_quality_score(
    task: AutonomousChainTask,
    *,
    learning_tasks: Iterable[AutonomousChainTask],
    task_profile_policy: TaskProfilePolicy,
    now: datetime,
) -> float:
    evidence = dict(task.evidence or {})
    metadata = dict(task.metadata or {})
    for candidate in (
        evidence.get("learning_quality_score"),
        metadata.get("learning_quality_score"),
        metadata.get("quality_score"),
    ):
        if candidate is None:
            continue
        try:
            score = float(candidate)
        except (TypeError, ValueError):
            continue
        if 0.0 < score <= 1.0:
            score *= 100.0
        return max(0.0, min(100.0, score))
    return calculate_learning_quality_score(
        learning_tasks,
        task_profile_policy=task_profile_policy,
        now=now,
    )


def build_autonomous_chain_auto_decision(
    *,
    task: AutonomousChainTask,
    drive_input: Optional[Dict[str, Any]],
    autonomous_chain_gate_active: bool,
    task_profile_policy: TaskProfilePolicy,
    active_tasks: Iterable[AutonomousChainTask],
    learning_history: Optional[Iterable[AutonomousChainTask]],
    now: datetime,
    body_improvement_min_quality: float,
) -> tuple[str, str]:
    drive_input = dict(drive_input or {})
    task_type = task_profile_policy.governance_type(task)
    task_family = task_profile_policy.runtime_family(task)
    active_task_list = list(active_tasks)
    learning_history_list = list(
        active_task_list if learning_history is None else learning_history
    )

    if is_agent_pull_task(task, task_profile_policy=task_profile_policy):
        execution_kind = task_profile_policy.execution_kind(task)
        if execution_kind == "body_improvement":
            if has_pending_self_learning_prerequisite(
                active_task_list,
                task_profile_policy=task_profile_policy,
                body_task=task,
            ):
                return (
                    "deferred",
                    "Body-improvement task deferred because there are still planned/approved/running self-learning tasks awaiting completion. Supervisor must let learning evidence settle before code-improvement execution is released.",
                )
            learning_quality_score = body_improvement_learning_quality_score(
                task,
                learning_tasks=learning_history_list,
                task_profile_policy=task_profile_policy,
                now=now,
            )
            min_quality = float(body_improvement_min_quality or 60.0)
            if learning_quality_score < min_quality:
                return (
                    "cancelled",
                    (
                        "Body-improvement task cancelled because learning evidence is insufficient; "
                        f"current score {learning_quality_score:.2f} is below required {min_quality:.2f}."
                    ),
                )
            return (
                "approved",
                "Agent-pull body-improvement task transferred by API-B for API-A autonomous execution. Autonomous-chain baseline keeps this path pull -> execute -> write back.",
            )
        return (
            "approved",
            "Agent-pull self-learning task transferred by API-B for API-A autonomous execution. Autonomous-chain baseline keeps this path pull -> execute -> write back.",
        )

    # The autonomous-chain gate bypasses user-chain quiet signals only for
    # task families that are explicitly allowed to run autonomously.
    if autonomous_chain_gate_active:
        if task_type == "self_learning":
            return (
                "approved",
                "Autonomous-chain gate active: self-learning task transferred without waiting for user-chain quiet signals. Learn-only constraints still apply.",
            )
        if task_type == "memory_maintenance":
            return (
                "approved",
                "Autonomous-chain gate active: memory-maintenance task transferred without waiting for user-chain quiet signals.",
            )

    decision = (
        drive_input.get("task_family_decisions", {}).get(task_family)
        or drive_input.get("governance_task_type_decisions", {}).get(task_type)
        or drive_input["decisions"]
    )

    if decision["eligible_for_execution"]:
        if task_type == "self_learning":
            return (
                "approved",
                "该学习链路项已由 API-B 转交：当前没有冲突中的内部流程活动；用户链路只作为软感知信号，不构成自学习证据工作的硬门控。",
            )
        if task_type == "memory_maintenance":
            return (
                "approved",
                "该记忆维护链路项已由 API-B 转交：当前运行时与记忆并发护栏满足要求；用户链路仍只作为软感知信号。",
            )
        return (
            "approved",
            "该链路项已由 API-B 转交，将进入下一轮自主交接；当前运行时并发护栏满足要求。",
        )
    if task_type == "self_learning":
        return (
            "deferred",
            "该学习链路项暂缓：当前已有内部流程或子系统在途工作；这次延后来自并发护栏，而不是用户空闲门控。",
        )
    if task_type == "memory_maintenance":
        return (
            "deferred",
            "该记忆维护链路项暂缓：当前仍有运行时或记忆侧工作在途；用户链路仍只作为软感知信号，并非这里的执行门。",
        )
    return (
        "deferred",
        "该链路项暂缓：当前运行时并发护栏尚未满足；任务继续留在 API-B 判断在途中等待后续复核。",
    )


__all__ = [
    "body_improvement_learning_quality_score",
    "build_autonomous_chain_auto_decision",
    "calculate_learning_quality_score",
    "has_pending_self_learning_prerequisite",
    "is_agent_pull_task",
    "normalize_autonomous_chain_decision",
]
