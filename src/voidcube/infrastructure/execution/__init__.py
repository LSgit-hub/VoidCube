"""Execution infrastructure adapters.

The package owns task-scoped execution contracts, terminal dispatch, and the
concrete local/container/remote environment backends.  Legacy ``tools``
imports are compatibility aliases only.
"""

from .task_execution import (
    TaskExecutionBlocked,
    TaskExecutionContract,
    TaskExecutionState,
    begin_task_execution,
    block_task_execution,
    clear_task_execution_state,
    configure_task_execution,
    ensure_task_execution_path,
    ensure_task_execution_request,
    get_task_execution_contract,
    get_task_execution_state,
    mark_task_execution_ready,
    release_task_execution,
)
from .process_registry import ProcessRegistry, process_registry

__all__ = [
    "TaskExecutionBlocked",
    "TaskExecutionContract",
    "TaskExecutionState",
    "begin_task_execution",
    "block_task_execution",
    "clear_task_execution_state",
    "configure_task_execution",
    "ensure_task_execution_path",
    "ensure_task_execution_request",
    "get_task_execution_contract",
    "get_task_execution_state",
    "mark_task_execution_ready",
    "release_task_execution",
    "ProcessRegistry",
    "process_registry",
]
