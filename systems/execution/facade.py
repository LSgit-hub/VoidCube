from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from systems.supervisor.task_queue import SelfEvolutionExecutionRequest


@dataclass(slots=True)
class VoidCubeExecutionFacade:
    """Stable execution-facing facade over the canonical execution adapters."""

    agent_lifecycle: Any
    watch_window: Any
    body_lifecycle: Any
    body_upgrade: Any
    memory_maintenance: Any

    async def start_managed_agent(self, request: dict) -> Dict[str, Any]:
        return await self.agent_lifecycle.start_managed_agent(request)

    async def stop_agent(self, instance_id: str) -> Dict[str, Any]:
        return await self.agent_lifecycle.stop_agent(instance_id)

    async def activate_body(self, request: dict) -> Dict[str, Any]:
        return await self.agent_lifecycle.activate_body(request)

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

    async def execute_self_evolution_request(self, request: dict) -> Dict[str, Any]:
        execution_request = SelfEvolutionExecutionRequest.model_validate(request)
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

        if execution_request.kind in {"body_upgrade", "body_switch"}:
            # body_switch is a distinct operation (baseline §7.4) — the
            # execution_request already carries the kind, so the adapter
            # can differentiate without extra payload keys.
            result = await self.body_upgrade.execute_body_upgrade(payload)
            return {
                "status": "formal_self_evolution_executed",
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
                "status": "formal_self_evolution_executed",
                "execution_metadata": execution_metadata,
                "execution_request": execution_request_payload,
                "result": result,
            }

        return {
            "status": "formal_self_evolution_recorded",
            "execution_metadata": execution_metadata,
            "execution_request": execution_request_payload,
        }

    async def record_body_probe_report(self, request: dict) -> Dict[str, Any]:
        return await self.body_lifecycle.record_body_probe_report(request)

    async def run_body_probe(self, request: dict) -> Dict[str, Any]:
        return await self.body_lifecycle.run_body_probe(request)

    async def trigger_memory_compression(self, request: dict | None = None) -> Dict[str, Any]:
        return await self.memory_maintenance.trigger_memory_compression(request)

