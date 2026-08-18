from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from systems.body_registry import BodyRegistryManager, BodySlotMeta
from systems.governor import GovernorAction, GovernorResponse
from systems.probe import ProbeReport
from systems.runtime_task_profile import derive_runtime_task_profile

LifecycleExecutionStatus = Literal["applied", "noop", "failed"]


class LifecycleActionResult(BaseModel):
    action_type: str
    status: LifecycleExecutionStatus
    slot_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class LifecycleExecutionReport(BaseModel):
    """Execution-side report for a governor-approved body lifecycle decision."""

    decision: str
    action_results: List[LifecycleActionResult] = Field(default_factory=list)
    writeback_events: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_task_profile: Optional[Dict[str, Any]] = None


class BodyLifecycleExecutor:
    """Execution-side adapter for deterministic body actions.

    The governor decides whether a body transition is allowed; this class only
    applies the approved state changes against the body registry.
    """

    def __init__(self, registry: BodyRegistryManager) -> None:
        self.registry = registry

    def apply_governor_response(self, response: GovernorResponse) -> LifecycleExecutionReport:
        action_results: list[LifecycleActionResult] = []
        for action in response.required_actions:
            action_results.append(self.execute_action(action))

        writeback_events = [
            event.model_dump(mode="json")
            for event in response.writeback_events
        ]
        return LifecycleExecutionReport(
            decision=response.decision,
            action_results=action_results,
            writeback_events=writeback_events,
            runtime_task_profile=self._extract_runtime_task_profile(
                writeback_events=writeback_events,
                actions=response.required_actions,
            ),
        )

    def execute_action(self, action: GovernorAction) -> LifecycleActionResult:
        slot_id = action.slot_id
        payload = dict(action.payload or {})

        if action.action_type == "issue_probe_lease":
            if not slot_id:
                return self._failed(action, "Probe lease requires a slot_id.")
            slot_meta = self.registry.load_slot_meta(slot_id)
            if slot_meta.body_state == "shell":
                self.registry.mark_candidate(slot_id)
            meta = self.registry.start_probe(
                slot_id,
                lease=str(payload.get("lease", "probe")),
            )
            return self._applied(
                action,
                slot_id=meta.slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                    "body_state": meta.body_state,
                    "lease": meta.lease,
                    },
                    payload,
                ),
            )

        if action.action_type == "activate_slot":
            if not slot_id:
                return self._failed(action, "Activation requires a slot_id.")
            registry = self.registry.activate_slot(
                slot_id,
                lease=str(payload.get("lease", "active")),
                watch_window_seconds=int(payload.get("watch_window_seconds", 300)),
                stable_window_days=int(payload.get("stable_window_days", 3)),
                stable_health_checks=int(payload.get("stable_health_checks", 3)),
                reason=str(payload.get("reason", "governor_approved")),
                runtime_task_profile=payload.get("runtime_task_profile"),
            )
            slot_meta = self.registry.load_slot_meta(slot_id)
            return self._applied(
                action,
                slot_id=slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                    "active_slot": registry.active_slot,
                    "retired_slot": registry.retired_slot,
                    "watch_window_status": registry.watch_window.status,
                    "active_body_pointer_path": str(self.registry.active_body_pointer_path()),
                    "active_ref": slot_meta.active_ref,
                    "active_commit": slot_meta.active_commit,
                    "candidate_branch": slot_meta.candidate_branch,
                    "candidate_commit": slot_meta.candidate_commit,
                    },
                    payload,
                ),
            )

        if action.action_type == "await_user_consent":
            if not slot_id:
                return self._failed(action, "User-consent gate requires a slot_id.")
            registry = self.registry.await_user_consent(
                slot_id,
                reason=str(payload.get("reason", "governor_approved_pending_user_consent")),
                request_payload=payload,
                runtime_task_profile=payload.get("runtime_task_profile"),
            )
            slot_meta = self.registry.load_slot_meta(slot_id)
            return self._applied(
                action,
                slot_id=slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                    "active_slot": registry.active_slot,
                    "body_state": slot_meta.body_state,
                    "requires_user_consent": True,
                    "switch_consent_requested_at": (
                        slot_meta.switch_consent_requested_at.isoformat()
                        if slot_meta.switch_consent_requested_at
                        else None
                    ),
                    },
                    payload,
                ),
            )

        if action.action_type == "restore_retired_slot":
            if not slot_id:
                return self._failed(action, "Rollback restore requires a slot_id.")
            registry = self.registry.activate_slot(
                slot_id,
                lease=str(payload.get("lease", "active")),
                watch_window_seconds=int(payload.get("watch_window_seconds", 300)),
                stable_window_days=int(payload.get("stable_window_days", 3)),
                stable_health_checks=int(payload.get("stable_health_checks", 3)),
                reason=str(payload.get("reason", "rollback_restore")),
                runtime_task_profile=payload.get("runtime_task_profile"),
            )
            slot_meta = self.registry.load_slot_meta(slot_id)
            return self._applied(
                action,
                slot_id=slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                    "active_slot": registry.active_slot,
                    "retired_slot": registry.retired_slot,
                    "restored_from_failure": payload.get("failed_body_id"),
                    "active_body_pointer_path": str(self.registry.active_body_pointer_path()),
                    "active_ref": slot_meta.active_ref,
                    "active_commit": slot_meta.active_commit,
                    },
                    payload,
                ),
            )

        if action.action_type == "restore_healthy_commit":
            if not slot_id:
                return self._failed(action, "Healthy-commit restore requires a slot_id.")
            try:
                slot_meta = self.registry.restore_previous_healthy_commit(
                    slot_id,
                    expected_current_commit=payload.get("expected_current_commit"),
                    request_id=payload.get("request_id"),
                    reason=str(payload.get("reason") or "destructive_body_improvement"),
                )
            except (FileNotFoundError, ValueError) as exc:
                return self._failed(action, str(exc))
            return self._applied(
                action,
                slot_id=slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                        "body_state": slot_meta.body_state,
                        "lease": slot_meta.lease,
                        "rollback": dict(slot_meta.rollback_in_progress or {}),
                    },
                    payload,
                ),
            )

        if action.action_type == "recycle_retired_slot":
            if not slot_id:
                return self._failed(action, "Recycle action requires a slot_id.")
            registry = self.registry.recycle_retired_slot(
                slot_id,
                source_slot_id=payload.get("source_slot_id"),
                source_path=payload.get("source_path"),
            )
            slot_meta = self.registry.load_slot_meta(slot_id)
            return self._applied(
                action,
                slot_id=slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                    "shell_slot": registry.shell_slot,
                    "body_state": slot_meta.body_state,
                    "materialized_from": slot_meta.materialized_from,
                    },
                    payload,
                ),
            )

        if action.action_type == "abandon_candidate":
            if not slot_id:
                return self._failed(action, "Abandon candidate requires a slot_id.")
            reason = str(payload.get("reason", "probe_failed"))
            slot_meta = self.registry.abandon_candidate(
                slot_id,
                source_slot_id=payload.get("source_slot_id"),
                source_path=payload.get("source_path"),
            )
            registry = self.registry.load_registry()
            return self._applied(
                action,
                slot_id=slot_id,
                details=self._details_with_runtime_task_profile(
                    {
                    "previous_state": "probe",
                    "body_state": slot_meta.body_state,
                    "shell_slot": registry.shell_slot,
                    "abandon_reason": reason,
                    },
                    payload,
                ),
            )

        if action.action_type == "record_evolution_event":
            return self._noop(
                action,
                details=self._details_with_runtime_task_profile(
                    {
                    "notes": action.notes or "No deterministic state change required.",
                    },
                    payload,
                ),
            )

        return self._failed(action, f"Unsupported lifecycle action type: {action.action_type}")

    def current_slot_meta(self, slot_id: str) -> BodySlotMeta:
        return self.registry.load_slot_meta(slot_id)

    def record_probe_report(
        self,
        slot_id: str,
        report: ProbeReport | Dict[str, Any],
    ) -> LifecycleActionResult:
        if isinstance(report, ProbeReport):
            normalized = report
        else:
            normalized = ProbeReport.model_validate(report)
        if normalized.slot_id != slot_id:
            return LifecycleActionResult(
                action_type="record_probe_report",
                status="failed",
                slot_id=slot_id,
                details={
                    "reason": (
                        f"Probe report slot_id {normalized.slot_id!r} does not match "
                        f"target slot {slot_id!r}."
                    )
                },
            )
        self.registry.write_probe_report(slot_id, normalized.model_dump(mode="json"))
        return LifecycleActionResult(
            action_type="record_probe_report",
            status="applied",
            slot_id=slot_id,
            details={
                "overall_passed": normalized.overall_passed,
                "overall_status": normalized.overall_status,
                "failed_count": normalized.failed_count,
            },
        )

    def _applied(
        self,
        action: GovernorAction,
        *,
        slot_id: Optional[str],
        details: Dict[str, Any],
    ) -> LifecycleActionResult:
        return LifecycleActionResult(
            action_type=action.action_type,
            status="applied",
            slot_id=slot_id,
            details=details,
        )

    def _noop(
        self,
        action: GovernorAction,
        *,
        details: Dict[str, Any],
    ) -> LifecycleActionResult:
        return LifecycleActionResult(
            action_type=action.action_type,
            status="noop",
            slot_id=action.slot_id,
            details=details,
        )

    def _failed(self, action: GovernorAction, reason: str) -> LifecycleActionResult:
        return LifecycleActionResult(
            action_type=action.action_type,
            status="failed",
            slot_id=action.slot_id,
            details={"reason": reason},
        )

    def _details_with_runtime_task_profile(
        self,
        details: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        runtime_task_profile = self._normalized_runtime_task_profile(payload)
        if runtime_task_profile is None:
            return details
        merged = dict(details)
        merged["runtime_task_profile"] = runtime_task_profile
        for key in ("governance_task_type", "task_family", "execution_kind"):
            value = runtime_task_profile.get(key)
            if value is not None:
                merged[key] = value
        return merged

    def _extract_runtime_task_profile(
        self,
        *,
        writeback_events: List[Dict[str, Any]],
        actions: List[GovernorAction],
    ) -> Optional[Dict[str, Any]]:
        for event in writeback_events:
            payload = dict(event.get("payload") or {})
            runtime_task_profile = self._normalized_runtime_task_profile(payload)
            if runtime_task_profile is not None:
                return runtime_task_profile
        for action in actions:
            runtime_task_profile = self._normalized_runtime_task_profile(action.payload)
            if runtime_task_profile is not None:
                return runtime_task_profile
        return None

    @staticmethod
    def _normalized_runtime_task_profile(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Canonical runtime task profile — delegates to shared helper."""
        runtime_task_profile = payload.get("runtime_task_profile")
        if not isinstance(runtime_task_profile, dict):
            return None
        return {
            "task_type": runtime_task_profile.get("task_type"),
            **derive_runtime_task_profile(
                task_type=runtime_task_profile.get("task_type"),
                governance_task_type=runtime_task_profile.get("governance_task_type"),
                task_family=runtime_task_profile.get("task_family"),
                execution_kind=runtime_task_profile.get("execution_kind"),
                kind=payload.get("kind"),
                default_task_family="general_self_evolution",
            ),
        }
