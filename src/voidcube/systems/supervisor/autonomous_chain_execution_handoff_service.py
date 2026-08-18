"""Execution handoff boundary for approved autonomous-chain tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from .autonomous_chain_store import AutonomousChainTask
from .task_profile_policy import TaskProfilePolicy


class AutonomousChainExecutionHandoffService:
    """Run approved requests and own execution-lane status writeback."""

    _ERROR_STATUSES = frozenset({"error", "failed", "timeout", "unreachable"})
    _MEMORY_ASYNC_SUCCESS_STATUSES = frozenset({"accepted", "in_progress"})
    _SUCCESS_STATUSES = frozenset(
        {
            "ok",
            "success",
            "executed",
            "completed",
            "complete",
            "compressed",
            "already_compressed",
            "learn_only_completed",
            "autonomous_chain_execution_executed",
            "autonomous_chain_execution_recorded",
        }
    )

    def __init__(
        self,
        *,
        task_state: Any,
        task_store: Any,
        task_profile_policy: TaskProfilePolicy,
        execution_facade_provider: Callable[[], Any],
        propose_memory_promotion: Callable[
            [AutonomousChainTask], Awaitable[Optional[Dict[str, Any]]]
        ],
        task_activity_metadata: Callable[[AutonomousChainTask], Dict[str, Any]],
        touch_gateway_activity: Callable[..., Awaitable[Any]],
        record_ui_activity: Callable[..., Any],
    ) -> None:
        self._task_state = task_state
        self._task_store = task_store
        self._task_profile_policy = task_profile_policy
        self._execution_facade_provider = execution_facade_provider
        self._propose_memory_promotion = propose_memory_promotion
        self._task_activity_metadata = task_activity_metadata
        self._touch_gateway_activity = touch_gateway_activity
        self._record_ui_activity = record_ui_activity

    async def handoff(
        self,
        task: AutonomousChainTask,
        *,
        max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        execution_request = task.execution_request
        if execution_request is None or task.status == "running":
            return None

        await self._propose_memory_promotion(task)
        if task.status == "retry":
            task = self._task_state.update_status(
                task.task_id,
                status="approved",
                actor="supervisor",
                reason="Retry was explicitly rescheduled for execution handoff",
                event_type="execution_handoff_retry_approved",
            )
        self._task_state.update_status(
            task.task_id,
            status="running",
            actor="supervisor",
            reason="自主交接已开始",
            event_type="execution_handoff_started",
        )
        self._task_state.update_metadata(
            task.task_id,
            metadata={"executed_at": datetime.now(timezone.utc).isoformat()},
            execution_request=execution_request,
        )

        payload = execution_request.model_dump(mode="json")
        try:
            result = await self._execution_facade_provider().execute_autonomous_chain_request(
                payload
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {
                "status": "execution_handoff_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self._record_failure(
                task,
                result=result,
                result_status=result["status"],
                max_retries=max_retries,
            )
            return result

        nested_result = dict(result.get("result") or {}) if isinstance(result, dict) else {}
        result_status = nested_result.get("status") or (
            result.get("status") if isinstance(result, dict) else None
        )
        normalized_status = str(result_status).strip().lower() if result_status is not None else ""
        governance_type = self._task_profile_policy.governance_type(task)
        is_success = normalized_status in self._SUCCESS_STATUSES or (
            governance_type == "memory_maintenance"
            and normalized_status in self._MEMORY_ASYNC_SUCCESS_STATUSES
        )
        is_failure = (
            normalized_status in self._ERROR_STATUSES
            or not is_success
        )
        if normalized_status == "upgrade_awaiting_user_consent":
            self._task_state.update_metadata(
                task.task_id,
                metadata={
                    "execution_result": result,
                    "awaiting_user_consent_since": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )
            self._task_state.update_status(
                task.task_id,
                status="awaiting_user_consent",
                actor="supervisor_executor",
                reason="Body candidate passed probe and Governor review; waiting for explicit user consent.",
                event_type="execution_awaiting_user_consent",
            )
            return result
        if is_failure:
            self._record_failure(
                task,
                result=result,
                result_status=result_status,
                max_retries=max_retries,
            )
            return result

        if governance_type == "memory_maintenance":
            actor = "supervisor_memory_service"
            if normalized_status == "accepted":
                completion_reason = (
                    "Memory Service accepted the maintenance request; execution continues "
                    "inside Memory Service."
                )
            elif normalized_status == "in_progress":
                completion_reason = (
                    "Memory Service is already processing maintenance; no duplicate run was started."
                )
            else:
                completion_reason = (
                    "Memory-maintenance handoff completed "
                    f"(executor_status={str(result_status)[:60] if result_status else 'ok'})."
                )
        elif governance_type == "self_evolution":
            actor = "supervisor_executor"
            completion_reason = (
                "Autonomous-chain task completed by the supervisor's body executor. "
                f"executor_status={str(result_status)[:60] if result_status else 'ok'}"
            )
        else:
            actor = "supervisor"
            completion_reason = f"自主交接已完成，执行结果：{str(result_status)[:100]}"
        self._task_state.update_status(
            task.task_id,
            status="completed",
            actor=actor,
            reason=completion_reason,
            event_type="execution_handoff_completed",
        )
        self._task_state.update_metadata(
            task.task_id,
            metadata={"execution_result": result},
        )
        await self._touch_gateway_activity(
            "autonomous_chain_execute",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
            },
        )
        self._record_ui_activity(
            "execution_handoff_started",
            scene="handoff",
            summary=f"已把「{task.title}」交接给执行面处理。",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
                "result_status": result_status,
            },
        )
        return result

    def _record_failure(
        self,
        task: AutonomousChainTask,
        *,
        result: Dict[str, Any],
        result_status: Any,
        max_retries: int,
    ) -> None:
        current = self._task_store.get_task(task.task_id) or task
        failure_count = int(dict(current.metadata or {}).get("execution_failure_count") or 0) + 1
        governance_type = self._task_profile_policy.governance_type(current)
        actor = (
            "supervisor_memory_service"
            if governance_type == "memory_maintenance"
            else "supervisor_executor"
        )
        terminal = failure_count >= max_retries
        self._task_state.update_status(
            task.task_id,
            status="failed" if terminal else "retry",
            actor=actor,
            reason=(
                f"Execution handoff failed after {failure_count}/{max_retries} attempt(s); "
                f"executor_status={str(result_status)[:80] or 'unknown'}."
            ),
            event_type="execution_handoff_failed" if terminal else "execution_handoff_retry",
        )
        self._task_state.update_metadata(
            task.task_id,
            metadata={
                "execution_failed": True,
                "execution_failure_count": failure_count,
                "execution_result": dict(result),
            },
        )


__all__ = ["AutonomousChainExecutionHandoffService"]
