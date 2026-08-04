"""Explicit orchestration owner for one autonomous Supervisor cycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from systems.supervisor.endogenous_drive_cycle import (
    EndogenousDriveCycleContext,
    run_endogenous_drive_cycle,
)


JsonDict = Dict[str, Any]
RunAsync = Callable[..., Awaitable[JsonDict]]
RecordActivity = Callable[..., None]
UpdateSchedule = Callable[[datetime, datetime], None]
Now = Callable[[], datetime]


class AutonomousCycleService:
    """Coordinate drive, planning, review, and handoff through explicit ports."""

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
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def run_drive_cycle(self) -> JsonDict:
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
                "handed_off": [
                    dict(item) if isinstance(item, dict) else {"task_id": str(item)}
                    for item in cycle_result.get("handed_off", [])
                ],
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

        total_handed_off = len(phases.get("review", {}).get("handed_off", []))
        total_planned = phases.get("drive", {}).get("planned", 0)
        self._record_ui_activity(
            "autonomous_cycle_completed",
            scene="handoff" if total_handed_off > 0 else "planning",
            summary=(
                f"自主链路一轮完成：新增 {total_planned} 个候选，"
                f"{total_handed_off} 个链路项已进入自主交接。"
            ),
            metadata={
                "phases": {
                    key: {field: value for field, value in phase.items() if field != "task_ids"}
                    for key, phase in phases.items()
                },
                "total_planned": total_planned,
                "total_handed_off": total_handed_off,
                "focus": focus or None,
            },
        )

        return {
            "status": "completed",
            "phases": phases,
            "summary": {
                "planned": total_planned,
                "handed_off": total_handed_off,
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
