"""Reconcile body-switch consent outcomes with autonomous task state."""

from __future__ import annotations

from typing import Any, Dict

from systems.supervisor.autonomous_chain_store import AutonomousChainStore
from systems.supervisor.autonomous_task_state import AutonomousTaskStateService


class AutonomousBodySwitchConsentService:
    """Own the task-state writeback after a user body-switch decision."""

    def __init__(
        self,
        *,
        store: AutonomousChainStore,
        task_state: AutonomousTaskStateService,
    ) -> None:
        self._store = store
        self._task_state = task_state

    def reconcile(self, result: Dict[str, Any]) -> None:
        task_link = dict(result.get("autonomous_task_link") or {})
        task_id = str(task_link.get("task_id") or "").strip()
        if not task_id:
            return
        task = self._store.get_task(task_id)
        if task is None or str(task.status) != "awaiting_user_consent":
            return

        status = str(result.get("status") or "").strip().lower()
        if status == "body_switch_activated":
            target_status = "completed"
            reason = "User approved the probe-passed body and activation completed."
        elif status == "body_switch_rejected":
            target_status = "cancelled"
            reason = "User rejected the body activation; the candidate returned to shell."
        else:
            return

        self._task_state.update_metadata(
            task_id,
            metadata={"body_switch_consent_result": dict(result)},
        )
        self._task_state.update_status(
            task_id,
            status=target_status,
            actor="user_consent",
            reason=reason,
            event_type="body_switch_consent_outcome",
        )


__all__ = ["AutonomousBodySwitchConsentService"]
