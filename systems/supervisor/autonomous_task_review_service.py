"""Runtime orchestration for autonomous-chain task review."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException

from systems.supervisor.autonomous_chain_store import (
    AutonomousChainExecutionRequest,
    AutonomousChainStore,
    AutonomousChainTask,
)
from systems.supervisor.autonomous_task_review import (
    is_agent_pull_task,
    normalize_autonomous_chain_decision,
)
from systems.supervisor.autonomous_task_state import AutonomousTaskStateService
from systems.supervisor.schedule_allocator import ScheduleAllocator
from systems.supervisor.task_profile_policy import TaskProfilePolicy


ResolveDriveInput = Callable[..., Awaitable[Dict[str, Any]]]
ReviewAdviser = Callable[..., Awaitable[Dict[str, Dict[str, Any]]]]
AutoDecision = Callable[[AutonomousChainTask, Dict[str, Any]], tuple[str, str]]
BuildExecutionRequest = Callable[..., Optional[AutonomousChainExecutionRequest]]
MemoryPromotion = Callable[[AutonomousChainTask], Awaitable[Optional[Dict[str, Any]]]]
NormalizeContext = Callable[..., Dict[str, Any]]
SerializeTask = Callable[[AutonomousChainTask], Dict[str, Any]]
BuildActivityMetadata = Callable[..., Dict[str, Any]]
RecordActivity = Callable[..., None]
TouchActivity = Callable[..., Awaitable[Any]]


class AutonomousTaskReviewService:
    """Coordinate review effects without owning Supervisor or route state."""

    def __init__(
        self,
        *,
        store: AutonomousChainStore,
        task_profile_policy: TaskProfilePolicy,
        schedule_allocator: ScheduleAllocator,
        task_state: AutonomousTaskStateService,
        resolve_drive_input: ResolveDriveInput,
        auto_decision: AutoDecision,
        normalize_context: NormalizeContext,
        build_execution_request: BuildExecutionRequest,
        propose_memory_promotion: MemoryPromotion,
        build_response_fields: Callable[..., Dict[str, Any]],
        serialize_task: SerializeTask,
        build_activity_metadata: BuildActivityMetadata,
        record_activity: RecordActivity,
        touch_activity: TouchActivity,
        get_active_tasks: Callable[[], list[AutonomousChainTask]],
        get_review_statuses: Callable[[], list[str]],
    ) -> None:
        self._store = store
        self._task_profile_policy = task_profile_policy
        self._schedule_allocator = schedule_allocator
        self._task_state = task_state
        self._resolve_drive_input = resolve_drive_input
        self._auto_decision = auto_decision
        self._normalize_context = normalize_context
        self._build_execution_request = build_execution_request
        self._propose_memory_promotion = propose_memory_promotion
        self._build_response_fields = build_response_fields
        self._serialize_task = serialize_task
        self._build_activity_metadata = build_activity_metadata
        self._record_activity = record_activity
        self._touch_activity = touch_activity
        self._get_active_tasks = get_active_tasks
        self._get_review_statuses = get_review_statuses

    async def review(
        self,
        request: Optional[Dict[str, Any]] = None,
        *,
        review_adviser: ReviewAdviser,
    ) -> Dict[str, Any]:
        request = dict(request or {})
        statuses = request.get("statuses") or self._get_review_statuses()
        normalized_statuses = []
        for status in statuses:
            normalized = normalize_autonomous_chain_decision(str(status))
            if normalized is None or normalized == "auto":
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported review status: {status}",
                )
            normalized_statuses.append(normalized)

        drive_input = await self._resolve_drive_input(request)
        requested_task_family = self._task_profile_policy.normalize_family(
            request.get("execution_kind")
            or request.get("task_family")
            or drive_input.get("execution_kind")
            or drive_input.get("task_family")
        )
        requested_governance_task_type = self._task_profile_policy.normalize_type(
            requested_task_family
        )
        review_decision = (
            drive_input.get("task_family_decisions", {}).get(requested_task_family)
            or drive_input.get("governance_task_type_decisions", {}).get(
                requested_governance_task_type
            )
            or drive_input["decisions"]
        )
        default_review_status = (
            "approved" if review_decision["eligible_for_execution"] else "deferred"
        )

        candidate_tasks = [
            task
            for task in self._store.list_api_b_judgement_tasks()
            if task.status in normalized_statuses and task.status != "cancelled"
        ]
        candidate_tasks.sort(key=self._schedule_allocator.task_sort_key)

        review_actions = await review_adviser(
            candidate_tasks,
            drive_input=drive_input,
        )
        reserved_schedule_tokens = self._schedule_allocator.conflict_index(
            self._get_active_tasks(),
            exclude_task_ids={task.task_id for task in candidate_tasks},
        )

        reviewed: list[AutonomousChainTask] = []
        reviewed_statuses: list[str] = []
        for task in candidate_tasks:
            task_drive_input = drive_input
            task_family = self._task_profile_policy.runtime_family(task)
            if drive_input.get("task_family") != task_family:
                task_drive_input = await self._resolve_drive_input(
                    dict(request),
                    default_task_family=task_family,
                    default_execution_kind=self._task_profile_policy.execution_kind(task),
                )

            target_status, default_reason = self._auto_decision(task, task_drive_input)
            decision_context: Dict[str, Any] = self._normalize_context(
                drive_input=task_drive_input,
            )
            review_action = review_actions.get(task.task_id)
            reprioritized = False
            if review_action:
                followup_suggestion = review_action.get("followup_suggestion")
                if isinstance(followup_suggestion, dict):
                    decision_context["supervisor_followup_suggestion"] = followup_suggestion

                priority_recommendation = review_action.get("priority")
                if (
                    priority_recommendation is not None
                    and priority_recommendation != str(task.priority)
                ):
                    task = self._task_state.update_priority(
                        task.task_id,
                        priority=priority_recommendation,
                        actor=str(request.get("actor", "supervisor")),
                        reason=(
                            f"Supervisor review reprioritized task to {priority_recommendation}."
                        ),
                        context={
                            **decision_context,
                            "supervisor_priority_adjustment": {
                                "priority": priority_recommendation,
                                "reason": str(review_action.get("reason") or "").strip(),
                            },
                        },
                    )
                    decision_context["supervisor_priority_adjustment"] = {
                        "priority": priority_recommendation,
                        "reason": str(review_action.get("reason") or "").strip(),
                    }
                    reprioritized = True

                suggested_status = self._coerce_review_action(
                    review_action.get("action"),
                    current_status=str(task.status),
                )
                if suggested_status is not None:
                    keep_body_gate = (
                        self._task_profile_policy.execution_kind(task) == "body_improvement"
                        and target_status in {"cancelled", "deferred"}
                    )
                    preserve_agent_pull_approval = (
                        target_status == "approved"
                        and suggested_status in {"deferred", "paused"}
                        and is_agent_pull_task(
                            task,
                            task_profile_policy=self._task_profile_policy,
                        )
                    )
                    if preserve_agent_pull_approval or keep_body_gate:
                        decision_context["supervisor_followup_suggestion"] = {
                            "action": suggested_status,
                            "reason": str(review_action.get("reason") or "").strip(),
                            "preserved_status": target_status,
                        }
                    else:
                        target_status = suggested_status
                    lm_reason = str(review_action.get("reason") or "").strip()
                    if lm_reason and not keep_body_gate:
                        default_reason = f"Supervisor review: {lm_reason}"
                    decision_context["supervisor_review_outcome"] = {
                        "action": suggested_status,
                        "reason": lm_reason,
                    }
                elif isinstance(followup_suggestion, dict):
                    default_reason = (
                        str(request.get("reason"))
                        or "Supervisor follow-up suggestion recorded: "
                        f"{followup_suggestion.get('action', 'review')}."
                    )
                elif reprioritized and not str(request.get("reason") or "").strip():
                    default_reason = (
                        "Supervisor review reprioritized task to "
                        f"{decision_context['supervisor_priority_adjustment']['priority']}."
                    )

            schedule_token = self._schedule_allocator.task_schedule_token(task)
            if target_status == "approved" and schedule_token:
                occupied = reserved_schedule_tokens.get(schedule_token)
                if occupied is not None:
                    target_status = "deferred"
                    decision_context["schedule_conflict"] = {
                        "scheduled_for": schedule_token,
                        "occupied_by_task_id": occupied.task_id,
                        "occupied_by_title": occupied.title,
                        "occupied_by_status": str(occupied.status),
                    }
                    default_reason = (
                        "该链路项暂缓：当前预设时点已被 "
                        f"「{occupied.title}」占用；同一个 scheduled_for 只能保留一个在途链路项。"
                    )

            execution_request = None
            if target_status == "approved":
                decision_id = str(request.get("decision_id") or uuid.uuid4())
                if self._task_profile_policy.requires_execution_request(task):
                    try:
                        execution_request = self._build_execution_request(
                            task,
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=str(request.get("reason") or default_reason),
                            decision_context=self._normalize_context(
                                drive_input=task_drive_input,
                            ),
                        )
                    except ValueError:
                        updated = self._task_state.update_status(
                            task.task_id,
                            status="deferred",
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason="该链路项暂缓：当前自主交接缺少必要的谱系、目标槽位或回滚证据。",
                            context=decision_context,
                            event_type="review",
                        )
                        reviewed.append(updated)
                        reviewed_statuses.append(updated.status)
                        continue
            else:
                decision_id = str(request.get("decision_id") or uuid.uuid4())

            updated = self._task_state.update_status(
                task.task_id,
                status=target_status,
                decision_id=decision_id,
                actor=str(request.get("actor", "supervisor")),
                reason=str(request.get("reason") or default_reason),
                context=decision_context,
                execution_request=execution_request,
                event_type="review",
            )
            if target_status == "approved":
                await self._propose_memory_promotion(updated)
            reviewed.append(updated)
            reviewed_statuses.append(updated.status)
            updated_schedule_token = self._schedule_allocator.task_schedule_token(updated)
            if target_status == "approved" and updated_schedule_token:
                reserved_schedule_tokens.setdefault(updated_schedule_token, updated)

        unique_statuses = sorted(set(reviewed_statuses)) if reviewed else []
        followup_suggestion_count = 0
        followup_action_counts: Dict[str, int] = {}
        priority_update_count = 0
        for task in reviewed:
            if not task.decision_history:
                continue
            latest_context = dict(task.decision_history[-1].context or {})
            followup_suggestion = latest_context.get("supervisor_followup_suggestion")
            if isinstance(followup_suggestion, dict):
                followup_suggestion_count += 1
                action = str(followup_suggestion.get("action") or "unknown")
                followup_action_counts[action] = followup_action_counts.get(action, 0) + 1
            if isinstance(latest_context.get("supervisor_priority_adjustment"), dict):
                priority_update_count += 1

        if reviewed:
            activity_extra = {
                "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                "supervisor_followup_suggestions": followup_suggestion_count,
                "supervisor_suggestion_action_counts": followup_action_counts,
                "supervisor_priority_adjustments": priority_update_count,
            }
            self._record_activity(
                "tasks_reviewed",
                scene="planning",
                summary=(
                    f"监督者已复核 {len(reviewed)} 个链路项: {', '.join(unique_statuses)}。"
                    + (
                        f" 保留建议 {followup_suggestion_count} 条。"
                        if followup_suggestion_count > 0
                        else ""
                    )
                    + (
                        f" 优先级重排 {priority_update_count} 次。"
                        if priority_update_count > 0
                        else ""
                    )
                ),
                metadata=self._build_activity_metadata(
                    reviewed,
                    action="review",
                    extra=activity_extra,
                ),
            )
            await self._touch_activity(
                "autonomous_chain_plan",
                metadata=self._build_activity_metadata(
                    reviewed,
                    action="review",
                    extra=activity_extra,
                ),
            )

        return {
            "status": "reviewed",
            "decision": default_review_status,
            "reviewed_statuses": unique_statuses,
            "tasks": [self._serialize_task(task) for task in reviewed],
            "count": len(reviewed),
            **self._build_response_fields(drive_input=drive_input),
        }

    @staticmethod
    def _coerce_review_action(
        action: Any,
        *,
        current_status: str,
    ) -> Optional[str]:
        if not isinstance(action, str):
            return None
        normalized = {
            "approve": "approved",
            "approved": "approved",
            "defer": "deferred",
            "deferred": "deferred",
            "cancel": "cancelled",
            "cancelled": "cancelled",
            "pause": "paused",
            "paused": "paused",
        }.get(action.strip().lower())
        if normalized is None or current_status in {"completed", "failed", "cancelled"}:
            return None
        return normalized


__all__ = ["AutonomousTaskReviewService"]
