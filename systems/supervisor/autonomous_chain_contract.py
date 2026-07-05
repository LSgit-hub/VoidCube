from __future__ import annotations

AUTONOMOUS_CHAIN_TASKS_ROUTE = "/autonomous-chain/tasks"
AUTONOMOUS_CHAIN_TASK_REVIEW_ROUTE = f"{AUTONOMOUS_CHAIN_TASKS_ROUTE}/review"
AUTONOMOUS_CHAIN_TASK_CLEAR_ROUTE = f"{AUTONOMOUS_CHAIN_TASKS_ROUTE}/clear"
AUTONOMOUS_CHAIN_CYCLE_ROUTE = "/autonomous-chain/cycle"


def autonomous_chain_task_route(task_id: str) -> str:
    return f"{AUTONOMOUS_CHAIN_TASKS_ROUTE}/{task_id}"


def autonomous_chain_task_decision_route(task_id: str) -> str:
    return f"{autonomous_chain_task_route(task_id)}/decision"
