from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from systems.evolution_boundary import classify_agent_evolution_changes
from systems.runtime_task_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_family,
    normalize_runtime_task_type,
    resolve_broad_task_type,
)
from systems.self_learning import SupervisorConclusionSubmission
from systems.supervisor.endogenous_drive import CORE_VALUES
from systems.supervisor.task_queue import (
    SelfEvolutionExecutionRequest,
    SelfEvolutionGitLineage,
    SelfEvolutionTask,
)

logger = logging.getLogger("supervisor")


class PlanningRuntimeMixin:
    """Supervisor planning, idle-window, and self-evolution orchestration."""

    async def get_governor_history(self, limit: int = 20):
        return {
            "history": self._governor.list_history(limit=limit),
            "latest": self._governor.get_latest(),
        }

    def _normalize_runtime_task_family(self, value: Optional[str]) -> str:
        return str(
            normalize_runtime_task_family(value, default="general_self_evolution")
        )

    def _normalize_runtime_task_type(self, value: Optional[str]) -> str:
        return str(normalize_runtime_task_type(value, default="self_evolution"))

    def _task_runtime_family(self, task: SelfEvolutionTask) -> str:
        execution = dict(task.metadata.get("execution_request") or {})
        runtime_task_profile = derive_runtime_task_profile(
            task_type=task.task_type,
            governance_task_type=(
                execution.get("governance_task_type")
                or task.governance_task_type
                or task.metadata.get("governance_task_type")
            ),
            task_family=(
                execution.get("task_family")
                or task.task_family
                or task.metadata.get("task_family")
            ),
            execution_kind=(
                execution.get("execution_kind")
                or task.execution_kind
                or task.metadata.get("execution_kind")
            ),
            kind=execution.get("kind"),
            default_task_family="general_self_evolution",
        )
        return str(runtime_task_profile["task_family"] or "general_self_evolution")

    def _task_execution_kind(self, task: SelfEvolutionTask) -> Optional[str]:
        task_family = self._task_runtime_family(task)
        if task_family in {
            "body_upgrade",
            "body_switch",
            "memory_maintenance",
            "general_self_evolution",
        }:
            return task_family
        return None

    def _task_runtime_profile(self, task: SelfEvolutionTask) -> Dict[str, Any]:
        execution = dict(task.metadata.get("execution_request") or {})
        return derive_runtime_task_profile(
            task_type=task.task_type,
            governance_task_type=(
                execution.get("governance_task_type")
                or task.governance_task_type
                or task.metadata.get("governance_task_type")
            ),
            task_family=(
                execution.get("task_family")
                or task.task_family
                or task.metadata.get("task_family")
            ),
            execution_kind=(
                execution.get("execution_kind")
                or task.execution_kind
                or task.metadata.get("execution_kind")
            ),
            kind=execution.get("kind"),
            default_task_family="general_self_evolution",
        )

    def _request_task_metadata(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        for key in ("governance_task_type", "task_family", "execution_kind"):
            value = payload.get(key)
            if value is not None:
                metadata[key] = value
        return metadata

    def _request_task_type(
        self,
        payload: Dict[str, Any],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        merged_metadata = dict(metadata or payload.get("metadata") or {})
        return resolve_broad_task_type(
            task_type=payload.get("task_type"),
            governance_task_type=merged_metadata.get("governance_task_type"),
            task_family=merged_metadata.get("task_family"),
            execution_kind=merged_metadata.get("execution_kind"),
            source=payload.get("source"),
        )

    def _idle_window_request_profile(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return derive_runtime_task_profile(
            governance_task_type=request.get("governance_task_type"),
            task_family=request.get("task_family"),
            execution_kind=request.get("execution_kind"),
            default_task_family="general_self_evolution",
        )

    def _task_governance_type(self, task: SelfEvolutionTask) -> str:
        return str(self._task_runtime_profile(task)["governance_task_type"])

    def _task_requires_execution_request(self, task: SelfEvolutionTask) -> bool:
        return self._task_governance_type(task) in {"self_evolution", "memory_maintenance"}

    def _task_activity_metadata(self, task: SelfEvolutionTask) -> Dict[str, Any]:
        profile = self._task_runtime_profile(task)
        metadata: Dict[str, Any] = {
            "task_id": task.task_id,
            "trace_id": task.trace_id,
            "task_type": task.task_type,
            "governance_task_type": profile["governance_task_type"],
            "task_family": profile["task_family"],
        }
        execution_kind = profile.get("execution_kind")
        if execution_kind is not None:
            metadata["execution_kind"] = execution_kind
        return metadata

    def _build_self_evolution_activity_metadata(
        self,
        tasks: list[SelfEvolutionTask],
        *,
        action: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "action": action,
            "count": len(tasks),
            **dict(extra or {}),
        }
        metadata["governance_task_types"] = sorted(
            {self._task_governance_type(task) for task in tasks}
        )
        metadata["task_families"] = sorted(
            {self._task_runtime_family(task) for task in tasks}
        )
        execution_kinds = sorted(
            {
                execution_kind
                for execution_kind in (self._task_execution_kind(task) for task in tasks)
                if execution_kind is not None
            }
        )
        if execution_kinds:
            metadata["execution_kinds"] = execution_kinds
        if len(tasks) == 1:
            metadata.update(self._task_activity_metadata(tasks[0]))
        return metadata

    def _planning_activity_kind_for_task(self, task_type: str) -> str:
        normalized = self._normalize_runtime_task_type(task_type)
        if normalized == "self_learning":
            return "self_learning"
        if normalized in {"self_evolution", "memory_maintenance"}:
            return "self_evolution_plan"
        return "self_evolution_plan"

    async def _fetch_gateway_activity_snapshot(self) -> Dict[str, Any]:
        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/admin/activity"
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        raise HTTPException(
                            status_code=503,
                            detail=f"Gateway activity endpoint returned status {response.status}",
                        )
                    return await response.json()
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(f"Failed to fetch gateway activity snapshot: {exc}")
            raise HTTPException(status_code=503, detail="Gateway activity snapshot unavailable")

    def _parse_activity_timestamp(self, value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _idle_seconds_since(self, timestamp: Optional[datetime], *, now: datetime) -> Optional[float]:
        if timestamp is None:
            return None
        return max((now - timestamp).total_seconds(), 0.0)

    async def get_runtime_activity(self):
        snapshot = await self._fetch_gateway_activity_snapshot()
        return {
            "status": "ok",
            "gateway_address": self.config.execution.gateway_address,
            "activity": snapshot,
        }

    async def evaluate_idle_window(self, request: dict | None = None):
        request = request or {}
        snapshot = await self._fetch_gateway_activity_snapshot()

        now_override = request.get("now")
        if isinstance(now_override, str):
            try:
                now = datetime.fromisoformat(now_override)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid now override: {exc}")
        else:
            now = datetime.now()

        user_idle_threshold = int(request.get("user_idle_seconds", 600))
        memory_idle_threshold = int(request.get("memory_idle_seconds", 600))
        workflow_idle_threshold = int(request.get("workflow_idle_seconds", 600))
        execution_window_start_hour = int(request.get("execution_window_start_hour", 0))
        execution_window_end_hour = int(request.get("execution_window_end_hour", 6))
        requested_task_profile = self._idle_window_request_profile(request)
        requested_governance_task_type = str(requested_task_profile["governance_task_type"])
        requested_task_family = str(requested_task_profile["task_family"])

        last_user_request_at = self._parse_activity_timestamp(snapshot.get("last_user_request_at"))
        last_agent_work_at = self._parse_activity_timestamp(snapshot.get("last_agent_work_at"))
        last_memory_task_at = self._parse_activity_timestamp(snapshot.get("last_memory_task_at"))
        last_self_learning_activity_at = self._parse_activity_timestamp(
            snapshot.get("last_self_learning_activity_at")
        )
        last_self_evolution_plan_at = self._parse_activity_timestamp(
            snapshot.get("last_self_evolution_plan_at")
        )
        last_self_evolution_execute_at = self._parse_activity_timestamp(
            snapshot.get("last_self_evolution_execute_at")
        )
        last_self_evolution_activity_at = self._parse_activity_timestamp(
            snapshot.get("last_self_evolution_activity_at")
        )

        user_idle_seconds = self._idle_seconds_since(last_user_request_at, now=now)
        agent_idle_seconds = self._idle_seconds_since(last_agent_work_at, now=now)
        memory_idle_seconds = self._idle_seconds_since(last_memory_task_at, now=now)
        self_learning_idle_seconds = self._idle_seconds_since(last_self_learning_activity_at, now=now)
        self_evolution_plan_idle_seconds = self._idle_seconds_since(
            last_self_evolution_plan_at,
            now=now,
        )
        self_evolution_execute_idle_seconds = self._idle_seconds_since(
            last_self_evolution_execute_at,
            now=now,
        )
        self_evolution_idle_seconds = self._idle_seconds_since(last_self_evolution_activity_at, now=now)

        has_user_idle = user_idle_seconds is None or user_idle_seconds >= user_idle_threshold
        has_memory_idle = memory_idle_seconds is None or memory_idle_seconds >= memory_idle_threshold
        has_agent_idle = agent_idle_seconds is None or agent_idle_seconds >= workflow_idle_threshold
        has_self_learning_idle = (
            self_learning_idle_seconds is None
            or self_learning_idle_seconds >= workflow_idle_threshold
        )
        has_self_evolution_plan_idle = (
            self_evolution_plan_idle_seconds is None
            or self_evolution_plan_idle_seconds >= workflow_idle_threshold
        )
        has_self_evolution_execute_idle = (
            self_evolution_execute_idle_seconds is None
            or self_evolution_execute_idle_seconds >= workflow_idle_threshold
        )
        has_self_evolution_idle = (
            self_evolution_idle_seconds is None
            or self_evolution_idle_seconds >= workflow_idle_threshold
        )

        in_execution_window = execution_window_start_hour <= now.hour < execution_window_end_hour
        governance_task_type_decisions = {
            "user": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
            "self_learning": {
                "eligible_for_planning": has_user_idle,
                "eligible_for_execution": (
                    has_user_idle
                    and has_agent_idle
                    and has_memory_idle
                    and has_self_learning_idle
                    and has_self_evolution_plan_idle
                    and has_self_evolution_execute_idle
                ),
            },
            "memory_maintenance": {
                "eligible_for_planning": has_user_idle,
                "eligible_for_execution": (
                    in_execution_window
                    and has_user_idle
                    and has_agent_idle
                    and has_memory_idle
                    and has_self_learning_idle
                    and has_self_evolution_plan_idle
                    and has_self_evolution_execute_idle
                ),
            },
            "self_evolution": {
                "eligible_for_planning": (
                    has_user_idle
                    and has_self_evolution_plan_idle
                ),
                "eligible_for_execution": (
                    in_execution_window
                    and has_user_idle
                    and has_agent_idle
                    and has_memory_idle
                    and has_self_learning_idle
                    and has_self_evolution_execute_idle
                ),
            },
        }
        task_family_decisions = {
            "user": dict(governance_task_type_decisions["user"]),
            "self_learning": dict(governance_task_type_decisions["self_learning"]),
            "memory_maintenance": dict(governance_task_type_decisions["memory_maintenance"]),
            "general_self_evolution": dict(governance_task_type_decisions["self_evolution"]),
            "body_upgrade": dict(governance_task_type_decisions["self_evolution"]),
            "body_switch": dict(governance_task_type_decisions["self_evolution"]),
        }
        selected_task_decisions = task_family_decisions[requested_task_family]

        return {
            "status": "evaluated",
            "evaluated_at": now.isoformat(),
            "gateway_address": self.config.execution.gateway_address,
            "governance_task_type": requested_governance_task_type,
            "task_family": requested_task_family,
            "execution_kind": requested_task_profile.get("execution_kind"),
            "task_profile": requested_task_profile,
            "activity": snapshot,
            "idle_seconds": {
                "user": user_idle_seconds,
                "agent": agent_idle_seconds,
                "memory": memory_idle_seconds,
                "self_learning": self_learning_idle_seconds,
                "self_evolution_plan": self_evolution_plan_idle_seconds,
                "self_evolution_execute": self_evolution_execute_idle_seconds,
                "self_evolution": self_evolution_idle_seconds,
            },
            "thresholds": {
                "user_idle_seconds": user_idle_threshold,
                "memory_idle_seconds": memory_idle_threshold,
                "workflow_idle_seconds": workflow_idle_threshold,
                "execution_window_start_hour": execution_window_start_hour,
                "execution_window_end_hour": execution_window_end_hour,
            },
            "checks": {
                "has_user_idle": has_user_idle,
                "has_memory_idle": has_memory_idle,
                "has_agent_idle": has_agent_idle,
                "has_self_learning_idle": has_self_learning_idle,
                "has_self_evolution_plan_idle": has_self_evolution_plan_idle,
                "has_self_evolution_execute_idle": has_self_evolution_execute_idle,
                "has_self_evolution_idle": has_self_evolution_idle,
                "in_execution_window": in_execution_window,
            },
            "governance_task_type_decisions": governance_task_type_decisions,
            "task_family_decisions": task_family_decisions,
            "decisions": {
                "eligible_for_planning": selected_task_decisions["eligible_for_planning"],
                "eligible_for_execution": selected_task_decisions["eligible_for_execution"],
            },
        }

    async def _touch_gateway_activity(
        self,
        activity_kind: str,
        *,
        source_service: str = "supervisor",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/admin/activity/touch"
                payload = {
                    "activity_kind": activity_kind,
                    "source_service": source_service,
                    "metadata": dict(metadata or {}),
                }
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status != 200:
                        logger.debug(
                            "Gateway activity touch ignored with status %s for kind %s",
                            response.status,
                            activity_kind,
                        )
        except Exception as exc:
            logger.debug(f"Unable to touch gateway activity kind={activity_kind}: {exc}")

    def _existing_endogenous_drive_keys(self) -> set[str]:
        active_statuses = {"planned", "deferred", "paused"}
        keys: set[str] = set()
        for task in self._self_evolution_queue.list_tasks():
            is_active = task.status in active_statuses
            if task.status == "approved" and not task.metadata.get("execution_dispatched"):
                is_active = True
            if not is_active:
                continue
            key = task.metadata.get("endogenous_drive_key")
            if isinstance(key, str) and key:
                keys.add(key)
        return keys

    async def evaluate_endogenous_drive(self, request: dict | None = None):
        request = request or {}
        idle_window_request = dict(request.get("idle_window") or {})
        record_activity = bool(request.get("record_activity", True))
        idle_window = await self.evaluate_idle_window(idle_window_request)
        max_candidates = int(
            request.get(
                "max_candidates",
                self.config.service_runtime.endogenous_drive_max_candidates,
            )
        )
        candidates = self._endogenous_drive_engine.generate_candidates(
            idle_window=idle_window,
            existing_drive_keys=self._existing_endogenous_drive_keys(),
            max_candidates=max_candidates,
        )
        if record_activity:
            self._record_supervisor_ui_activity(
                "endogenous_drive_evaluated",
                scene="planning",
                summary=f"Endogenous drive evaluated {len(candidates)} candidate task(s).",
                metadata={
                    "count": len(candidates),
                    "candidate_keys": [candidate.stable_key for candidate in candidates],
                },
            )
        return {
            "status": "evaluated",
            "enabled": self.config.service_runtime.endogenous_drive_enabled,
            "core_values": CORE_VALUES,
            "idle_window": idle_window,
            "candidates": [
                {
                    **candidate.to_queue_item(),
                    "stable_key": candidate.stable_key,
                    "value_tags": candidate.value_tags,
                    "utility": candidate.utility,
                }
                for candidate in candidates
            ],
            "count": len(candidates),
        }

    async def _run_endogenous_drive_cycle(self) -> Dict[str, Any]:
        if not self.config.service_runtime.endogenous_drive_enabled:
            return {"status": "disabled", "planned": 0, "tasks": []}

        evaluation = await self.evaluate_endogenous_drive({})
        candidate_items = [
            candidate
            for candidate in evaluation.get("candidates", [])
            if isinstance(candidate, dict)
        ]
        if not candidate_items:
            self._record_supervisor_ui_activity(
                "endogenous_drive_idle",
                scene="idle",
                summary="Endogenous drive found no new candidate tasks.",
            )
            return {
                "status": "idle",
                "planned": 0,
                "tasks": [],
                "idle_window": evaluation.get("idle_window"),
            }

        plan_result = await self.plan_self_evolution_task({"items": candidate_items})
        created_tasks = plan_result.get("tasks", [])
        if created_tasks:
            self._record_supervisor_ui_activity(
                "endogenous_drive_planned",
                scene="planning",
                summary=f"Endogenous drive queued {len(created_tasks)} candidate task(s).",
                metadata={
                    "task_ids": [task.get("task_id") for task in created_tasks],
                    "endogenous_drive_keys": [
                        task.get("metadata", {}).get("endogenous_drive_key")
                        for task in created_tasks
                    ],
                },
            )
            await self._touch_gateway_activity(
                "self_evolution_plan",
                metadata={
                    "action": "endogenous_drive",
                    "count": len(created_tasks),
                    "endogenous_drive_keys": [
                        task.get("metadata", {}).get("endogenous_drive_key")
                        for task in created_tasks
                    ],
                },
            )

        return {
            "status": "planned",
            "planned": len(created_tasks),
            "tasks": created_tasks,
            "idle_window": evaluation.get("idle_window"),
        }

    def _normalize_self_evolution_decision(self, decision: Optional[str]) -> Optional[str]:
        if decision is None:
            return None
        normalized = decision.strip().lower()
        mapping = {
            "planned": "planned",
            "approve": "approved",
            "approved": "approved",
            "defer": "deferred",
            "deferred": "deferred",
            "pause": "paused",
            "paused": "paused",
            "cancel": "cancelled",
            "cancelled": "cancelled",
            "auto": "auto",
        }
        return mapping.get(normalized)

    def _build_self_evolution_auto_decision(
        self,
        *,
        task: SelfEvolutionTask,
        idle_window: Dict[str, Any],
    ) -> tuple[str, str]:
        task_type = self._task_governance_type(task)
        task_family = self._task_runtime_family(task)
        decision = (
            idle_window.get("task_family_decisions", {}).get(task_family)
            or idle_window.get("governance_task_type_decisions", {}).get(task_type)
            or idle_window["decisions"]
        )

        if decision["eligible_for_execution"]:
            if task_type == "self_learning":
                return (
                    "approved",
                    "Task approved for learn-only follow-up because the user path is idle and no conflicting workflow activity is active. Execution-window gating is not required for self-learning evidence work.",
                )
            if task_type == "memory_maintenance":
                return (
                    "approved",
                    "Task approved for memory maintenance because the system is inside the execution window and user, runtime, memory, and workflow idle requirements are satisfied.",
                )
            if task_family == "body_upgrade":
                return (
                    "approved",
                    "Task approved for body-upgrade execution handoff because the system is inside the execution window and idle requirements are satisfied.",
                )
            if task_family == "body_switch":
                return (
                    "approved",
                    "Task approved for body-switch execution handoff because the system is inside the execution window and idle requirements are satisfied.",
                )
            return (
                "approved",
                "Task approved for the next execution handoff because the system is inside the execution window and idle requirements are satisfied.",
            )
        if task_type == "self_learning":
            return (
                "deferred",
                "Task deferred because the user path or another internal workflow is still active. Self-learning follow-up stays queued until the system is idle enough for learn-only work.",
            )
        if task_type == "memory_maintenance":
            return (
                "deferred",
                "Task deferred because memory maintenance requires the execution window plus idle user, runtime, memory, and workflow facts.",
            )
        if task_family == "body_upgrade":
            return (
                "deferred",
                "Task deferred because body-upgrade execution still requires the execution window and idle runtime facts before handoff.",
            )
        if task_family == "body_switch":
            return (
                "deferred",
                "Task deferred because body-switch execution still requires the execution window and idle runtime facts before handoff.",
            )
        return (
            "deferred",
            "Task deferred because the execution window or idle requirements are not yet satisfied. The task remains queued for future review.",
        )

    def _build_self_evolution_execution_request(
        self,
        task: SelfEvolutionTask,
        *,
        decision_id: str,
        actor: str,
        reason: str,
        decision_context: Dict[str, Any],
    ) -> Optional[SelfEvolutionExecutionRequest]:
        execution = dict(task.metadata.get("execution_request") or {})
        kind = self._task_execution_kind(task) or "general_self_evolution"
        task_family = self._task_runtime_family(task)
        governance_task_type = self._task_governance_type(task)
        if kind not in {
            "body_upgrade",
            "body_switch",
            "memory_maintenance",
            "general_self_evolution",
        }:
            kind = "general_self_evolution"

        git_lineage = {
            **dict(task.evidence.get("git_lineage") or {}),
            **dict(execution.get("git_lineage") or {}),
        }
        rollback_plan = {
            **dict(task.constraints.get("rollback_plan") or {}),
            **dict(execution.get("rollback_plan") or {}),
        }
        governor_decision = {
            "decision": "approved_for_execution",
            "actor": actor,
            "reason": reason,
            "task_status": "approved",
        }
        if "governor_decision" in task.evidence:
            governor_decision["evidence_decision"] = task.evidence["governor_decision"]
        if kind in {"body_upgrade", "body_switch"}:
            governor_decision["evolution_boundary"] = classify_agent_evolution_changes(
                git_lineage.get("changed_files") or []
            ).model_dump()

        return SelfEvolutionExecutionRequest(
            task_id=task.task_id,
            trace_id=task.trace_id,
            task_type=task.task_type,
            governance_task_type=governance_task_type,
            task_family=task_family,
            execution_kind=kind,
            decision_id=decision_id,
            kind=kind,  # type: ignore[arg-type]
            source_actor=str(execution.get("source_actor") or actor or "mem_supervisor"),
            target_slot_id=(
                execution.get("target_slot_id")
                or task.metadata.get("target_slot_id")
                or task.constraints.get("target_slot_id")
            ),
            git_lineage=SelfEvolutionGitLineage.model_validate(git_lineage),
            probe_report_ref=(
                execution.get("probe_report_ref")
                or task.evidence.get("probe_report_ref")
                or task.evidence.get("probe_report_path")
            ),
            idle_window_evidence=dict(decision_context.get("idle_window") or {}),
            governor_decision=governor_decision,
            rollback_plan=rollback_plan,
        )

    def _serialize_self_evolution_task(self, task: SelfEvolutionTask) -> Dict[str, Any]:
        payload = task.model_dump(mode="json")
        payload.update(self._task_runtime_profile(task))
        boundary = self._self_evolution_task_boundary(task)
        if boundary is not None:
            payload["evolution_boundary"] = boundary
        return payload

    def _self_evolution_task_git_lineage(self, task: SelfEvolutionTask) -> Dict[str, Any]:
        execution = dict(task.metadata.get("execution_request") or {})
        return {
            **dict(task.evidence.get("git_lineage") or {}),
            **dict(execution.get("git_lineage") or {}),
        }

    def _self_evolution_task_boundary(self, task: SelfEvolutionTask) -> Optional[Dict[str, Any]]:
        kind = self._task_execution_kind(task) or "general_self_evolution"
        if kind not in {"body_upgrade", "body_switch"}:
            return None

        git_lineage = self._self_evolution_task_git_lineage(task)
        changed_files = git_lineage.get("changed_files") or []
        return classify_agent_evolution_changes(changed_files).model_dump()

    def _record_self_evolution_boundary_defer(
        self,
        *,
        task: SelfEvolutionTask,
        actor: str,
        reason: str,
        boundary: Dict[str, Any],
    ) -> None:
        try:
            execution = dict(task.metadata.get("execution_request") or {})
            self._governor.record_boundary_defer(
                task_id=task.task_id,
                trace_id=task.trace_id,
                task_type=task.task_type,
                governance_task_type=self._task_governance_type(task),
                task_family=self._task_runtime_family(task),
                execution_kind=self._task_execution_kind(task),
                decision_id=(
                    task.decision_history[-1].decision_id
                    if task.decision_history
                    else None
                ),
                title=task.title,
                body_id=(
                    execution.get("target_slot_id")
                    or task.metadata.get("target_slot_id")
                    or task.constraints.get("target_slot_id")
                ),
                source_actor=actor,
                reason=reason,
                git_lineage=self._self_evolution_task_git_lineage(task),
                evolution_boundary=boundary,
            )
        except Exception as exc:
            logger.warning("Failed to record boundary defer governance history: %s", exc)

    async def list_self_evolution_tasks(self, status: Optional[str] = None):
        normalized_status = None
        if status is not None:
            normalized_status = self._normalize_self_evolution_decision(status)
            if normalized_status is None or normalized_status == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported task status filter: {status}")
        tasks = self._self_evolution_queue.list_tasks(status=normalized_status)
        return {
            "tasks": [self._serialize_self_evolution_task(task) for task in tasks],
            "count": len(tasks),
        }

    async def get_self_evolution_task(self, task_id: str):
        task = self._self_evolution_queue.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Self-evolution task not found: {task_id}")
        return self._serialize_self_evolution_task(task)

    async def plan_self_evolution_task(self, request: dict | None = None):
        request = request or {}
        items = request.get("items")

        created = []
        if isinstance(items, list) and items:
            for item in items:
                title = str(item.get("title") or "").strip()
                if not title:
                    raise HTTPException(status_code=400, detail="Each task item must include a title.")
                request_metadata = self._request_task_metadata(item)
                task = self._self_evolution_queue.create_task(
                    title=title,
                    summary=str(item.get("summary", "")),
                    trace_id=str(item.get("trace_id") or uuid.uuid4()),
                    task_type=self._request_task_type(item, metadata=request_metadata),
                    source=str(item.get("source", "self_learning")),
                    priority=str(item.get("priority", "normal")),
                    metadata=request_metadata,
                    evidence=dict(item.get("evidence") or {}),
                    constraints=dict(item.get("constraints") or {}),
                )
                created.append(task)
        else:
            title = str(request.get("title") or "").strip()
            if not title:
                raise HTTPException(status_code=400, detail="title is required")
            request_metadata = self._request_task_metadata(request)
            created.append(
                self._self_evolution_queue.create_task(
                    title=title,
                    summary=str(request.get("summary", "")),
                    trace_id=str(request.get("trace_id") or uuid.uuid4()),
                    task_type=self._request_task_type(request, metadata=request_metadata),
                    source=str(request.get("source", "self_learning")),
                    priority=str(request.get("priority", "normal")),
                    metadata=request_metadata,
                    evidence=dict(request.get("evidence") or {}),
                    constraints=dict(request.get("constraints") or {}),
                )
            )

        await self._touch_gateway_activity(
            "self_evolution_plan",
            metadata=self._build_self_evolution_activity_metadata(created, action="plan"),
        )
        if created:
            self._record_supervisor_ui_activity(
                "tasks_planned",
                scene="planning",
                summary=f"Supervisor queued {len(created)} task(s).",
                metadata=self._build_self_evolution_activity_metadata(created, action="plan"),
            )

        return {
            "status": "planned",
            "tasks": [self._serialize_self_evolution_task(task) for task in created],
            "count": len(created),
        }

    async def decide_self_evolution_task(self, task_id: str, request: dict | None = None):
        request = request or {}
        task = self._self_evolution_queue.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Self-evolution task not found: {task_id}")

        normalized = self._normalize_self_evolution_decision(request.get("decision"))
        decision_context: Dict[str, Any] = {}

        if normalized is None or normalized == "auto":
            idle_window_request = dict(request.get("idle_window") or {})
            idle_window_request.setdefault("task_family", self._task_runtime_family(task))
            task_execution_kind = self._task_execution_kind(task)
            if task_execution_kind is not None:
                idle_window_request.setdefault("execution_kind", task_execution_kind)
            idle_window = await self.evaluate_idle_window(idle_window_request)
            normalized, auto_reason = self._build_self_evolution_auto_decision(
                task=task,
                idle_window=idle_window,
            )
            decision_context["idle_window"] = idle_window
            reason = str(request.get("reason") or auto_reason)
        else:
            reason = str(request.get("reason") or f"Task marked as {normalized} by supervisor decision.")

        if task.status == "cancelled":
            return {
                "status": "unchanged",
                "task": self._serialize_self_evolution_task(task),
                "reason": "Cancelled tasks are terminal and cannot be re-decided by the supervisor.",
            }

        actor = str(request.get("actor", "supervisor"))
        decision_id = str(request.get("decision_id") or uuid.uuid4())
        execution_request = None
        if normalized == "approved" and self._task_requires_execution_request(task):
            try:
                execution_request = self._build_self_evolution_execution_request(
                    task,
                    decision_id=decision_id,
                    actor=actor,
                    reason=reason,
                    decision_context=decision_context,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        updated_task = self._self_evolution_queue.update_status(
            task_id,
            status=normalized,
            decision_id=decision_id,
            actor=actor,
            reason=reason,
            context=decision_context,
            execution_request=execution_request,
        )

        await self._touch_gateway_activity(
            self._planning_activity_kind_for_task(task.task_type),
            metadata=self._build_self_evolution_activity_metadata(
                [updated_task],
                action="decision",
                extra={"status": normalized},
            ),
        )
        self._record_supervisor_ui_activity(
            "task_decided",
            scene="planning" if normalized != "approved" else "execution",
            summary=f"Task '{updated_task.title}' was marked {normalized}.",
            metadata={
                **self._task_activity_metadata(updated_task),
                "status": normalized,
            },
        )

        return {
            "status": normalized,
            "task": self._serialize_self_evolution_task(updated_task),
        }

    async def review_self_evolution_tasks(self, request: dict | None = None):
        request = request or {}
        statuses = request.get("statuses") or ["planned", "deferred", "paused"]
        normalized_statuses = []
        for status in statuses:
            normalized = self._normalize_self_evolution_decision(str(status))
            if normalized is None or normalized == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported review status: {status}")
            normalized_statuses.append(normalized)

        idle_window_request = dict(request.get("idle_window") or {})
        idle_window = await self.evaluate_idle_window(idle_window_request)
        requested_task_family = self._normalize_runtime_task_family(
            idle_window_request.get("execution_kind") or idle_window_request.get("task_family")
        )
        requested_governance_task_type = self._normalize_runtime_task_type(requested_task_family)
        review_decision = (
            idle_window.get("task_family_decisions", {}).get(requested_task_family)
            or idle_window.get("governance_task_type_decisions", {}).get(
                requested_governance_task_type
            )
            or idle_window["decisions"]
        )
        default_review_status = (
            "approved" if review_decision["eligible_for_execution"] else "deferred"
        )

        reviewed = []
        reviewed_statuses = []
        for task in self._self_evolution_queue.list_tasks():
            if task.status not in normalized_statuses or task.status == "cancelled":
                continue
            task_idle_window = idle_window
            task_family = self._task_runtime_family(task)
            if idle_window.get("task_family") != task_family:
                task_idle_window_request = dict(idle_window_request)
                task_idle_window_request["task_family"] = task_family
                task_execution_kind = self._task_execution_kind(task)
                task_idle_window_request.pop("execution_kind", None)
                if task_execution_kind is not None:
                    task_idle_window_request["execution_kind"] = task_execution_kind
                task_idle_window = await self.evaluate_idle_window(task_idle_window_request)
            target_status, default_reason = self._build_self_evolution_auto_decision(
                task=task,
                idle_window=task_idle_window,
            )
            execution_request = None
            if target_status == "approved":
                boundary = self._self_evolution_task_boundary(task)
                if boundary is not None and not boundary["ok"]:
                    violations = ", ".join(boundary["violations"])
                    actor = str(request.get("actor", "supervisor"))
                    decision_id = str(request.get("decision_id") or uuid.uuid4())
                    reason = (
                        "Task deferred because body self-evolution changes cross the "
                        f"child-agent boundary: {violations}."
                    )
                    updated = self._self_evolution_queue.update_status(
                        task.task_id,
                        status="deferred",
                        decision_id=decision_id,
                        actor=actor,
                        reason=reason,
                        context={"idle_window": task_idle_window, "evolution_boundary": boundary},
                    )
                    self._record_self_evolution_boundary_defer(
                        task=updated,
                        actor=actor,
                        reason=reason,
                        boundary=boundary,
                    )
                    reviewed.append(updated)
                    reviewed_statuses.append(updated.status)
                    continue
                if self._task_requires_execution_request(task):
                    decision_id = str(request.get("decision_id") or uuid.uuid4())
                    try:
                        execution_request = self._build_self_evolution_execution_request(
                            task,
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=str(request.get("reason") or default_reason),
                            decision_context={"idle_window": task_idle_window},
                        )
                    except ValueError:
                        updated = self._self_evolution_queue.update_status(
                            task.task_id,
                            status="deferred",
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=(
                                "Task deferred because approved execution handoff lacks required "
                                "lineage, target, or rollback evidence."
                            ),
                            context={"idle_window": task_idle_window},
                        )
                        reviewed.append(updated)
                        reviewed_statuses.append(updated.status)
                        continue
                else:
                    decision_id = str(request.get("decision_id") or uuid.uuid4())
            else:
                decision_id = str(request.get("decision_id") or uuid.uuid4())
            updated = self._self_evolution_queue.update_status(
                task.task_id,
                status=target_status,
                decision_id=decision_id,
                actor=str(request.get("actor", "supervisor")),
                reason=str(request.get("reason") or default_reason),
                context={"idle_window": task_idle_window},
                execution_request=execution_request,
            )
            reviewed.append(updated)
            reviewed_statuses.append(updated.status)

        if reviewed:
            unique_statuses = sorted(set(reviewed_statuses))
            self._record_supervisor_ui_activity(
                "tasks_reviewed",
                scene="planning" if "approved" not in unique_statuses else "execution",
                summary=f"Supervisor reviewed {len(reviewed)} task(s): {', '.join(unique_statuses)}.",
                metadata=self._build_self_evolution_activity_metadata(
                    reviewed,
                    action="review",
                    extra={
                        "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                    },
                ),
            )
            await self._touch_gateway_activity(
                "self_evolution_plan",
                metadata=self._build_self_evolution_activity_metadata(
                    reviewed,
                    action="review",
                    extra={
                        "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                    },
                ),
            )
        else:
            unique_statuses = []

        return {
            "status": "reviewed",
            "decision": default_review_status,
            "reviewed_statuses": unique_statuses,
            "tasks": [self._serialize_self_evolution_task(task) for task in reviewed],
            "count": len(reviewed),
            "idle_window": idle_window,
        }

    async def submit_self_learning_conclusion(self, request: dict | None = None):
        try:
            submission = SupervisorConclusionSubmission.model_validate(request or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        created = []
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
            task = self._self_evolution_queue.create_task(
                title=proposal.title,
                summary=proposal.summary,
                trace_id=str(submission.metadata.get("trace_id") or submission.conclusion_id or uuid.uuid4()),
                task_type=self._request_task_type(proposal_payload, metadata=proposal_metadata),
                source=proposal.source,
                priority=proposal.priority,
                metadata=proposal_metadata,
                evidence={
                    **submission.evidence,
                    **proposal.evidence,
                },
                constraints=dict(proposal.constraints),
            )
            created.append(self._serialize_self_evolution_task(task))

        if created:
            self._record_supervisor_ui_activity(
                "self_learning_submitted",
                scene="learning",
                summary=f"Self-learning submitted {len(created)} proposal task(s).",
                metadata={
                    "count": len(created),
                    "conclusion_id": submission.conclusion_id,
                    "task_ids": [task.get("task_id") for task in created],
                },
            )
            await self._touch_gateway_activity(
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

    async def _dispatch_self_evolution_execution_request(
        self,
        task: SelfEvolutionTask,
    ) -> Optional[Dict[str, Any]]:
        execution_request = task.execution_request
        if execution_request is None:
            return None

        task_metadata = dict(task.metadata or {})
        if task_metadata.get("execution_dispatched"):
            return None

        payload = execution_request.model_dump(mode="json")
        result = await self._execution_facade.execute_self_evolution_request(payload)
        self._self_evolution_queue.update_metadata(
            task.task_id,
            metadata={
                "execution_dispatched": True,
                "execution_result": result,
                "executed_at": datetime.utcnow().isoformat(),
            },
            execution_request=execution_request,
        )
        await self._touch_gateway_activity(
            "self_evolution_execute",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
            },
        )
        self._record_supervisor_ui_activity(
            "execution_dispatched",
            scene="execution",
            summary=f"Execution request dispatched for '{task.title}'.",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
                "result_status": result.get("status") if isinstance(result, dict) else None,
            },
        )
        return result

    async def _dispatch_self_learning_followup(
        self,
        task: SelfEvolutionTask,
    ) -> Optional[Dict[str, Any]]:
        task_metadata = dict(task.metadata or {})
        if task_metadata.get("execution_dispatched"):
            return None

        result = await self._execution_facade.execute_self_learning_followup(
            {"task": self._serialize_self_evolution_task(task)}
        )
        submission_result = None
        supervisor_submission = (
            result.get("supervisor_submission")
            if isinstance(result, dict)
            else None
        )
        if isinstance(supervisor_submission, dict):
            submission_result = await self.submit_self_learning_conclusion(supervisor_submission)

        self._self_evolution_queue.update_metadata(
            task.task_id,
            metadata={
                "execution_dispatched": True,
                "self_learning_dispatched": True,
                "execution_result": result,
                "self_learning_submission_result": submission_result,
                "executed_at": datetime.utcnow().isoformat(),
            },
        )
        await self._touch_gateway_activity(
            "self_learning",
            metadata={
                **self._task_activity_metadata(task),
                "action": "self_learning_followup_execute",
                "result_status": result.get("status") if isinstance(result, dict) else None,
            },
        )
        self._record_supervisor_ui_activity(
            "self_learning_completed",
            scene="learning",
            summary=f"Self-learning follow-up completed for '{task.title}'.",
            metadata={
                **self._task_activity_metadata(task),
                "result_status": result.get("status") if isinstance(result, dict) else None,
                "submitted_tasks": (
                    submission_result.get("count")
                    if isinstance(submission_result, dict)
                    else None
                ),
            },
        )
        return result

    async def _run_self_evolution_cycle(self) -> Dict[str, Any]:
        review_result = await self.review_self_evolution_tasks({})
        dispatched = []

        for task_payload in review_result.get("tasks", []):
            if task_payload.get("status") != "approved":
                continue

            task = self._self_evolution_queue.get_task(task_payload.get("task_id", ""))
            if task is None:
                continue

            if self._task_governance_type(task) == "self_learning":
                result = await self._dispatch_self_learning_followup(task)
                if result is not None:
                    dispatched.append(
                        {
                            "task_id": task.task_id,
                            "status": result.get("status"),
                        }
                    )
                continue

            if task.execution_request is None:
                continue

            result = await self._dispatch_self_evolution_execution_request(task)
            if result is not None:
                dispatched.append(
                    {
                        "task_id": task.task_id,
                        "status": result.get("status"),
                    }
                )

        return {
            "reviewed": review_result.get("count", 0),
            "dispatched": dispatched,
        }
