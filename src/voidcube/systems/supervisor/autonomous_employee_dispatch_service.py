"""Dispatch API-B approved work to isolated employee agents."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from .autonomous_chain_store import AutonomousChainTask
from .autonomous_chain_store import StaleExecutionLeaseError
from .autonomous_learning_quality import assess_autonomous_learning_quality
from .task_profile_policy import TaskProfilePolicy


logger = logging.getLogger("supervisor")


class AutonomousEmployeeDispatchService:
    """Own employee dispatch and return results to the Supervisor."""

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
        review_body_improvement: Callable[[Dict[str, Any]], Any] | None = None,
        on_employee_result: Callable[
            [AutonomousChainTask, Dict[str, Any], str], Any
        ]
        | None = None,
    ) -> None:
        self._task_state = task_state
        self._task_store = task_store
        self._scheduled_task_store = scheduled_task_store
        self._task_profile_policy = task_profile_policy
        self._resolve_worker_role = resolve_worker_role
        self._touch_gateway_activity = touch_gateway_activity
        self._record_ui_activity = record_ui_activity
        self._review_body_improvement = review_body_improvement
        self._on_employee_result = on_employee_result

    @staticmethod
    def _parse_result_payload(result_summary: str) -> Dict[str, Any] | None:
        text = str(result_summary or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def _validate_body_improvement_result(
        self,
        task: AutonomousChainTask,
        result_summary: str,
    ) -> Dict[str, Any]:
        execution_kind = str(
            self._task_profile_policy.execution_kind(task) or ""
        ).strip().lower()
        if execution_kind != "body_improvement":
            return {"ok": True}

        payload = self._parse_result_payload(result_summary)
        if payload is None:
            return {
                "ok": False,
                "reject_reason": "body_improvement_result_must_be_json",
            }
        report = payload.get("body_improvement_report")
        report = dict(report) if isinstance(report, dict) else payload
        task_id = str(report.get("task_id") or payload.get("task_id") or "").strip()
        if task_id != task.task_id:
            return {"ok": False, "reject_reason": "body_improvement_task_id_mismatch"}

        lease = getattr(task, "execution_lease", None)
        if lease is None:
            return {
                "ok": False,
                "reject_reason": "body_improvement_lease_missing",
            }
        try:
            generation = int(
                report.get("lease_generation", payload.get("lease_generation"))
            )
        except (TypeError, ValueError):
            generation = -1
        attempt_id = str(
            report.get("attempt_id") or payload.get("attempt_id") or ""
        ).strip()
        if generation != lease.generation or attempt_id != str(lease.attempt_id or ""):
            return {
                "ok": False,
                "reject_reason": "body_improvement_lease_mismatch",
                "expected_generation": lease.generation,
                "expected_attempt_id": lease.attempt_id,
            }

        lineage = report.get("git_lineage") or report.get("commit_lineage")
        lineage = dict(lineage) if isinstance(lineage, dict) else {}
        baseline_commit = str(
            report.get("baseline_commit") or lineage.get("source_commit") or ""
        ).strip()
        candidate_commit = str(
            report.get("commit_hash")
            or report.get("candidate_commit")
            or lineage.get("candidate_commit")
            or ""
        ).strip()
        changed_files = report.get("changed_files") or lineage.get("changed_files")
        changed_files = [str(path).strip() for path in changed_files or [] if str(path).strip()]
        if not baseline_commit or not candidate_commit or not changed_files:
            return {
                "ok": False,
                "reject_reason": "body_improvement_commit_lineage_missing",
            }
        verification = report.get("verification") or report.get("validation")
        verification = dict(verification) if isinstance(verification, dict) else {}
        checks = verification.get("checks") or verification.get("evidence") or []
        verified = verification.get("passed") is True or (
            isinstance(checks, list) and bool(checks)
        )
        if not verified:
            return {
                "ok": False,
                "reject_reason": "body_improvement_verification_missing",
            }

        expected_request = getattr(task, "execution_request", None)
        expected_lineage = (
            expected_request.git_lineage.model_dump(mode="json")
            if expected_request is not None
            else dict(task.evidence.get("git_lineage") or {})
        )
        expected_source = str(
            expected_lineage.get("source_commit")
            or task.evidence.get("evaluated_baseline_commit")
            or ""
        ).strip()
        expected_candidate = str(
            expected_lineage.get("candidate_commit")
            or task.evidence.get("evaluated_candidate_commit")
            or ""
        ).strip()
        if expected_source and baseline_commit != expected_source:
            return {"ok": False, "reject_reason": "body_improvement_baseline_mismatch"}
        if expected_candidate and candidate_commit != expected_candidate:
            return {"ok": False, "reject_reason": "body_improvement_candidate_mismatch"}
        expected_files = {
            str(path).strip()
            for path in (
                expected_lineage.get("changed_files")
                or task.evidence.get("changed_files")
                or []
            )
            if str(path).strip()
        }
        if expected_files and expected_files != set(changed_files):
            return {"ok": False, "reject_reason": "body_improvement_changed_files_mismatch"}

        return {
            "ok": True,
            "task_id": task_id,
            "lease_generation": generation,
            "attempt_id": attempt_id,
            "git_lineage": {
                "source_commit": baseline_commit,
                "candidate_commit": candidate_commit,
                "changed_files": list(dict.fromkeys(changed_files)),
            },
            "verification": verification,
            "report": {
                **report,
                "task_id": task_id,
                "baseline_commit": baseline_commit,
                "commit_hash": candidate_commit,
                "changed_files": list(dict.fromkeys(changed_files)),
            },
        }

    async def _review_body_improvement_result(
        self,
        evidence_validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run the governed git/worktree review before allowing completion."""
        if self._review_body_improvement is None:
            return {"ok": True, "skipped": "review_service_unconfigured"}
        report = evidence_validation.get("report")
        if not isinstance(report, dict):
            return {"ok": False, "reject_reason": "body_improvement_report_missing"}
        try:
            result = self._review_body_improvement(report)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return {
                "ok": False,
                "reject_reason": "body_improvement_review_failed",
                "error": str(exc)[:500],
            }
        if not isinstance(result, dict):
            return {
                "ok": False,
                "reject_reason": "body_improvement_review_invalid_result",
            }
        if result.get("reject_reason"):
            return {
                "ok": False,
                "reject_reason": str(result["reject_reason"]),
                "review": result,
            }
        return {"ok": True, "review": result}

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
            if schedule is None and task.status in {"reconciling", "approved", "retry"}:
                dispatch_task = task
                if task.status == "reconciling":
                    dispatch_task = self._task_state.update_status(
                        task.task_id,
                        status="approved",
                        actor="employee_dispatch_migration",
                        reason="历史执行租约状态已迁移为员工代理派工状态。",
                        context={"migration": "employee_reconciling_to_employee_dispatch"},
                        event_type="employee_dispatch_migration",
                    )
                assignment = self.dispatch(dispatch_task)
                updates.append(
                    {
                        "task_id": dispatch_task.task_id,
                        "status": str(dispatch_task.status),
                        "employee_task_id": assignment.get("employee_task_id"),
                    }
                )
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

            # A legacy migration can leave a terminal scheduled run paired
            # with an approved canonical task. Re-enter the legal execution
            # path before projecting the terminal result so one stale item
            # cannot abort reconciliation for the rest of the employee lane.
            lease = getattr(task, "execution_lease", None)
            if (
                task.status in {"approved", "retry"}
                and lease is not None
                and str(getattr(lease, "state", "") or "").strip().lower()
                == "reconciling"
            ):
                task = self._task_state.update_status(
                    task.task_id,
                    status="running",
                    actor="employee_dispatch_recovery",
                    reason="历史员工结果已存在，恢复合法执行状态后继续回收。",
                    context=self._result_context(schedule, run),
                    event_type="employee_execution_recovery",
                )

            success = run_status == "completed"
            result_summary = str(run.get("result_summary") or "").strip()
            result_context = self._result_context(schedule, run)
            evidence_validation = (
                self._validate_body_improvement_result(task, result_summary)
                if success
                else {"ok": True, "skipped": "employee_run_failed"}
            )
            if success and evidence_validation.get("ok") and "report" in evidence_validation:
                review_validation = await self._review_body_improvement_result(
                    evidence_validation
                )
                evidence_validation["review_validation"] = review_validation
                if not review_validation.get("ok"):
                    success = False
            result_context["evidence_validation"] = evidence_validation
            if success and result_summary:
                result_context["employee_final_response"] = result_summary[:4000]
            if success and not evidence_validation.get("ok"):
                success = False
            final_status = "completed" if success else "failed"
            metadata: Dict[str, Any] = {
                "employee_execution_result": result_context,
                "completed_at": str(run.get("completed_at") or ""),
                "employee_result_disposition": {
                    "status": "returned_to_xingzi",
                    "returned_at": datetime.now(timezone.utc).isoformat(),
                    "employee_task_id": str(schedule.get("schedule_id") or ""),
                    "employee_run_id": str(run.get("run_id") or ""),
                    "final_status": final_status,
                },
            }
            if success and self._task_profile_policy.runtime_family(task) == "self_learning":
                assessment = assess_autonomous_learning_quality(
                    task,
                    {"response": result_summary},
                )
                metadata["quality_score"] = assessment["score"]
                metadata["learning_quality_assessment"] = assessment
            self._task_state.update_metadata(task.task_id, metadata=metadata)
            reason = (
                "员工代理已完成 API-B 派发的任务。"
                if success
                else "员工代理 body improvement 未通过受治理的 git/worktree 评审。"
                if evidence_validation.get("review_validation", {}).get("reject_reason")
                else "员工代理结果缺少可验证的 body improvement 证据。"
                if evidence_validation.get("reject_reason")
                else "员工代理未能完成 API-B 派发的任务。"
            )
            lease = getattr(task, "execution_lease", None)
            if lease is not None and lease.state == "active" and lease.attempt_id:
                try:
                    updated = self._task_state.finalize_execution(
                        task.task_id,
                        generation=lease.generation,
                        attempt_id=str(lease.attempt_id),
                        status=final_status,
                        actor="employee_agent",
                        reason=reason,
                        context=result_context,
                    )
                except StaleExecutionLeaseError:
                    # A gate stop or another fenced writer already closed this
                    # generation. Its terminal state is authoritative.
                    current = self._task_store.get_task(task.task_id)
                    if current is None:
                        continue
                    current_lease = current.execution_lease
                    if (
                        current.status == "running"
                        and current_lease.generation == lease.generation
                        and current_lease.attempt_id == lease.attempt_id
                    ):
                        try:
                            updated = self._task_state.expire_execution(
                                task.task_id,
                                expected_generation=lease.generation,
                                expected_attempt_id=str(lease.attempt_id),
                                expected_heartbeat_at=current_lease.heartbeat_at,
                                reason="员工结果回收时 autonomous execution lease 已过期。",
                            )
                        except StaleExecutionLeaseError:
                            updated = self._task_store.get_task(task.task_id) or current
                    else:
                        updated = current
            else:
                updated = self._task_state.update_status(
                    task.task_id,
                    status=final_status,
                    actor="employee_agent",
                    reason=reason,
                    context=result_context,
                    event_type=f"employee_execution_{final_status}",
                )
            if self._on_employee_result is not None:
                try:
                    callback_result = self._on_employee_result(
                        updated,
                        result_context,
                        final_status,
                    )
                    if inspect.isawaitable(callback_result):
                        await callback_result
                except Exception as exc:
                    logger.warning(
                        "Employee result was returned but Supervisor handling failed for %s: %s",
                        task.task_id,
                        exc,
                    )
            if str(schedule.get("schedule_type") or "once").strip().lower() != "once":
                self._prepare_recurring_successor(task, schedule)
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

    def _prepare_recurring_successor(
        self,
        task: AutonomousChainTask,
        schedule: Dict[str, Any],
    ) -> None:
        """Keep recurring Assist schedules claimable with a fresh canonical generation."""
        metadata = dict(task.metadata or {})
        metadata.pop("employee_assignment", None)
        metadata["recurring_parent_task_id"] = task.task_id
        successor = self._task_state.create_task(
            title=task.title,
            summary=task.summary,
            task_type=task.task_type,
            source=task.source,
            priority=task.priority,
            metadata=metadata,
            evidence=dict(task.evidence or {}),
            constraints=dict(task.constraints or {}),
        )
        successor = self._task_state.update_status(
            successor.task_id,
            status="approved",
            actor="employee_recurrence",
            reason="Recurring employee schedule prepared its next canonical execution.",
            context={
                "parent_task_id": task.task_id,
                "employee_task_id": schedule.get("schedule_id"),
            },
            event_type="employee_recurring_successor",
        )
        updated_schedule = self._scheduled_task_store.update(
            str(schedule.get("schedule_id") or ""),
            {"autonomous_task_id": successor.task_id},
        )
        self._task_state.update_metadata(
            successor.task_id,
            metadata={
                "employee_assignment": {
                    "employee_task_id": str(updated_schedule.get("schedule_id") or ""),
                    "worker_role": str(updated_schedule.get("worker_role") or ""),
                    "dispatched_at": str(updated_schedule.get("updated_at") or ""),
                }
            },
        )

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
        execution_lease = getattr(task, "execution_lease", None)
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
            "execution_lease": (
                execution_lease.model_dump(mode="json")
                if execution_lease is not None and hasattr(execution_lease, "model_dump")
                else vars(execution_lease)
                if execution_lease is not None
                else None
            ),
        }
        instruction = (
            "完成以下由 API-B 审批并派发的任务。你是独立员工代理，不得再调用或转交给 API-A。"
            "严格遵守 constraints；代码类任务必须使用系统绑定的 canonical worktree。"
            "不得直接切换或覆盖当前活动身体。最终回复必须陈述实际完成内容、证据、验证结果和未完成项。"
        )
        if self._task_profile_policy.execution_kind(task) == "body_improvement":
            instruction += (
                " body improvement 任务的 evaluated candidate 已由 Supervisor 物化到 shell；"
                "本任务只允许检查该 commit、运行验证并提交证据，不得修改文件、创建新 commit 或 reset/切换 worktree。"
                "任务完成时，最终回复必须是 JSON（可放在 markdown code fence 中），"
                "包含 body_improvement_report.task_id、lease_generation、attempt_id、"
                "baseline_commit、commit_hash、changed_files，以及 verification.passed=true 或 verification.checks。"
            )
        return instruction + "\n\n" + json.dumps(
            payload, ensure_ascii=False, indent=2, default=str
        )[:11500]

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
