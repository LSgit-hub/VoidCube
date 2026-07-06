from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from VoidCube_core.utils import atomic_json_write
from systems.runtime_task_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_type,
)

AutonomousChainTaskStatus = Literal["planned", "deferred", "approved", "running", "paused", "cancelled", "completed", "failed", "awaiting_review", "retry"]
AutonomousChainExecutionRequestKind = Literal[
    "memory_maintenance",
    "general_self_evolution",
]
AutonomousChainExecutionRequestStatus = Literal["approved_for_execution"]


class AutonomousChainGitLineage(BaseModel):
    source_branch: Optional[str] = None
    source_commit: Optional[str] = None
    candidate_branch: Optional[str] = None
    candidate_commit: Optional[str] = None
    active_ref: Optional[str] = None
    rollback_ref: Optional[str] = None
    rollback_commit: Optional[str] = None
    diff_summary: str = ""
    changed_files: List[str] = Field(default_factory=list)


class AutonomousChainExecutionRequest(BaseModel):
    """Formal Mem/supervisor handoff contract consumed by executors.

    CLI and HTTP operations may test the executor surface, but a formal
    autonomous-chain execution needs this governance snapshot.
    """

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = "self_evolution"
    governance_task_type: Optional[str] = None
    task_family: Optional[str] = None
    execution_kind: Optional[str] = None
    decision_id: Optional[str] = None
    kind: AutonomousChainExecutionRequestKind = "general_self_evolution"
    status: AutonomousChainExecutionRequestStatus = "approved_for_execution"
    source_actor: str = "mem_supervisor"
    source_service: Optional[str] = None
    target_slot_id: Optional[str] = None
    target_service: Optional[str] = None
    session_id: Optional[str] = None
    git_lineage: AutonomousChainGitLineage = Field(default_factory=AutonomousChainGitLineage)
    probe_report_ref: Optional[str] = None
    activity_guard_evidence: Dict[str, Any] = Field(default_factory=dict)
    governor_decision: Dict[str, Any] = Field(default_factory=dict)
    rollback_plan: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _normalize_execution_request_profile(self) -> "AutonomousChainExecutionRequest":
        runtime_task_profile = derive_runtime_task_profile(
            task_type=self.task_type,
            governance_task_type=self.governance_task_type,
            task_family=self.task_family or str(self.kind),
            execution_kind=self.execution_kind or str(self.kind),
            kind=str(self.kind),
            default_task_family="general_self_evolution",
        )
        self.governance_task_type = runtime_task_profile["governance_task_type"]
        self.task_family = runtime_task_profile["task_family"]
        self.execution_kind = runtime_task_profile["execution_kind"]
        return self


class AutonomousChainTaskDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: AutonomousChainTaskStatus
    task_type: str = "self_evolution"
    governance_task_type: Optional[str] = None
    task_family: Optional[str] = None
    execution_kind: Optional[str] = None
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    actor: str = "supervisor"
    reason: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_runtime_profile(self) -> "AutonomousChainTaskDecision":
        runtime_task_profile = derive_runtime_task_profile(
            task_type=self.task_type,
            governance_task_type=self.governance_task_type,
            task_family=self.task_family,
            execution_kind=self.execution_kind,
            default_task_family="general_self_evolution",
        )
        self.governance_task_type = runtime_task_profile["governance_task_type"]
        self.task_family = runtime_task_profile["task_family"]
        self.execution_kind = runtime_task_profile["execution_kind"]
        return self


class AutonomousChainTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    summary: str = ""
    task_type: str = "self_evolution"
    governance_task_type: Optional[str] = None
    task_family: Optional[str] = None
    execution_kind: Optional[str] = None
    source: str = "self_learning"
    priority: str = "normal"
    status: AutonomousChainTaskStatus = "planned"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    decision_reason: str = ""
    decision_history: List[AutonomousChainTaskDecision] = Field(default_factory=list)
    execution_request: Optional[AutonomousChainExecutionRequest] = None

    @model_validator(mode="after")
    def _normalize_runtime_profile(self) -> "AutonomousChainTask":
        explicit_governance_task_type = (
            self.metadata.get("governance_task_type")
            or self.governance_task_type
        )
        explicit_task_family = self.metadata.get("task_family") or self.task_family
        explicit_execution_kind = (
            self.metadata.get("execution_kind")
            or self.execution_kind
        )
        runtime_task_profile = derive_runtime_task_profile(
            task_type=self.task_type,
            governance_task_type=explicit_governance_task_type,
            task_family=explicit_task_family,
            execution_kind=explicit_execution_kind,
            default_task_family="general_self_evolution",
        )
        self.governance_task_type = (
            explicit_governance_task_type
            or runtime_task_profile["governance_task_type"]
        )
        self.task_family = explicit_task_family or runtime_task_profile["task_family"]
        self.execution_kind = (
            explicit_execution_kind
            or runtime_task_profile["execution_kind"]
        )
        return self



