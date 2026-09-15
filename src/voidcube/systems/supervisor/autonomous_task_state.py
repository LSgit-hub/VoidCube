"""State mutation owner for the autonomous-chain task projection."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, Protocol

from memai.governance import (
    GovernanceDecision,
    GovernanceEvent,
    GovernanceEventType,
    GovernanceGitLineage,
)

from .autonomous_chain_store import (
    AutonomousChainExecutionRequest,
    AutonomousChainStore,
    AutonomousChainTask,
)
from ...domain.state.autonomous_task import AutonomousTaskTransition, validate_task_outcome_bundle
from ...application.ports import EventPort
from ...domain.events import (
    AutonomousTaskReviewed,
    AutonomousTaskStarted,
    GovernanceDecisionMade,
)


class GovernanceEventRepository(Protocol):
    def append(self, event: GovernanceEvent) -> None: ...


TaskStatusObserver = Callable[[AutonomousChainTask, str], None]


class AutonomousTaskStateService:
    """Own task mutations and their authoritative governance write-ahead events."""

    def __init__(
        self,
        *,
        store: AutonomousChainStore,
        governance_repository: GovernanceEventRepository,
        on_status_change: Optional[TaskStatusObserver] = None,
        event_port: EventPort | None = None,
    ) -> None:
        self._store = store
        self._governance_repository = governance_repository
        self._on_status_change = on_status_change
        self._event_port = event_port

    def _emit_event(self, event: object) -> None:
        if self._event_port is None:
            return
        try:
            outcome = self._event_port.emit(event)
            if getattr(outcome, "status", "") in {"failed", "degraded"}:
                import logging
                logging.getLogger(__name__).warning("Autonomous event publication failed: %s", getattr(outcome, "error", ""))
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Autonomous event publication failed", exc_info=True)

    def create_task(self, **kwargs: Any) -> AutonomousChainTask:
        return self._store.create_task(
            **kwargs,
            before_commit=lambda task: self._record_transition(
                task,
                transition_kind="created",
            ),
        )

    def update_metadata(
        self,
        task_id: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        execution_request: Optional[AutonomousChainExecutionRequest] = None,
    ) -> AutonomousChainTask:
        task = self._store.update_metadata(
            task_id,
            metadata=metadata,
            execution_request=execution_request,
            before_commit=lambda updated: self._record_transition(
                updated,
                transition_kind="metadata",
            ),
        )
        return task

    def update_priority(
        self,
        task_id: str,
        *,
        priority: str,
        actor: str,
        reason: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AutonomousChainTask:
        return self._store.update_priority(
            task_id,
            priority=priority,
            actor=actor,
            reason=reason,
            context=context,
            before_commit=lambda updated: self._record_transition(
                updated,
                transition_kind="priority",
            ),
        )

    def claim_execution(self, task_id: str, **kwargs: Any) -> AutonomousChainTask:
        task = self._store.claim_execution(
            task_id,
            **kwargs,
            before_commit=lambda updated: self._record_transition(
                updated, transition_kind="execution_claim"
            ),
        )
        self._notify_status(task, "execution_claim")
        self._emit_event(AutonomousTaskStarted(
            task_id=task.task_id,
            cycle_id=str(task.metadata.get("cycle_id") or ""),
            lease_id=str(task.execution_lease.attempt_id or ""),
            attempt=int(task.metadata.get("attempt") or task.execution_lease.generation or 0),
        ))
        return task

    def renew_execution(self, task_id: str, **kwargs: Any) -> AutonomousChainTask:
        return self._store.renew_execution(
            task_id,
            **kwargs,
            before_commit=lambda updated: self._record_transition(
                updated, transition_kind="execution_renew"
            ),
        )

    def finalize_execution(self, task_id: str, **kwargs: Any) -> AutonomousChainTask:
        task = self._store.finalize_execution(
            task_id,
            **kwargs,
            before_commit=lambda updated: self._record_transition(
                updated, transition_kind="execution_finalize"
            ),
        )
        self._notify_status(task, "execution_finalize")
        return task

    def begin_reconcile(self, task_id: str, **kwargs: Any) -> AutonomousChainTask:
        task = self._store.begin_reconcile(
            task_id,
            **kwargs,
            before_commit=lambda updated: self._record_transition(
                updated, transition_kind="execution_reconcile"
            ),
        )
        self._notify_status(task, "execution_reconcile")
        return task

    def expire_execution(self, task_id: str, **kwargs: Any) -> AutonomousChainTask:
        task = self._store.expire_execution(
            task_id,
            **kwargs,
            before_commit=lambda updated: self._record_transition(
                updated, transition_kind="execution_timeout"
            ),
        )
        self._notify_status(task, "execution_timeout")
        return task

    def _notify_status(self, task: AutonomousChainTask, event_type: str) -> None:
        if self._on_status_change is not None:
            self._on_status_change(task, event_type)

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
        actor: str = "supervisor",
        decision_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_request: Optional[AutonomousChainExecutionRequest] = None,
        event_type: str = "status_update",
    ) -> AutonomousChainTask:
        current = self._store.get_task(task_id)
        metadata = dict((current.metadata if current is not None else {}) or {})
        supplied_context = dict(context or {})
        validate_task_outcome_bundle(
            status,
            execution_outcome_status=str(
                supplied_context.get("execution_outcome_status")
                or metadata.get("execution_outcome_status") or "unknown"
            ),
            memory_write_status=str(
                supplied_context.get("memory_write_status")
                or metadata.get("memory_write_status") or "unknown"
            ),
        )
        task = self._store.update_status(
            task_id,
            status=status,
            decision_id=decision_id,
            actor=actor,
            reason=reason or f"Status updated to {status}",
            context=supplied_context,
            execution_request=execution_request,
            before_commit=lambda updated: self._record_transition(
                updated,
                transition_kind=event_type,
            ),
        )
        if self._on_status_change is not None:
            self._on_status_change(task, event_type)
        if event_type in {"status_update", "execution_finalize", "execution_reconcile"}:
            refs = tuple(str(x) for x in (task.evidence.get("evidence_refs") or ()) if str(x))
            self._emit_event(AutonomousTaskReviewed(
                task_id=task.task_id, status=str(task.status),
                reason=str(task.decision_reason or ""), evidence_refs=refs,
            ))
        if event_type == "status_update":
            self._emit_event(GovernanceDecisionMade(
                task_id=task.task_id, decision=str(task.status),
                reason=str(task.decision_reason or ""), evidence_refs=refs,
            ))
        return task

    def clear_tasks(self, tasks: Iterable[AutonomousChainTask]) -> None:
        task_list = list(tasks)
        self._record_clear(task_list)
        self._store.clear_tasks()

    def _record_transition(
        self,
        task: AutonomousChainTask,
        *,
        transition_kind: str,
    ) -> None:
        status = str(task.status or "").strip().lower()
        decision = {
            "approved": GovernanceDecision.APPROVE,
            "deferred": GovernanceDecision.DEFER,
            "paused": GovernanceDecision.PAUSE,
            "cancelled": GovernanceDecision.CANCEL,
            "completed": GovernanceDecision.COMPLETED,
            "failed": GovernanceDecision.FAILED,
        }.get(status, GovernanceDecision.RECORD_ONLY)
        latest_decision = task.decision_history[-1] if task.decision_history else None
        previous_decision = (
            task.decision_history[-2] if len(task.decision_history) > 1 else None
        )
        cycle_id = str(
            task.metadata.get("cycle_id")
            or task.evidence.get("cycle_id")
            or ""
        )
        lease_id = str(
            task.execution_lease.attempt_id
            or task.metadata.get("lease_id")
            or ""
        )
        attempt = int(
            task.metadata.get("attempt")
            or task.metadata.get("attempt_number")
            or task.execution_lease.generation
            or 0
        )
        evidence_refs = tuple(
            dict.fromkeys(
                str(reference).strip()
                for reference in (
                    list(task.evidence.get("evidence_refs") or [])
                    + list(task.metadata.get("evidence_refs") or [])
                )
                if str(reference).strip()
            )
        )
        memory_write_status = str(
            task.metadata.get("memory_write_status")
            or task.evidence.get("memory_write_status")
            or "unknown"
        )
        execution_outcome_status = str(
            task.metadata.get("execution_outcome_status")
            or task.evidence.get("execution_outcome_status")
            or "unknown"
        )
        transition = AutonomousTaskTransition(
            task_id=task.task_id,
            from_status=str(previous_decision.status if previous_decision else ""),
            to_status=status,
            reason=str(task.decision_reason or ""),
            cycle_id=cycle_id,
            lease_id=lease_id,
            attempt=attempt,
            evidence_refs=evidence_refs,
            memory_write_status=memory_write_status,
            context=(latest_decision.context if latest_decision is not None else {}),
        )
        execution_request = task.execution_request
        lineage_payload = (
            execution_request.git_lineage.model_dump(mode="json")
            if execution_request is not None
            else dict(task.evidence.get("git_lineage") or {})
        )
        self._governance_repository.append(
            GovernanceEvent.create(
                event_type=GovernanceEventType.AUTONOMOUS_TASK_TRANSITION,
                source_actor=(
                    str(latest_decision.actor)
                    if latest_decision is not None
                    else "supervisor"
                ),
                decision=decision,
                reason=(
                    str(latest_decision.reason or task.decision_reason)
                    if latest_decision is not None
                    else task.decision_reason or f"Task {transition_kind}: {status}"
                ),
                task_id=task.task_id,
                body_id=str(
                    (execution_request.target_slot_id if execution_request else None)
                    or task.metadata.get("target_slot_id")
                    or ""
                ),
                git_lineage=GovernanceGitLineage.from_dict(lineage_payload),
                execution_result={
                    "transition_kind": transition_kind,
                    "transition_evidence": {
                        "task_id": transition.task_id,
                        "from_status": transition.from_status,
                        "to_status": transition.to_status,
                        "reason": transition.reason,
                        "cycle_id": transition.cycle_id,
                        "lease_id": transition.lease_id,
                        "attempt": transition.attempt,
                        "evidence_refs": list(transition.evidence_refs),
                        "memory_write_status": transition.memory_write_status,
                        "execution_outcome_status": execution_outcome_status,
                        "context": dict(transition.context),
                    },
                    "autonomous_task_projection": task.model_dump(mode="json"),
                    "runtime_task_profile": {
                        "governance_task_type": task.governance_task_type,
                        "task_family": task.task_family,
                        "execution_kind": task.execution_kind,
                    },
                },
            )
        )

    def _record_clear(self, tasks: list[AutonomousChainTask]) -> None:
        self._governance_repository.append(
            GovernanceEvent.create(
                event_type=GovernanceEventType.AUTONOMOUS_TASK_CLEAR,
                source_actor="supervisor_admin",
                decision=GovernanceDecision.RECORD_ONLY,
                reason="Administrative autonomous-chain runtime clear.",
                execution_result={
                    "cleared_task_ids": [task.task_id for task in tasks],
                    "cleared_task_count": len(tasks),
                },
            )
        )


__all__ = ["AutonomousTaskStateService"]
