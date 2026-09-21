from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from ..supervisor.autonomous_chain_store import AutonomousChainExecutionRequest


@dataclass(slots=True)
class VoidCubeExecutionFacade:
    """Stable execution-facing facade over the canonical execution adapters."""

    watch_window: Any
    body_lifecycle: Any
    body_upgrade: Any
    memory_maintenance: Any
    governor_review: Any = None
    supervisor: Any = None

    def get_watch_window_status(self) -> Dict[str, Any]:
        return self.watch_window.get_watch_window_status()

    async def evaluate_watch_window(self, request: dict | None = None) -> Dict[str, Any]:
        return await self.watch_window.evaluate_watch_window(request)

    async def prepare_body_slot(self, slot_id: str, request: dict | None = None) -> Dict[str, Any]:
        return await self.body_lifecycle.prepare_body_slot(slot_id, request)

    def get_body_registry(self) -> Dict[str, Any]:
        return self.body_lifecycle.get_body_registry()

    def get_active_body_target(self) -> Dict[str, Any]:
        return self.body_lifecycle.get_active_body_target()

    def list_body_slots(self) -> Dict[str, Any]:
        return self.body_lifecycle.list_body_slots()

    def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        return self.body_lifecycle.get_body_slot(slot_id)

    async def mark_body_candidate(self, slot_id: str, request: dict | None = None) -> Dict[str, Any]:
        return await self.body_lifecycle.mark_body_candidate(slot_id, request)

    async def execute_body_upgrade(self, request: dict | None = None) -> Dict[str, Any]:
        return await self.body_upgrade.execute_body_upgrade(request)

    def review_body(self, request: Any) -> Dict[str, Any]:
        if self.governor_review is None:
            raise RuntimeError("Governor review adapter is not configured")
        return self.governor_review.execute_governor_request(request)

    async def confirm_body_switch(self, request: dict | None = None) -> Dict[str, Any]:
        result = await self.body_upgrade.confirm_body_switch(request)
        if self.supervisor is not None:
            self.supervisor._autonomous_body_switch_consent_service.reconcile(result)
        return result

    @staticmethod
    def _execution_phase(result: Any) -> str:
        """Normalize adapter output without treating a handoff as completion."""
        raw = str(result.get("status") or "").strip().lower() if isinstance(result, dict) else ""
        if "consent" in raw:
            return "awaiting_user_consent"
        if raw in {"completed", "complete", "compressed", "body_switch_activated", "succeeded", "success"}:
            return "completed"
        if raw in {
            "failed",
            "error",
            "rejected",
            "cancelled",
            "degraded",
            # The body-upgrade adapter halts after a failed probe or review;
            # this is a terminal execution failure, not an asynchronous handoff.
            "upgrade_halted",
        } or raw.endswith("_failed"):
            return "failed"
        if raw in {"running", "accepted", "queued", "in_progress", "pending"}:
            return "running"
        # An adapter that does not provide a terminal status is a handoff,
        # never proof that execution completed.
        return "running"

    async def _touch_execution_activity(
        self,
        execution_metadata: Dict[str, Any],
        *,
        phase: str,
        result: Any = None,
    ) -> None:
        touch = getattr(self.supervisor, "_touch_gateway_activity", None)
        if not callable(touch):
            return
        metadata = dict(execution_metadata)
        metadata["execution_phase"] = phase
        if isinstance(result, dict):
            metadata["adapter_status"] = str(result.get("status") or "")
        try:
            await touch(
                "autonomous_chain_execute",
                source_service=execution_metadata.get("target_service") or "executor",
                metadata=metadata,
            )
        except Exception:
            # Activity is observability; task writeback remains authoritative.
            return

    async def execute_autonomous_chain_request(self, request: dict) -> Dict[str, Any]:
        execution_request = AutonomousChainExecutionRequest.model_validate(request)
        execution_request_payload = execution_request.model_dump(mode="json")
        execution_metadata = {
            "request_id": execution_request.request_id,
            "trace_id": execution_request.trace_id,
            "task_id": execution_request.task_id,
            "governance_task_type": execution_request.governance_task_type,
            "task_family": execution_request.task_family,
            "execution_kind": execution_request.execution_kind,
            "decision_id": execution_request.decision_id,
            "kind": execution_request.kind,
            "source_actor": execution_request.source_actor,
            "source_service": execution_request.source_service or "supervisor",
            "target_service": execution_request.target_service or "executor",
            "session_id": execution_request.session_id,
        }
        payload = {
            "slot_id": execution_request.target_slot_id,
            "execution_request": execution_request_payload,
        }

        task_state = getattr(self.supervisor, "_autonomous_task_state", None)
        task_store = getattr(self.supervisor, "_autonomous_chain_store", None)
        task = task_store.get_task(execution_request.task_id) if task_store is not None else None
        lease = None
        if task_state is not None and task_store is not None:
            if task is None:
                return {
                    "status": "autonomous_chain_execution_rejected",
                    "reason": "canonical_task_not_found",
                    "execution_metadata": execution_metadata,
                    "execution_request": execution_request_payload,
                }
            owner = execution_request.session_id or execution_request.source_actor or "executor"
            if str(task.status) in {"approved", "retry"}:
                task = task_state.claim_execution(
                    task.task_id,
                    owner_session_id=owner,
                    actor=execution_request.target_service or "executor",
                    reason="Executor claimed the approved autonomous-chain request.",
                    context={"execution_request": execution_request_payload},
                )
            elif str(task.status) == "running" and getattr(task.execution_lease, "attempt_id", None):
                # A caller may be retrying an already claimed request. Reuse its
                # current lease; never create a second generation implicitly.
                pass
            else:
                return {
                    "status": "autonomous_chain_execution_rejected",
                    "reason": f"canonical_task_not_claimable:{task.status}",
                    "execution_metadata": execution_metadata,
                    "execution_request": execution_request_payload,
                }
            lease = task.execution_lease
            task_state.update_metadata(
                task.task_id,
                metadata={"execution_request": execution_request_payload},
                execution_request=execution_request,
            )

        result: Any = None
        try:
            if execution_request.kind == "general_self_evolution":
                result = await self.body_upgrade.execute_body_upgrade(payload)
            elif execution_request.kind == "memory_maintenance":
                result = await self.memory_maintenance.trigger_memory_compression(
                    {"execution_request": execution_request_payload}
                )
        except Exception as exc:
            result = {
                "status": "failed",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

        phase = self._execution_phase(result)
        if task_state is not None and task is not None and lease is not None:
            context = {
                "execution_outcome_status": "succeeded" if phase == "completed" else "failed" if phase == "failed" else "queued",
                "execution_result": result if isinstance(result, dict) else {"value": str(result)},
                "execution_request": execution_request_payload,
                "memory_write_status": (result or {}).get("memory_write_status", "unknown") if isinstance(result, dict) else "unknown",
            }
            if phase == "completed":
                task = task_state.finalize_execution(
                    task.task_id,
                    generation=lease.generation,
                    attempt_id=str(lease.attempt_id),
                    status="completed",
                    actor=execution_request.target_service or "executor",
                    reason="Autonomous-chain executor completed the request.",
                    context=context,
                )
            elif phase == "failed":
                task = task_state.finalize_execution(
                    task.task_id,
                    generation=lease.generation,
                    attempt_id=str(lease.attempt_id),
                    status="failed",
                    actor=execution_request.target_service or "executor",
                    reason="Autonomous-chain executor failed the request.",
                    context=context,
                )
            elif phase == "awaiting_user_consent":
                task = task_state.update_status(
                    task.task_id,
                    status="awaiting_user_consent",
                    actor=execution_request.target_service or "executor",
                    reason="Execution is waiting for user consent.",
                    context=context,
                    execution_request=execution_request,
                    event_type="execution_awaiting_user_consent",
                )
            else:
                task_state.update_metadata(task.task_id, metadata=context)
            await self._touch_execution_activity(execution_metadata, phase=phase, result=result)

        if task_state is None or task is None:
            # Standalone executor instances have no canonical task owner. They
            # expose a handoff result, not a false completed result.
            phase_status = "autonomous_chain_execution_handoff"
        else:
            phase_status = {
                "completed": "autonomous_chain_execution_completed",
                "failed": "autonomous_chain_execution_failed",
                "awaiting_user_consent": "autonomous_chain_execution_awaiting_user_consent",
            }.get(phase, "autonomous_chain_execution_running")
        return {
            "status": phase_status,
            "execution_phase": phase,
            "execution_metadata": execution_metadata,
            "execution_request": execution_request_payload,
            "result": result,
            "task_status": str(task.status) if task is not None else None,
        }

    async def record_body_probe_report(self, request: dict) -> Dict[str, Any]:
        return await self.body_lifecycle.record_body_probe_report(request)

    async def run_body_probe(self, request: dict) -> Dict[str, Any]:
        return await self.body_lifecycle.run_body_probe(request)

    async def rollback_body_improvement(
        self,
        slot_id: str,
        request: dict | None = None,
    ) -> Dict[str, Any]:
        return await self.body_lifecycle.rollback_body_improvement(slot_id, request)

    async def trigger_memory_compression(self, request: dict | None = None) -> Dict[str, Any]:
        return await self.memory_maintenance.trigger_memory_compression(request)

    def get_slot_health(self, slot_id: str) -> Dict[str, Any]:
        try:
            slot_meta = self.body_lifecycle._body_registry.load_slot_meta(slot_id)
            return {
                "slot_id": slot_meta.slot_id,
                "health_score": slot_meta.health_score,
                "improvement_count": slot_meta.improvement_count,
                "last_improvement_at": slot_meta.last_improvement_at,
                "current_healthy_commit": slot_meta.current_healthy_commit,
                "previous_healthy_commit": slot_meta.previous_healthy_commit,
                "rollback_in_progress": slot_meta.rollback_in_progress,
                "last_improvement_rollback": slot_meta.last_improvement_rollback,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_slot_health_history(self, slot_id: str) -> Dict[str, Any]:
        try:
            slot_meta = self.body_lifecycle._body_registry.load_slot_meta(slot_id)
            return {
                "slot_id": slot_meta.slot_id,
                "health_history": slot_meta.health_history,
            }
        except Exception as e:
            return {"error": str(e)}

    def reset_slot_health(self, slot_id: str) -> Dict[str, Any]:
        try:
            slot_meta = self.body_lifecycle._body_registry.load_slot_meta(slot_id)
            slot_meta.health_score = 0.0
            slot_meta.health_history = []
            slot_meta.improvement_count = 0
            slot_meta.last_improvement_at = None
            slot_meta.current_healthy_commit = None
            slot_meta.previous_healthy_commit = None
            slot_meta.decay_applied_at = None
            slot_meta.rollback_in_progress = None
            slot_meta.last_improvement_rollback = None
            self.body_lifecycle._body_registry.save_slot_meta(slot_meta)
            return {"status": "ok", "slot_id": slot_id}
        except Exception as e:
            return {"error": str(e)}

    async def submit_body_improvement_report(self, request: dict) -> Dict[str, Any]:
        if not self.supervisor:
            return {"status": "error", "reason": "supervisor_not_available"}
        try:
            return await self.supervisor._body_improvement_review_service.review(request)
        except Exception as e:
            return {"status": "error", "reason": str(e)}

