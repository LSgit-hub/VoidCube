"""Planning and serialization owner for autonomous-chain task projections."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException

from systems.self_learning.models import SupervisorConclusionSubmission
from systems.supervisor.autonomous_chain_store import (
    AutonomousChainStore,
    AutonomousChainTask,
)
from systems.supervisor.autonomous_task_state import AutonomousTaskStateService
from systems.supervisor.autonomous_task_review import normalize_autonomous_chain_decision
from systems.supervisor.schedule_allocator import ScheduleAllocator
from systems.supervisor.task_profile_policy import TaskProfilePolicy


BuildActivityMetadata = Callable[..., Dict[str, Any]]
RecordActivity = Callable[..., None]
RecordDriveOutcome = Callable[..., None]
TouchActivity = Callable[..., Awaitable[None]]


class AutonomousChainPlanningService:
    """Own task request normalization, creation, and read-model serialization."""

    def __init__(
        self,
        *,
        store: AutonomousChainStore,
        task_state: AutonomousTaskStateService,
        task_profile_policy: TaskProfilePolicy,
        schedule_allocator: ScheduleAllocator,
        build_activity_metadata: BuildActivityMetadata,
        record_activity: RecordActivity,
        record_drive_outcome: RecordDriveOutcome,
        touch_activity: TouchActivity,
    ) -> None:
        self._store = store
        self._task_state = task_state
        self._task_profile_policy = task_profile_policy
        self._schedule_allocator = schedule_allocator
        self._build_activity_metadata = build_activity_metadata
        self._record_activity = record_activity
        self._record_drive_outcome = record_drive_outcome
        self._touch_activity = touch_activity

    def _request_task_metadata(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        for key in ("governance_task_type", "task_family", "execution_kind", "rationale"):
            value = payload.get(key)
            if value is not None:
                metadata[key] = value
        for key in (
            "scheduled_for",
            "preset_time",
            "scheduled_at",
            "run_at",
            "execute_after",
            "time_slot",
            "window",
        ):
            value = payload.get(key)
            if value is not None and key not in metadata:
                metadata[key] = value
        metadata = self._schedule_allocator.normalize_metadata(metadata)
        explicit_execution_kind = str(metadata.get("execution_kind") or "").strip().lower()
        if explicit_execution_kind in {"body_switch", "body_improvement"} and not metadata.get(
            "task_family"
        ):
            metadata["task_family"] = explicit_execution_kind
        return metadata

    @staticmethod
    def _normalize_execution_request_evidence_payload(
        execution_request_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = dict(execution_request_payload or {})
        drive_input_evidence = dict(payload.get("drive_input_evidence") or {})
        payload.pop("activity_guard_evidence", None)
        if not drive_input_evidence:
            return payload
        payload["drive_input_evidence"] = dict(drive_input_evidence)
        return payload

    def serialize_task(self, task: AutonomousChainTask) -> Dict[str, Any]:
        payload = task.model_dump(mode="json")
        execution_request_payload = payload.get("execution_request")
        if isinstance(execution_request_payload, dict):
            payload["execution_request"] = self._normalize_execution_request_evidence_payload(
                execution_request_payload
            )
        runtime_profile = self._task_profile_policy.runtime_profile(task)
        execution = dict(task.metadata.get("execution_request") or {})
        payload["governance_task_type"] = (
            execution.get("governance_task_type")
            or task.metadata.get("governance_task_type")
            or task.governance_task_type
            or runtime_profile.get("governance_task_type")
        )
        payload["task_family"] = (
            execution.get("task_family")
            or task.metadata.get("task_family")
            or task.task_family
            or runtime_profile.get("task_family")
        )
        payload["execution_kind"] = (
            execution.get("execution_kind")
            or task.metadata.get("execution_kind")
            or task.execution_kind
            or runtime_profile.get("execution_kind")
        )
        scheduled_for = self._schedule_allocator.task_schedule_token(task)
        if scheduled_for is not None:
            payload["scheduled_for"] = scheduled_for
        requested_kind = str(execution.get("kind") or "").strip() or None
        decision_history = payload.get("decision_history") or []
        latest_context: Dict[str, Any] = {}
        if isinstance(decision_history, list) and decision_history:
            latest = decision_history[-1]
            if isinstance(latest, dict):
                latest_context = dict(latest.get("context") or {})
        judgement_preview = self._judgement_preview_projection(
            latest_context=latest_context,
            current_task=task,
        )
        if judgement_preview:
            payload["judgement_preview"] = judgement_preview
        display_kind = (
            requested_kind
            or payload.get("execution_kind")
            or payload.get("task_family")
            or payload.get("governance_task_type")
            or payload.get("task_type")
        )
        payload["task_identity"] = {
            "task_id": payload.get("task_id"),
            "title": payload.get("title"),
            "task_type": payload.get("task_type"),
            "governance_task_type": payload.get("governance_task_type"),
            "task_family": payload.get("task_family"),
            "execution_kind": payload.get("execution_kind"),
            "runtime_task_family": runtime_profile.get("task_family"),
            "runtime_execution_kind": runtime_profile.get("execution_kind"),
            "requested_kind": requested_kind,
            "display_kind": display_kind,
            "summary": (
                f"{payload.get('title')} ({display_kind})"
                if payload.get("title") and display_kind
                else payload.get("title") or display_kind or payload.get("task_id")
            ),
        }
        return payload

    async def list_tasks(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_status = None
        if status is not None:
            normalized_status = normalize_autonomous_chain_decision(status)
            if normalized_status is None or normalized_status == "auto":
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported task status filter: {status}",
                )

        tasks = self._store.list_chain_projection_tasks(
            status=normalized_status,
            include_cancelled=True,
        )
        if task_type:
            normalized_type = str(task_type).strip()
            tasks = [
                task
                for task in tasks
                if self._task_profile_policy.governance_type(task) == normalized_type
            ]
        if execution_kind:
            normalized_kind = self._task_profile_policy.normalize_family(execution_kind)
            explicit_kind = str(execution_kind).strip().lower()
            tasks = [
                task
                for task in tasks
                if (
                    str(self._task_profile_policy.execution_kind(task) or "").strip().lower()
                    == explicit_kind
                    or str(self.serialize_task(task).get("execution_kind") or "").strip().lower()
                    == explicit_kind
                    or self._task_profile_policy.execution_kind(task) == normalized_kind
                )
            ]
        return {
            "tasks": [self.serialize_task(task) for task in tasks],
            "count": len(tasks),
        }

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        task = self._store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Autonomous-chain task not found: {task_id}")
        return self.serialize_task(task)

    def _governance_action_label(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "approve": "转交",
            "defer": "延后",
            "cancel": "清退",
            "pause": "暂停",
            "retire": "退休建议",
            "merge": "合并建议",
            "reprioritize": "重排优先级",
            "reprioritise": "重排优先级",
        }.get(normalized, str(value or "").strip() or "判断动作")

    def _governance_priority_label(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "low": "低",
            "normal": "中",
            "high": "高",
        }.get(normalized, str(value or "").strip() or "未识别")

    def _judgement_preview_projection(
        self,
        *,
        latest_context: Dict[str, Any],
        current_task: AutonomousChainTask,
    ) -> Dict[str, Any]:
        preview: Dict[str, Any] = {}
        notes: list[str] = []

        review_context = latest_context.get("supervisor_review_outcome")
        if isinstance(review_context, dict):
            review_payload = dict(review_context)
            action_label = self._governance_action_label(review_payload.get("action"))
            review_payload["action_label"] = action_label
            review_payload["summary"] = (
                f"监督者已采纳判断动作: {action_label}"
                + (
                    f" · {str(review_payload.get('reason') or '').strip()[:120]}"
                    if str(review_payload.get("reason") or "").strip()
                    else ""
                )
            )
            preview["review_outcome"] = review_payload
            notes.append(str(review_payload["summary"]))

        followup_context = latest_context.get("supervisor_followup_suggestion")
        if isinstance(followup_context, dict):
            followup_payload = dict(followup_context)
            action_label = self._governance_action_label(followup_payload.get("action"))
            followup_payload["action_label"] = action_label
            merge_target = self._store.get_task(str(followup_payload.get("merge_into") or ""))
            merge_target_title = str(merge_target.title or "").strip() if merge_target else ""
            if merge_target_title:
                followup_payload["merge_into_title"] = merge_target_title
            if followup_payload.get("merge_into") and merge_target_title:
                followup_extra = f" · 并入 {merge_target_title}"
            elif followup_payload.get("merge_into"):
                followup_extra = f" · 并入 {str(followup_payload.get('merge_into') or '')[:16]}"
            else:
                followup_extra = ""
            followup_payload["summary"] = (
                f"监督者保留建议: {action_label}{followup_extra}"
                + (
                    f" · {str(followup_payload.get('reason') or '').strip()[:120]}"
                    if str(followup_payload.get("reason") or "").strip()
                    else ""
                )
            )
            preview["followup_suggestion"] = followup_payload
            notes.append(str(followup_payload["summary"]))

        priority_context = latest_context.get("supervisor_priority_adjustment")
        if isinstance(priority_context, dict):
            priority_payload = dict(priority_context)
            priority_label = self._governance_priority_label(priority_payload.get("priority"))
            priority_payload["priority_label"] = priority_label
            priority_payload["summary"] = (
                f"监督者已重排优先级: {priority_label}"
                + (
                    f" · {str(priority_payload.get('reason') or '').strip()[:120]}"
                    if str(priority_payload.get("reason") or "").strip()
                    else ""
                )
            )
            preview["priority_adjustment"] = priority_payload
            notes.append(str(priority_payload["summary"]))

        if notes:
            preview["notes"] = notes[:3]
            preview["summary"] = notes[0]
        if preview:
            preview["task_title"] = str(current_task.title or "").strip()
        return preview

    async def plan(self, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request = dict(request or {})
        items = request.get("items")
        created: list[AutonomousChainTask] = []

        if isinstance(items, list) and items:
            for item in items:
                if not isinstance(item, dict):
                    raise HTTPException(status_code=400, detail="Each task item must be an object.")
                title = str(item.get("title") or "").strip()
                if not title:
                    raise HTTPException(
                        status_code=400,
                        detail="Each task item must include a title.",
                    )
                metadata = self._request_task_metadata(item)
                created.append(
                    self._task_state.create_task(
                        title=title,
                        summary=str(item.get("summary", "")),
                        trace_id=str(item.get("trace_id") or uuid.uuid4()),
                        task_type=self._task_profile_policy.request_type(item, metadata=metadata),
                        source=str(item.get("source", "self_learning")),
                        priority=str(item.get("priority", "normal")),
                        metadata=metadata,
                        evidence=dict(item.get("evidence") or {}),
                        constraints=dict(item.get("constraints") or {}),
                    )
                )
        else:
            title = str(request.get("title") or "").strip()
            if not title:
                raise HTTPException(status_code=400, detail="title is required")
            metadata = self._request_task_metadata(request)
            created.append(
                self._task_state.create_task(
                    title=title,
                    summary=str(request.get("summary", "")),
                    trace_id=str(request.get("trace_id") or uuid.uuid4()),
                    task_type=self._task_profile_policy.request_type(request, metadata=metadata),
                    source=str(request.get("source", "self_learning")),
                    priority=str(request.get("priority", "normal")),
                    metadata=metadata,
                    evidence=dict(request.get("evidence") or {}),
                    constraints=dict(request.get("constraints") or {}),
                )
            )

        activity_metadata = self._build_activity_metadata(created, action="plan")
        await self._touch_activity("autonomous_chain_plan", metadata=activity_metadata)
        if created:
            for task in created:
                self._record_drive_outcome(task, event_type="planned")
            self._record_activity(
                "tasks_planned",
                scene="planning",
                summary=f"监督者已把 {len(created)} 个链路项纳入 API-B 判断在途存储。",
                metadata=activity_metadata,
            )

        return {
            "status": "planned",
            "tasks": [self.serialize_task(task) for task in created],
            "count": len(created),
        }

    async def submit_self_learning_conclusion(
        self,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            submission = SupervisorConclusionSubmission.model_validate(request or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        created: list[Dict[str, Any]] = []
        for proposal in submission.proposals:
            proposal_metadata = {
                **submission.metadata,
                **proposal.metadata,
            }
            if proposal.governance_task_type is not None:
                proposal_metadata["governance_task_type"] = proposal.governance_task_type
            if proposal.task_family is not None:
                proposal_metadata["task_family"] = proposal.task_family
            if proposal.execution_kind is not None:
                proposal_metadata["execution_kind"] = proposal.execution_kind
            proposal_payload = {
                "task_type": proposal.task_type,
                "source": proposal.source,
                "governance_task_type": proposal.governance_task_type,
                "task_family": proposal.task_family,
                "execution_kind": proposal.execution_kind,
                "metadata": proposal_metadata,
            }
            task = self._task_state.create_task(
                title=proposal.title,
                summary=proposal.summary,
                trace_id=str(
                    submission.metadata.get("trace_id")
                    or submission.conclusion_id
                    or uuid.uuid4()
                ),
                task_type=self._task_profile_policy.request_type(
                    proposal_payload,
                    metadata=proposal_metadata,
                ),
                source=proposal.source,
                priority=proposal.priority,
                metadata=proposal_metadata,
                evidence={
                    **submission.evidence,
                    **proposal.evidence,
                },
                constraints=dict(proposal.constraints),
            )
            created.append(self.serialize_task(task))

        if created:
            self._record_activity(
                "self_learning_submitted",
                scene="drive",
                summary=f"自主学习结论已提交 {len(created)} 个 API-B 判断在途提案。",
                metadata={
                    "count": len(created),
                    "conclusion_id": submission.conclusion_id,
                    "task_ids": [task.get("task_id") for task in created],
                },
            )
            await self._touch_activity(
                "self_learning",
                metadata={
                    "action": "self_learning_submission",
                    "count": len(created),
                    "conclusion_id": submission.conclusion_id,
                },
            )

        return {
            "status": "accepted",
            "source": submission.source,
            "conclusion_id": submission.conclusion_id,
            "topic_id": submission.topic_id,
            "count": len(created),
            "tasks": created,
        }


__all__ = ["AutonomousChainPlanningService"]
