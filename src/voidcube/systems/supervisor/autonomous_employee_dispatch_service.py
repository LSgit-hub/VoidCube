"""Dispatch API-B approved work to isolated employee agents."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from .autonomous_chain_store import AutonomousChainTask
from .autonomous_learning_quality import assess_autonomous_learning_quality
from .task_profile_policy import TaskProfilePolicy


class AutonomousEmployeeDispatchService:
    """Own the canonical API-B -> employee queue -> writeback lifecycle."""

    def __init__(
        self,
        *,
        task_state: Any,
        task_store: Any,
        scheduled_task_store: Any,
        task_profile_policy: TaskProfilePolicy,
        resolve_worker_role: Callable[[str], str],
        touch_gateway_activity: Callable[..., Any],
        record_ui_activity: Callable[..., Any],
    ) -> None:
        self._task_state = task_state
        self._task_store = task_store
        self._scheduled_task_store = scheduled_task_store
        self._task_profile_policy = task_profile_policy
        self._resolve_worker_role = resolve_worker_role
        self._touch_gateway_activity = touch_gateway_activity
        self._record_ui_activity = record_ui_activity

    def dispatch(self, task: AutonomousChainTask) -> Dict[str, Any]:
        """Create one idempotent employee assignment for an approved task."""
        existing = self._find_schedule(task.task_id)
        if existing is not None and str(existing.get("status") or "").lower() in {
            "failed",
            "cancelled",
            "completed",
        } and task.status in {"approved", "retry"}:
            existing = None
        if existing is not None:
            self._record_assignment(task, existing)
            return self._assignment_payload(existing, created=False)

        worker_role = self._resolve_worker_role(self._worker_role(task))
        schedule = self._scheduled_task_store.create(
            {
                "title": task.title,
                "instruction": self._instruction(task),
                "schedule_type": "once",
                "run_at": datetime.now(timezone.utc).isoformat(),
                "created_by": "api_b",
                "requested_via": "autonomous_worker",
                "worker_role": worker_role,
                "autonomous_task_id": task.task_id,
            }
        )
        self._record_assignment(task, schedule)
        self._record_ui_activity(
            "employee_task_dispatched",
            scene="handoff",
            summary=f"已将「{task.title}」派给{worker_role}员工。",
            metadata={
                "task_id": task.task_id,
                "employee_task_id": schedule.get("schedule_id"),
                "worker_role": worker_role,
            },
        )
        return self._assignment_payload(schedule, created=True)

    async def reconcile(self) -> list[Dict[str, Any]]:
        """Project employee queue and run outcomes back to autonomous tasks."""
        updates: list[Dict[str, Any]] = []
        runs_by_schedule = self._latest_runs_by_schedule()
        for task in self._task_store.list_employee_execution_lane_tasks():
            schedule = self._find_schedule(task.task_id)
            if task.status == "reconciling" and schedule is None:
                migrated = self._task_state.update_status(
                    task.task_id,
                    status="approved",
                    actor="employee_dispatch_migration",
                    reason="历史执行租约状态已迁移为员工代理派工状态。",
                    context={"migration": "employee_reconciling_to_employee_dispatch"},
                    event_type="employee_dispatch_migration",
                )
                updates.append({"task_id": migrated.task_id, "status": "approved"})
                continue
            if schedule is None:
                continue
            run = runs_by_schedule.get(str(schedule.get("schedule_id") or ""), {})
            run_status = str(run.get("status") or "").strip().lower()
            if run_status == "running" and task.status == "approved":
                updated = self._task_state.update_status(
                    task.task_id,
                    status="running",
                    actor="employee_dispatch",
                    reason="员工代理已认领 API-B 派发的任务。",
                    context=self._result_context(schedule, run),
                    event_type="employee_execution_started",
                )
                updates.append({"task_id": updated.task_id, "status": "running"})
                continue
            if run_status not in {"completed", "failed", "cancelled"}:
                continue

            success = run_status == "completed"
            result_summary = str(run.get("result_summary") or "").strip()
            result_context = self._result_context(schedule, run)
            metadata: Dict[str, Any] = {
                "employee_execution_result": result_context,
                "completed_at": str(run.get("completed_at") or ""),
            }
            if success and self._task_profile_policy.runtime_family(task) == "self_learning":
                assessment = assess_autonomous_learning_quality(
                    task,
                    {"response": result_summary},
                )
                metadata["quality_score"] = assessment["score"]
                metadata["learning_quality_assessment"] = assessment
            self._task_state.update_metadata(task.task_id, metadata=metadata)
            final_status = "completed" if success else "failed"
            updated = self._task_state.update_status(
                task.task_id,
                status=final_status,
                actor="employee_agent",
                reason=(
                    "员工代理已完成 API-B 派发的任务。"
                    if success
                    else "员工代理未能完成 API-B 派发的任务。"
                ),
                context=result_context,
                event_type=f"employee_execution_{final_status}",
            )
            await self._touch_gateway_activity(
                "autonomous_chain_execute",
                metadata={
                    "task_id": task.task_id,
                    "employee_task_id": schedule.get("schedule_id"),
                    "worker_role": schedule.get("worker_role"),
                    "status": final_status,
                },
            )
            updates.append({"task_id": updated.task_id, "status": final_status})
        return updates

    def _find_schedule(self, task_id: str) -> Dict[str, Any] | None:
        matches = [
            schedule
            for schedule in self._scheduled_task_store.list(include_completed=True)
            if str(schedule.get("autonomous_task_id") or "") == task_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: str(item.get("created_at") or ""))

    def _latest_runs_by_schedule(self) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for run in self._scheduled_task_store.recent_runs(limit=1000):
            latest.setdefault(str(run.get("schedule_id") or ""), dict(run))
        return latest

    def _record_assignment(
        self,
        task: AutonomousChainTask,
        schedule: Dict[str, Any],
    ) -> None:
        assignment = {
            "employee_task_id": str(schedule.get("schedule_id") or ""),
            "worker_role": str(schedule.get("worker_role") or ""),
            "dispatched_at": str(schedule.get("created_at") or ""),
        }
        if dict(task.metadata or {}).get("employee_assignment") != assignment:
            self._task_state.update_metadata(
                task.task_id,
                metadata={"employee_assignment": assignment},
            )

    def _worker_role(self, task: AutonomousChainTask) -> str:
        governance_type = self._task_profile_policy.governance_type(task)
        execution_kind = self._task_profile_policy.execution_kind(task)
        if execution_kind in {"body_improvement", "body_upgrade", "general_self_evolution"}:
            return "coding"
        if governance_type == "self_learning":
            return "research"
        return "general"

    def _instruction(self, task: AutonomousChainTask) -> str:
        payload = {
            "task_id": task.task_id,
            "summary": task.summary,
            "task_type": task.task_type,
            "governance_task_type": task.governance_task_type,
            "task_family": task.task_family,
            "execution_kind": task.execution_kind,
            "evidence": task.evidence,
            "constraints": task.constraints,
            "execution_request": (
                task.execution_request.model_dump(mode="json")
                if task.execution_request is not None
                else None
            ),
        }
        return (
            "完成以下由 API-B 审批并派发的任务。你是独立员工代理，不得再调用或转交给 API-A。"
            "严格遵守 constraints；需要修改代码时在隔离任务环境中完成并运行相关验证，"
            "不得直接切换或覆盖当前活动身体。最终回复必须陈述实际完成内容、证据、验证结果和未完成项。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:11500]
        )

    @staticmethod
    def _assignment_payload(schedule: Dict[str, Any], *, created: bool) -> Dict[str, Any]:
        return {
            "status": "dispatched" if created else "already_dispatched",
            "employee_task_id": schedule.get("schedule_id"),
            "worker_role": schedule.get("worker_role"),
        }

    @staticmethod
    def _result_context(
        schedule: Dict[str, Any],
        run: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "employee_task_id": schedule.get("schedule_id"),
            "employee_run_id": run.get("run_id"),
            "worker_role": schedule.get("worker_role"),
            "execution_provider": run.get("execution_provider"),
            "execution_model": run.get("execution_model"),
            "result_summary": str(run.get("result_summary") or "")[:12000],
            "error": str(run.get("error") or "")[:2000],
            "elapsed_ms": run.get("elapsed_ms"),
        }


__all__ = ["AutonomousEmployeeDispatchService"]