# NOTE(SB-02): The autonomous-chain store JSON file is runtime coordination
# state, not an authoritative store. It can be rebuilt from Mem governance
# history if lost. The Mem repository (governance_event table) is the true
# source of record for governance decisions. See state-boundary.md §4.

class AutonomousChainStoreSnapshot(BaseModel):
    version: int = 1
    tasks: List[AutonomousChainTask] = Field(default_factory=list)


class AutonomousChainStore:
    _GOVERNANCE_BACKLOG_STATUSES: frozenset[str] = frozenset(
        {
            "planned",
            "deferred",
            "approved",
            "running",
            "paused",
            "awaiting_review",
            "retry",
        }
    )
    _API_A_EXECUTION_LANE_STATUSES: frozenset[str] = frozenset(
        {
            "approved",
            "running",
            "retry",
        }
    )
    _WRITEBACK_HISTORY_STATUSES: frozenset[str] = frozenset(
        {
            "completed",
            "failed",
        }
    )

    def __init__(self, storage_path: str | Path) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.storage_path.exists():
            self._write_snapshot(AutonomousChainStoreSnapshot())

    def list_tasks(self, *, status: Optional[AutonomousChainTaskStatus] = None) -> List[AutonomousChainTask]:
        snapshot = self._load_snapshot()
        tasks = snapshot.tasks
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return tasks

    def list_governance_backlog_tasks(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
    ) -> List[AutonomousChainTask]:
        """Return live governance backlog items still participating in the chain."""
        allowed = self._status_filter(
            status=status,
            default_statuses=self._GOVERNANCE_BACKLOG_STATUSES,
        )
        return self._list_tasks_by_statuses(allowed)

    def list_api_a_execution_lane_tasks(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
    ) -> List[AutonomousChainTask]:
        """Return tasks that are in or approaching the API-A execution lane."""
        allowed = self._status_filter(
            status=status,
            default_statuses=self._API_A_EXECUTION_LANE_STATUSES,
        )
        return self._list_tasks_by_statuses(allowed)

    def list_writeback_history(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
    ) -> List[AutonomousChainTask]:
        """Return outcome records that already formed a writeback-worthy history."""
        allowed = self._status_filter(
            status=status,
            default_statuses=self._WRITEBACK_HISTORY_STATUSES,
        )
        return self._list_tasks_by_statuses(allowed)

    def list_chain_projection_tasks(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
        include_cancelled: bool = False,
    ) -> List[AutonomousChainTask]:
        """Return autonomous-chain task records without exposing raw storage semantics."""
        normalized_status = self._normalized_status_value(status)
        if normalized_status:
            if normalized_status in self._GOVERNANCE_BACKLOG_STATUSES:
                return self.list_governance_backlog_tasks(status=status)
            if normalized_status in self._WRITEBACK_HISTORY_STATUSES:
                return self.list_writeback_history(status=status)
            if include_cancelled and normalized_status == "cancelled":
                return self._list_tasks_by_statuses(frozenset({"cancelled"}))
            return []

        allowed_statuses = set(self._GOVERNANCE_BACKLOG_STATUSES)
        allowed_statuses.update(self._WRITEBACK_HISTORY_STATUSES)
        if include_cancelled:
            allowed_statuses.add("cancelled")
        return self._list_tasks_by_statuses(frozenset(allowed_statuses))

    def get_task(self, task_id: str) -> Optional[AutonomousChainTask]:
        snapshot = self._load_snapshot()
        for task in snapshot.tasks:
            if task.task_id == task_id:
                return task
        return None

    def clear_tasks(self) -> None:
        with self._lock:
            self._write_snapshot(AutonomousChainStoreSnapshot())

    def create_task(
        self,
        *,
        title: str,
        summary: str = "",
        trace_id: Optional[str] = None,
        task_type: str = "self_evolution",
        source: str = "self_learning",
        priority: str = "normal",
        metadata: Optional[Dict[str, Any]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> AutonomousChainTask:
        with self._lock:
            snapshot = self._load_snapshot()
            task = AutonomousChainTask(
                title=title,
                summary=summary,
                trace_id=trace_id or str(uuid.uuid4()),
                task_type=task_type,
                source=source,
                priority=priority,
                metadata=dict(metadata or {}),
                evidence=dict(evidence or {}),
                constraints=dict(constraints or {}),
            )
            snapshot.tasks.append(task)
            self._write_snapshot(snapshot)
            return task

    def update_status(
        self,
        task_id: str,
        *,
        status: AutonomousChainTaskStatus,
        decision_id: Optional[str] = None,
        actor: str = "supervisor",
        reason: str = "",
        context: Optional[Dict[str, Any]] = None,
        execution_request: Optional[AutonomousChainExecutionRequest] = None,
    ) -> AutonomousChainTask:
        # ── Validate state transition ──
        _LEGAL_TRANSITIONS: dict[str, set[str]] = {
            "planned": {"approved", "paused", "cancelled", "deferred", "awaiting_review"},
            "awaiting_review": {"approved", "planned", "deferred", "paused", "cancelled"},
            "approved": {"running", "cancelled", "deferred", "paused"},
            "running": {"approved", "completed", "failed", "paused", "retry"},
            "paused": {"planned", "approved", "cancelled", "deferred"},
            "deferred": {"planned", "approved", "cancelled", "paused", "awaiting_review"},
            "retry": {"approved", "planned", "deferred", "paused", "cancelled"},
            "completed": set(),   # terminal
            "failed": set(),      # terminal
            "cancelled": set(),   # terminal
        }
        target = status.value if hasattr(status, 'value') else str(status)

        with self._lock:
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                current = task.status.value if hasattr(task.status, 'value') else str(task.status)
                if target == current:
                    if execution_request is not None:
                        task.execution_request = execution_request
                    if context:
                        # Preserve fresh review/handoff context without creating
                        # an illegal no-op transition entry.
                        task.metadata.update({"last_decision_context": dict(context)})
                    task.updated_at = datetime.utcnow()
                    snapshot.tasks[index] = task
                    self._write_snapshot(snapshot)
                    return task
                if current not in _LEGAL_TRANSITIONS:
                    raise ValueError(f"Unknown task state: {current}")
                if target not in _LEGAL_TRANSITIONS:
                    raise ValueError(f"Unknown task target state: {target}")
                legal = _LEGAL_TRANSITIONS[current]
                if target not in legal:
                    raise ValueError(
                        f"Illegal task state transition: {current} → {target} "
                        f"(legal: {', '.join(sorted(legal)) if legal else 'terminal'})"
                    )
                task.status = status
                task.updated_at = datetime.utcnow()
                task.decision_reason = reason
                task.decision_history.append(
                    AutonomousChainTaskDecision(
                        decision_id=decision_id or str(uuid.uuid4()),
                        status=status,
                        task_type=task.task_type,
                        governance_task_type=task.governance_task_type,
                        task_family=task.task_family,
                        execution_kind=task.execution_kind,
                        trace_id=task.trace_id,
                        actor=actor,
                        reason=reason,
                        context=dict(context or {}),
                    )
                )
                if execution_request is not None:
                    task.execution_request = execution_request
                snapshot.tasks[index] = task
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def update_metadata(
        self,
        task_id: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        execution_request: Optional[AutonomousChainExecutionRequest] = None,
    ) -> AutonomousChainTask:
        with self._lock:
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                if metadata:
                    task.metadata.update(dict(metadata))
                if execution_request is not None:
                    task.execution_request = execution_request
                task.updated_at = datetime.utcnow()
                snapshot.tasks[index] = task
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def update_priority(
        self,
        task_id: str,
        *,
        priority: str,
        actor: str = "supervisor",
        reason: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> AutonomousChainTask:
        normalized_priority = str(priority or "").strip().lower() or "normal"
        with self._lock:
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                task.priority = normalized_priority
                task.updated_at = datetime.utcnow()
                task.decision_reason = reason or f"Priority updated to {normalized_priority}"
                task.decision_history.append(
                    AutonomousChainTaskDecision(
                        decision_id=str(uuid.uuid4()),
                        status=task.status,
                        task_type=task.task_type,
                        governance_task_type=task.governance_task_type,
                        task_family=task.task_family,
                        execution_kind=task.execution_kind,
                        trace_id=task.trace_id,
                        actor=actor,
                        reason=task.decision_reason,
                        context=dict(context or {}),
                    )
                )
                snapshot.tasks[index] = task
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def _load_snapshot(self) -> AutonomousChainStoreSnapshot:
        if not self.storage_path.exists():
            return AutonomousChainStoreSnapshot()
        raw = self.storage_path.read_text(encoding="utf-8").strip()
        if not raw:
            return AutonomousChainStoreSnapshot()
        return AutonomousChainStoreSnapshot.model_validate_json(raw)

    def _write_snapshot(self, snapshot: AutonomousChainStoreSnapshot) -> None:
        atomic_json_write(
            self.storage_path,
            snapshot.model_dump(mode="json"),
        )

    def _list_tasks_by_statuses(
        self,
        allowed_statuses: frozenset[str],
    ) -> List[AutonomousChainTask]:
        snapshot = self._load_snapshot()
        return [
            task for task in snapshot.tasks
            if self._normalized_status(task) in allowed_statuses
        ]

    @staticmethod
    def _normalized_status(task: AutonomousChainTask) -> str:
        return str(task.status or "").strip().lower()

    @staticmethod
    def _normalized_status_value(status: Optional[AutonomousChainTaskStatus]) -> str:
        return str(status or "").strip().lower()

    @staticmethod
    def _status_filter(
        *,
        status: Optional[AutonomousChainTaskStatus],
        default_statuses: frozenset[str],
    ) -> frozenset[str]:
        if status is None:
            return default_statuses
        return frozenset({str(status).strip().lower()})

