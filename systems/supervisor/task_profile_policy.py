"""Pure task profile policy used by the planning runtime."""

from __future__ import annotations

from typing import Any, Dict, Optional

from systems.runtime_task_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_family,
    normalize_runtime_task_type,
    resolve_broad_task_type,
)
from systems.supervisor.autonomous_chain_store import AutonomousChainTask


class TaskProfilePolicy:
    """Derive task taxonomy without accessing Supervisor or persistent state."""

    def normalize_family(self, value: Optional[str]) -> str:
        return str(
            normalize_runtime_task_family(value, default="general_self_evolution")
        )

    def normalize_type(self, value: Optional[str]) -> str:
        return str(normalize_runtime_task_type(value, default="self_evolution"))

    def runtime_profile(self, task: AutonomousChainTask) -> Dict[str, Any]:
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

    def runtime_family(self, task: AutonomousChainTask) -> str:
        return str(self.runtime_profile(task)["task_family"] or "general_self_evolution")

    def execution_kind(self, task: AutonomousChainTask) -> Optional[str]:
        execution = dict(task.metadata.get("execution_request") or {})
        explicit_execution_kind = (
            execution.get("execution_kind")
            or task.metadata.get("execution_kind")
            or task.execution_kind
        )
        normalized_explicit_kind = (
            self.normalize_family(explicit_execution_kind)
            if explicit_execution_kind
            else None
        )
        if normalized_explicit_kind == "body_upgrade":
            explicit_lower = str(explicit_execution_kind or "").strip().lower()
            if explicit_lower in {"body_improvement", "body_switch", "body_upgrade"}:
                return explicit_lower

        task_family = self.runtime_family(task)
        if task_family in {
            "memory_maintenance",
            "general_self_evolution",
            "body_upgrade",
        }:
            return task_family
        return normalized_explicit_kind

    def request_type(
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

    def drive_input_profile(self, request: Dict[str, Any]) -> Dict[str, Optional[str]]:
        return derive_runtime_task_profile(
            governance_task_type=request.get("governance_task_type"),
            task_family=request.get("task_family"),
            execution_kind=request.get("execution_kind"),
            default_task_family="general_self_evolution",
        )

    def governance_type(self, task: AutonomousChainTask) -> str:
        return str(self.runtime_profile(task)["governance_task_type"])

    def requires_execution_request(self, task: AutonomousChainTask) -> bool:
        if self.execution_kind(task) == "body_improvement":
            return False
        return self.governance_type(task) in {
            "self_evolution",
            "memory_maintenance",
        }


__all__ = ["TaskProfilePolicy"]
