"""Explicit orchestration owner for one autonomous Supervisor cycle."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from .endogenous_drive_cycle import (
    EndogenousDriveCycleContext,
    run_endogenous_drive_cycle,
)


JsonDict = Dict[str, Any]
RunAsync = Callable[..., Awaitable[JsonDict]]
RecordActivity = Callable[..., None]
UpdateSchedule = Callable[[datetime, datetime], None]
Now = Callable[[], datetime]
logger = logging.getLogger("supervisor")


class AutonomousCycleService:
    """Coordinate drive, planning, review, and employee dispatch."""

    def __init__(
        self,
        *,
        runtime_config: Any,
        evaluate_drive: RunAsync,
        drive_input_fields_from_evaluation: Callable[..., Dict[str, JsonDict]],
        load_drive_history: Callable[[], JsonDict],
        load_governance_events: Callable[[], JsonDict],
        load_cognition_state: Callable[[], JsonDict],
        persist_evaluation: Callable[..., JsonDict],
        restore_evaluation_snapshots: Callable[..., None],
        lm_generation_application_state: Callable[[], Any],
        plan_autonomous_chain_task: RunAsync,
        record_ui_activity: RecordActivity,
        touch_gateway_activity: Callable[..., Awaitable[None]],
        run_review_cycle: RunAsync,
        update_drive_schedule: UpdateSchedule,
        update_review_schedule: UpdateSchedule,
        schedule_candidate_generation: RunAsync | None = None,
        register_candidate_generation_request: Callable[[JsonDict], JsonDict] | None = None,
        now: Optional[Now] = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._evaluate_drive = evaluate_drive
        self._drive_input_fields_from_evaluation = drive_input_fields_from_evaluation
        self._load_drive_history = load_drive_history
        self._load_governance_events = load_governance_events
        self._load_cognition_state = load_cognition_state
        self._persist_evaluation = persist_evaluation
        self._restore_evaluation_snapshots = restore_evaluation_snapshots
        self._lm_generation_application_state = lm_generation_application_state
        self._plan_autonomous_chain_task = plan_autonomous_chain_task
        self._record_ui_activity = record_ui_activity
        self._touch_gateway_activity = touch_gateway_activity
        self._run_review_cycle = run_review_cycle
        self._update_drive_schedule = update_drive_schedule
        self._update_review_schedule = update_review_schedule
        self._schedule_candidate_generation = schedule_candidate_generation
        self._register_candidate_generation_request = register_candidate_generation_request
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._drive_lock = asyncio.Lock()

    async def run_drive_cycle(self) -> JsonDict:
        if self._drive_lock.locked():
            logger.info(
                "Skipping endogenous-drive cycle because another cycle is already running."
            )
            return {
                "status": "skipped",
                "skipped": "cycle_already_running",
                "planned": 0,
                "tasks": [],
            }

        async with self._drive_lock:
            result = await self._run_drive_cycle_locked()

        if self._schedule_candidate_generation is not None:
            try:
                candidate_result = await self._schedule_candidate_generation(
                    mode="automatic"
                )
            except Exception as exc:
                candidate_result = {
                    "status": "error",
                    "error_code": type(exc).__name__,
                }
            result = dict(result)
            result["candidate_generation"] = candidate_result
        return result

    async def _run_drive_cycle_locked(self) -> JsonDict:
        context = EndogenousDriveCycleContext(
            runtime_config=self._runtime_config,
            evaluate_drive=self._evaluate_drive,
            drive_input_fields_from_evaluation=self._drive_input_fields_from_evaluation,
            load_drive_history=self._load_drive_history,
            load_governance_events=self._load_governance_events,
            load_cognition_state=self._load_cognition_state,
            persist_evaluation=self._persist_evaluation,
            restore_evaluation_snapshots=self._restore_evaluation_snapshots,
            lm_generation_application_state=self._lm_generation_application_state,
            plan_autonomous_chain_task=self._plan_autonomous_chain_task,
            record_ui_activity=self._record_ui_activity,
            touch_gateway_activity=self._touch_gateway_activity,
            register_candidate_generation_request=(
                self._register_candidate_generation_request
            ),
        )
        return await run_endogenous_drive_cycle(context=context)

    async def run_review_cycle(self, request: Optional[JsonDict] = None) -> JsonDict:
        return await self._run_review_cycle(dict(request or {}))

    async def run(self, request: Optional[JsonDict] = None) -> JsonDict:
        request = dict(request or {})
        focus = str(request.get("focus") or "").strip()
        phases: JsonDict = {}

        try:
            drive_result = await self.run_drive_cycle()
            phases["drive"] = {
                "status": drive_result.get("status"),
                "planned": drive_result.get("planned", 0),
                "task_ids": [
                    task.get("task_id")
                    for task in drive_result.get("tasks", [])
                    if isinstance(task, dict)
                ],
            }
            if isinstance(drive_result.get("candidate_generation"), dict):
                phases["candidate_generation"] = dict(
                    drive_result["candidate_generation"]
                )
            now = self._now()
            self._update_drive_schedule(
                now,
                now + timedelta(
                    seconds=self._runtime_config.endogenous_drive_interval
                ),
            )
        except Exception as exc:
            phases["drive"] = {"status": "error", "error": str(exc)}

        try:
            cycle_result = await self.run_review_cycle()
            phases["review"] = {
                "reviewed": cycle_result.get("reviewed", 0),
                "dispatched": [
                    dict(item) if isinstance(item, dict) else {"task_id": str(item)}
                    for item in cycle_result.get("dispatched", [])
                ],
                "employee_updates": list(cycle_result.get("employee_updates", [])),
                "governance_consumption": dict(
                    cycle_result.get("governance_consumption") or {}
                ),
                "alignment_consumption": dict(
                    cycle_result.get("alignment_consumption") or {}
                ),
                "truthfulness_consumption": dict(
                    cycle_result.get("truthfulness_consumption") or {}
                ),
            }
            now = self._now()
            self._update_review_schedule(
                now,
                now + timedelta(
                    seconds=self._runtime_config.autonomous_chain_review_interval
                ),
            )
        except Exception as exc:
            phases["review"] = {"status": "error", "error": str(exc)}

        total_dispatched = len(phases.get("review", {}).get("dispatched", []))
        total_planned = phases.get("drive", {}).get("planned", 0)
        self._record_ui_activity(
            "autonomous_cycle_completed",
            scene="handoff" if total_dispatched > 0 else "planning",
            summary=(
                f"自主链路一轮完成：新增 {total_planned} 个候选，"
                f"{total_dispatched} 个链路项已派给员工代理。"
            ),
            metadata={
                "phases": {
                    key: {field: value for field, value in phase.items() if field != "task_ids"}
                    for key, phase in phases.items()
                },
                "total_planned": total_planned,
                "total_dispatched": total_dispatched,
                "focus": focus or None,
            },
        )

        return {
            "status": "completed",
            "phases": phases,
            "summary": {
                "planned": total_planned,
                "dispatched": total_dispatched,
                "governance_consumed": int(
                    phases.get("review", {})
                    .get("governance_consumption", {})
                    .get("count", 0)
                ),
                "alignment_consumed": int(
                    phases.get("review", {})
                    .get("alignment_consumption", {})
                    .get("count", 0)
                ),
                "truthfulness_consumed": int(
                    phases.get("review", {})
                    .get("truthfulness_consumption", {})
                    .get("count", 0)
                ),
                "focus": focus or None,
            },
        }


__all__ = ["AutonomousCycleService"]
