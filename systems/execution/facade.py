from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from systems.supervisor.autonomous_chain_store import AutonomousChainExecutionRequest


@dataclass(slots=True)
class VoidCubeExecutionFacade:
    """Stable execution-facing facade over the canonical execution adapters."""

    watch_window: Any
    body_lifecycle: Any
    body_upgrade: Any
    memory_maintenance: Any
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

        if execution_request.kind == "general_self_evolution":
            # General self-evolution execution routed through body upgrade adapter.
            result = await self.body_upgrade.execute_body_upgrade(payload)
            return {
                "status": "autonomous_chain_execution_executed",
                "execution_metadata": execution_metadata,
                "execution_request": execution_request_payload,
                "result": result,
            }

        if execution_request.kind == "memory_maintenance":
            result = await self.memory_maintenance.trigger_memory_compression(
                {
                    "execution_request": execution_request_payload,
                }
            )
            return {
                "status": "autonomous_chain_execution_executed",
                "execution_metadata": execution_metadata,
                "execution_request": execution_request_payload,
                "result": result,
            }

        return {
            "status": "autonomous_chain_execution_recorded",
            "execution_metadata": execution_metadata,
            "execution_request": execution_request_payload,
        }

    async def record_body_probe_report(self, request: dict) -> Dict[str, Any]:
        return await self.body_lifecycle.record_body_probe_report(request)

    async def run_body_probe(self, request: dict) -> Dict[str, Any]:
        return await self.body_lifecycle.run_body_probe(request)

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
                "previous_healthy_commit": slot_meta.previous_healthy_commit,
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
            slot_meta.previous_healthy_commit = None
            slot_meta.decay_applied_at = None
            self.body_lifecycle._body_registry.save_slot_meta(slot_meta)
            return {"status": "ok", "slot_id": slot_id}
        except Exception as e:
            return {"error": str(e)}

    async def submit_body_improvement_report(self, request: dict) -> Dict[str, Any]:
        if not self.supervisor:
            return {"status": "error", "reason": "supervisor_not_available"}
        try:
            return await self.supervisor._planning_runtime._review_body_improvement(request)
        except Exception as e:
            return {"status": "error", "reason": str(e)}

