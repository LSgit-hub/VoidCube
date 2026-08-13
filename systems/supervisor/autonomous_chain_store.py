from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from VoidCube_core.utils import atomic_json_write, interprocess_file_lock
from systems.runtime_task_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_type,
)

# `approved` is a persisted historical enum name. In current chain semantics it
# means "API-B has handed this item off; API-A may claim it", not "executed".
AutonomousChainTaskStatus = Literal[
    "planned",
    "deferred",
    "approved",
    "running",
    "reconciling",
    "awaiting_user_consent",
    "paused",
    "cancelled",
    "completed",
    "failed",
    "awaiting_review",
    "retry",
]
AutonomousChainExecutionRequestKind = Literal[
    "memory_maintenance",
    "general_self_evolution",
]
# Persisted contract value for formal executor requests. Treat it as
# handoff-ready, not as proof that execution has already started or completed.
AutonomousChainExecutionRequestStatus = Literal["approved_for_execution"]
ExecutionLeaseState = Literal["released", "active", "reconciling", "expired"]


class StaleExecutionLeaseError(ValueError):
    """A writer does not own the task's current execution generation."""

    code = "stale_execution_lease"


class AutonomousChainExecutionLease(BaseModel):
    generation: int = Field(default=0, ge=0)
    attempt_id: Optional[str] = None
    owner_session_id: Optional[str] = None
    claimed_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    state: ExecutionLeaseState = "released"


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
    autonomous-chain execution needs this governance snapshot. Its
    `approved_for_execution` status means the request may be consumed by the
    executor; completion is only represented by a later task writeback.
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
    drive_input_evidence: Dict[str, Any] = Field(default_factory=dict)
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
        self.drive_input_evidence = dict(self.drive_input_evidence or {})
        if self.kind == "general_self_evolution":
            missing: list[str] = []
            if not str(self.target_slot_id or "").strip():
                missing.append("target_slot_id")
            lineage = self.git_lineage
            for field_name in (
                "source_commit",
                "candidate_commit",
                "rollback_commit",
            ):
                if not str(getattr(lineage, field_name) or "").strip():
                    missing.append(f"git_lineage.{field_name}")
            changed_files = [
                str(path).strip()
                for path in lineage.changed_files
                if str(path).strip()
            ]
            if not changed_files:
                missing.append("git_lineage.changed_files")
            if missing:
                raise ValueError(
                    "general_self_evolution execution requires auditable body lineage: "
                    + ", ".join(missing)
                )
            lineage.changed_files = list(dict.fromkeys(changed_files))
            if lineage.source_commit == lineage.candidate_commit:
                raise ValueError(
                    "git_lineage.candidate_commit must differ from source_commit"
                )
            if lineage.rollback_commit != lineage.source_commit:
                raise ValueError(
                    "git_lineage.rollback_commit must match source_commit"
                )
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
    execution_lease: AutonomousChainExecutionLease = Field(
        default_factory=AutonomousChainExecutionLease
    )

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
    version: int = 2
    tasks: List[AutonomousChainTask] = Field(default_factory=list)


class AutonomousChainStore:
    _LEGACY_LINEAGE_MIGRATION_CODE = (
        "legacy_execution_request_missing_auditable_lineage"
    )
    _AUTONOMOUS_CHAIN_LIVE_STATUSES: frozenset[str] = frozenset(
        {
            "planned",
            "deferred",
            "approved",
            "running",
            "reconciling",
            "awaiting_user_consent",
            "paused",
            "awaiting_review",
            "retry",
        }
    )
    _API_B_JUDGEMENT_STATUSES: frozenset[str] = frozenset(
        {
            "planned",
            "deferred",
            "paused",
            "awaiting_review",
        }
    )
    _API_A_EXECUTION_LANE_STATUSES: frozenset[str] = frozenset(
        {
            "approved",
            "running",
            "reconciling",
            "awaiting_user_consent",
            "retry",
        }
    )
    _WRITEBACK_HISTORY_STATUSES: frozenset[str] = frozenset(
        {
            "completed",
            "failed",
        }
    )
    _GOVERNANCE_DECISION_TO_STATUS: Dict[str, AutonomousChainTaskStatus] = {
        "approve": "approved",
        "approve_with_watch": "approved",
        "defer": "deferred",
        "reject": "cancelled",
        "cancel": "cancelled",
        "pause": "paused",
        "rollback_required": "failed",
        "completed": "completed",
        "failed": "failed",
    }

    def __init__(self, storage_path: str | Path) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._storage_lock_path = self.storage_path.with_suffix(
            f"{self.storage_path.suffix}.lock"
        )
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            if not self.storage_path.exists():
                self._write_snapshot(AutonomousChainStoreSnapshot())
            else:
                self._load_snapshot()

    def list_tasks(self, *, status: Optional[AutonomousChainTaskStatus] = None) -> List[AutonomousChainTask]:
        snapshot = self._load_snapshot()
        tasks = snapshot.tasks
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return tasks

    def list_api_b_judgement_tasks(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
    ) -> List[AutonomousChainTask]:
        """Return items still owned by API-B judgement, before API-A handoff."""
        allowed = self._status_filter(
            status=status,
            default_statuses=self._API_B_JUDGEMENT_STATUSES,
        )
        return self._list_tasks_by_statuses(allowed)

    def list_api_a_handoff_tasks(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
    ) -> List[AutonomousChainTask]:
        """Return items API-B has transferred and API-A may pick up.

        The backing status is the persisted `approved` enum, but this read path
        exposes the current API-A handoff meaning rather than an execution
        result.
        """
        allowed = self._status_filter(
            status=status,
            default_statuses=frozenset({"approved", "retry"}),
        )
        return self._list_tasks_by_statuses(allowed)

    def list_api_a_running_tasks(self) -> List[AutonomousChainTask]:
        """Return items currently reported as running on the API-A execution side."""
        return self._list_tasks_by_statuses(frozenset({"running"}))

    def list_api_a_execution_lane_tasks(
        self,
        *,
        status: Optional[AutonomousChainTaskStatus] = None,
    ) -> List[AutonomousChainTask]:
        """Return tasks in the API-A lane: handoff-ready, running, or retry."""
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
            if normalized_status in self._API_B_JUDGEMENT_STATUSES:
                return self.list_api_b_judgement_tasks(status=status)
            if normalized_status in self._API_A_EXECUTION_LANE_STATUSES:
                return self.list_api_a_execution_lane_tasks(status=status)
            if normalized_status in self._WRITEBACK_HISTORY_STATUSES:
                return self.list_writeback_history(status=status)
            if include_cancelled and normalized_status == "cancelled":
                return self._list_tasks_by_statuses(frozenset({"cancelled"}))
            return []

        allowed_statuses = set(self._API_B_JUDGEMENT_STATUSES)
        allowed_statuses.update(self._API_A_EXECUTION_LANE_STATUSES)
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
        with self._lock, interprocess_file_lock(self._storage_lock_path):
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
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        with self._lock, interprocess_file_lock(self._storage_lock_path):
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
            if before_commit is not None:
                before_commit(task)
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
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        # ── Validate state transition ──
        _LEGAL_TRANSITIONS: dict[str, set[str]] = {
            "planned": {"approved", "paused", "cancelled", "deferred", "awaiting_review"},
            "awaiting_review": {"approved", "planned", "deferred", "paused", "cancelled"},
            "approved": {"running", "cancelled", "deferred", "paused"},
            "running": {
                "reconciling",
                "awaiting_user_consent",
                "completed",
                "failed",
                "paused",
                "retry",
            },
            "reconciling": {"approved", "completed", "failed", "paused"},
            "awaiting_user_consent": {"completed", "failed", "cancelled"},
            "paused": {"planned", "approved", "cancelled", "deferred"},
            "deferred": {"planned", "approved", "cancelled", "paused", "awaiting_review"},
            "retry": {"approved", "planned", "deferred", "paused", "cancelled"},
            "completed": set(),   # terminal
            "failed": set(),      # terminal
            "cancelled": set(),   # terminal
        }
        target = status.value if hasattr(status, 'value') else str(status)

        with self._lock, interprocess_file_lock(self._storage_lock_path):
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
                    if before_commit is not None:
                        before_commit(task)
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
                if before_commit is not None:
                    before_commit(task)
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def update_metadata(
        self,
        task_id: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        execution_request: Optional[AutonomousChainExecutionRequest] = None,
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        with self._lock, interprocess_file_lock(self._storage_lock_path):
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
                if before_commit is not None:
                    before_commit(task)
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
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        normalized_priority = str(priority or "").strip().lower() or "normal"
        with self._lock, interprocess_file_lock(self._storage_lock_path):
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
                if before_commit is not None:
                    before_commit(task)
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    @staticmethod
    def _require_lease(
        task: AutonomousChainTask,
        *,
        generation: int,
        attempt_id: str,
    ) -> AutonomousChainExecutionLease:
        lease = task.execution_lease
        now = datetime.now(timezone.utc)
        expires_at = lease.expires_at
        if expires_at is None:
            raise StaleExecutionLeaseError(
                f"stale_execution_lease: task={task.task_id} lease has no expiry"
            )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            lease.generation != int(generation)
            or not attempt_id
            or lease.attempt_id != attempt_id
            or lease.state not in {"active", "reconciling"}
            or expires_at <= now
        ):
            raise StaleExecutionLeaseError(
                f"stale_execution_lease: task={task.task_id} "
                f"expected=({lease.generation},{lease.attempt_id}) "
                f"received=({generation},{attempt_id})"
            )
        return lease

    def claim_execution(
        self,
        task_id: str,
        *,
        owner_session_id: str,
        lease_seconds: float = 300,
        actor: str = "cli_agent",
        reason: str = "API-A claimed autonomous task.",
        context: Optional[Dict[str, Any]] = None,
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        owner = str(owner_session_id or "").strip()
        if not owner:
            raise ValueError("owner_session_id is required")
        now = datetime.now(timezone.utc)
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                if task.status not in {"approved", "retry"}:
                    raise StaleExecutionLeaseError(
                        f"stale_execution_lease: task {task_id} is {task.status}, not claimable"
                    )
                generation = task.execution_lease.generation + 1
                attempt_id = str(uuid.uuid4())
                task.status = "running"
                task.updated_at = datetime.utcnow()
                task.execution_lease = AutonomousChainExecutionLease(
                    generation=generation,
                    attempt_id=attempt_id,
                    owner_session_id=owner,
                    claimed_at=now,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=max(1.0, float(lease_seconds))),
                    state="active",
                )
                task.decision_reason = reason
                task.decision_history.append(
                    AutonomousChainTaskDecision(
                        status="running",
                        task_type=task.task_type,
                        governance_task_type=task.governance_task_type,
                        task_family=task.task_family,
                        execution_kind=task.execution_kind,
                        trace_id=task.trace_id,
                        actor=actor,
                        reason=reason,
                        context={
                            **dict(context or {}),
                            "lease_generation": generation,
                            "attempt_id": attempt_id,
                        },
                    )
                )
                snapshot.tasks[index] = task
                if before_commit is not None:
                    before_commit(task)
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def renew_execution(
        self,
        task_id: str,
        *,
        generation: int,
        attempt_id: str,
        lease_seconds: float = 300,
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        now = datetime.now(timezone.utc)
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                lease = self._require_lease(
                    task, generation=generation, attempt_id=attempt_id
                )
                if task.status != "running" or lease.state != "active":
                    raise StaleExecutionLeaseError(
                        f"stale_execution_lease: task {task_id} is not actively running"
                    )
                lease.heartbeat_at = now
                lease.expires_at = now + timedelta(
                    seconds=max(1.0, float(lease_seconds))
                )
                task.updated_at = datetime.utcnow()
                snapshot.tasks[index] = task
                if before_commit is not None:
                    before_commit(task)
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def validate_execution_lease(
        self,
        task_id: str,
        *,
        generation: int,
        attempt_id: str,
        owner_session_id: str | None = None,
    ) -> AutonomousChainTask:
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = self._load_snapshot()
            for task in snapshot.tasks:
                if task.task_id != task_id:
                    continue
                lease = self._require_lease(
                    task, generation=generation, attempt_id=attempt_id
                )
                if (
                    owner_session_id is not None
                    and lease.owner_session_id != str(owner_session_id).strip()
                ):
                    raise StaleExecutionLeaseError(
                        "stale_execution_lease: owner mismatch"
                    )
                if task.status not in {"running", "awaiting_user_consent", "reconciling"}:
                    raise StaleExecutionLeaseError(
                        f"stale_execution_lease: task {task_id} is {task.status}"
                    )
                return task
        raise KeyError(task_id)

    def record_stale_lease_rejection(
        self,
        task_id: str,
        *,
        generation: int,
        attempt_id: str,
        operation: str,
    ) -> None:
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                audit = task.metadata.get("stale_lease_rejections")
                audit = list(audit) if isinstance(audit, list) else []
                audit.append(
                    {
                        "operation": str(operation),
                        "received_generation": int(generation),
                        "received_attempt_id": str(attempt_id),
                        "current_generation": task.execution_lease.generation,
                        "current_attempt_id": task.execution_lease.attempt_id,
                        "rejected_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                task.metadata["stale_lease_rejections"] = audit[-50:]
                task.updated_at = datetime.utcnow()
                snapshot.tasks[index] = task
                self._write_snapshot(snapshot)
                return
        raise KeyError(task_id)

    def finalize_execution(
        self,
        task_id: str,
        *,
        generation: int,
        attempt_id: str,
        status: Literal["completed", "failed", "cancelled"],
        actor: str,
        reason: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                lease = self._require_lease(
                    task, generation=generation, attempt_id=attempt_id
                )
                if task.status not in {"running", "awaiting_user_consent", "reconciling"}:
                    raise StaleExecutionLeaseError(
                        f"stale_execution_lease: task {task_id} cannot finalize from {task.status}"
                    )
                task.status = status
                task.updated_at = datetime.utcnow()
                task.decision_reason = reason
                lease.state = "released"
                if metadata:
                    task.metadata.update(dict(metadata))
                task.decision_history.append(
                    AutonomousChainTaskDecision(
                        status=status,
                        task_type=task.task_type,
                        governance_task_type=task.governance_task_type,
                        task_family=task.task_family,
                        execution_kind=task.execution_kind,
                        trace_id=task.trace_id,
                        actor=actor,
                        reason=reason,
                        context={
                            **dict(context or {}),
                            "lease_generation": generation,
                            "attempt_id": attempt_id,
                        },
                    )
                )
                snapshot.tasks[index] = task
                if before_commit is not None:
                    before_commit(task)
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def begin_reconcile(
        self,
        task_id: str,
        *,
        reason: str,
        context: Optional[Dict[str, Any]] = None,
        before_commit: Optional[Callable[[AutonomousChainTask], None]] = None,
    ) -> AutonomousChainTask:
        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                if task.status != "running" or task.execution_lease.state not in {
                    "active",
                    "released",
                }:
                    raise ValueError(f"Task {task_id} is not actively running")
                task.status = "reconciling"
                task.updated_at = datetime.utcnow()
                task.decision_reason = reason
                task.execution_lease.state = "reconciling"
                task.decision_history.append(
                    AutonomousChainTaskDecision(
                        status="reconciling",
                        task_type=task.task_type,
                        governance_task_type=task.governance_task_type,
                        task_family=task.task_family,
                        execution_kind=task.execution_kind,
                        trace_id=task.trace_id,
                        actor="supervisor",
                        reason=reason,
                        context=dict(context or {}),
                    )
                )
                snapshot.tasks[index] = task
                if before_commit is not None:
                    before_commit(task)
                self._write_snapshot(snapshot)
                return task
        raise KeyError(task_id)

    def recover_from_governance_events(
        self,
        events: Iterable[Any],
        *,
        replace: bool = False,
    ) -> Dict[str, Any]:
        """Merge task projections rebuilt from Mem governance events.

        The JSON store is runtime coordination state. Mem's append-only log is
        authoritative: a newer recovered projection replaces an older runtime
        projection with the same task id. ``replace`` additionally drops tasks
        that have no recoverable governance history.
        """
        event_rows = sorted(
            (
                row
                for event in events
                if (row := self._governance_event_to_payload(event))
            ),
            key=self._governance_event_sort_key,
        )
        clear_indexes = [
            index
            for index, row in enumerate(event_rows)
            if self._enum_value(row.get("event_type")).strip().lower()
            == "autonomous_task_clear"
        ]
        if clear_indexes:
            event_rows = event_rows[clear_indexes[-1] + 1 :]
        by_task_id: Dict[str, List[Dict[str, Any]]] = {}
        skipped_without_task_id = 0
        for row in event_rows:
            task_id = str(row.get("task_id") or "").strip()
            if not task_id:
                skipped_without_task_id += 1
                continue
            if self._status_from_governance_event(row) is None:
                continue
            by_task_id.setdefault(task_id, []).append(row)

        recovered_tasks = [
            self._task_from_governance_events(task_id, rows)
            for task_id, rows in by_task_id.items()
        ]
        recovered_tasks = [task for task in recovered_tasks if task is not None]

        with self._lock, interprocess_file_lock(self._storage_lock_path):
            snapshot = AutonomousChainStoreSnapshot() if replace else self._load_snapshot()
            existing_indexes = {
                task.task_id: index for index, task in enumerate(snapshot.tasks)
            }
            added = 0
            updated = 0
            for task in recovered_tasks:
                existing_index = existing_indexes.get(task.task_id)
                if existing_index is not None:
                    existing = snapshot.tasks[existing_index]
                    if replace or self._task_is_newer(task, existing):
                        snapshot.tasks[existing_index] = task
                        updated += 1
                    continue
                snapshot.tasks.append(task)
                existing_indexes[task.task_id] = len(snapshot.tasks) - 1
                added += 1
            if added or updated or replace:
                self._write_snapshot(snapshot)

        return {
            "status": "recovered",
            "event_count": len(event_rows),
            "candidate_task_count": len(by_task_id),
            "added_task_count": added,
            "updated_task_count": updated,
            "skipped_without_task_id": skipped_without_task_id,
            "replace": replace,
        }

    @classmethod
    def _task_is_newer(
        cls,
        candidate: AutonomousChainTask,
        existing: AutonomousChainTask,
    ) -> bool:
        return cls._comparable_datetime(candidate.updated_at) > cls._comparable_datetime(
            existing.updated_at
        )

    @staticmethod
    def _comparable_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _load_snapshot(self) -> AutonomousChainStoreSnapshot:
        if not self.storage_path.exists():
            return AutonomousChainStoreSnapshot()
        raw = self.storage_path.read_text(encoding="utf-8").strip()
        if not raw:
            return AutonomousChainStoreSnapshot()
        payload = json.loads(raw)
        migrated_payload, migrated = self._migrate_legacy_execution_requests(payload)
        if isinstance(migrated_payload, dict) and int(
            migrated_payload.get("version") or 1
        ) < 2:
            migrated_payload["version"] = 2
            migrated = True
        snapshot = AutonomousChainStoreSnapshot.model_validate(migrated_payload)
        if migrated:
            self._write_snapshot(snapshot)
        return snapshot

    @classmethod
    def _migrate_legacy_execution_requests(
        cls,
        payload: Any,
    ) -> tuple[Any, bool]:
        """Withdraw pre-lineage execution grants and persist current-schema state."""
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            return payload, False

        migrated = False
        migrated_at = datetime.now(timezone.utc).isoformat()
        for task_payload in payload["tasks"]:
            if not isinstance(task_payload, dict):
                continue
            request_payload = task_payload.get("execution_request")
            if not isinstance(request_payload, dict):
                continue
            if str(request_payload.get("kind") or "general_self_evolution") != (
                "general_self_evolution"
            ):
                continue
            try:
                AutonomousChainExecutionRequest.model_validate(request_payload)
            except ValidationError as exc:
                if not cls._is_legacy_lineage_validation_error(exc):
                    raise
            else:
                continue

            missing_fields = cls._missing_execution_lineage_fields(request_payload)
            previous_status = str(task_payload.get("status") or "planned")
            request_id = str(request_payload.get("request_id") or "")
            task_payload["execution_request"] = None

            metadata = task_payload.setdefault("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
                task_payload["metadata"] = metadata
            metadata["snapshot_migration"] = {
                "code": cls._LEGACY_LINEAGE_MIGRATION_CODE,
                "migrated_at": migrated_at,
                "previous_status": previous_status,
                "removed_execution_request_id": request_id,
                "missing_fields": missing_fields,
                "review_required": previous_status in cls._API_A_EXECUTION_LANE_STATUSES,
            }

            if previous_status in cls._API_A_EXECUTION_LANE_STATUSES:
                reason = (
                    "Legacy execution authorization was withdrawn because its body "
                    "lineage is not auditable; the task requires a new review."
                )
                task_payload["status"] = "awaiting_review"
                task_payload["decision_reason"] = reason
                history = task_payload.setdefault("decision_history", [])
                if not isinstance(history, list):
                    history = []
                    task_payload["decision_history"] = history
                history.append(
                    {
                        "decision_id": f"snapshot-migration:{request_id or task_payload.get('task_id', '')}",
                        "status": "awaiting_review",
                        "task_type": task_payload.get("task_type") or "self_evolution",
                        "governance_task_type": task_payload.get("governance_task_type"),
                        "task_family": task_payload.get("task_family"),
                        "execution_kind": task_payload.get("execution_kind"),
                        "trace_id": task_payload.get("trace_id") or str(uuid.uuid4()),
                        "decided_at": migrated_at,
                        "actor": "snapshot_migration",
                        "reason": reason,
                        "context": {
                            "migration_code": cls._LEGACY_LINEAGE_MIGRATION_CODE,
                            "missing_fields": missing_fields,
                        },
                    }
                )
            migrated = True

        return payload, migrated

    @staticmethod
    def _is_legacy_lineage_validation_error(exc: ValidationError) -> bool:
        errors = exc.errors()
        marker = (
            "general_self_evolution execution requires auditable body lineage"
        )
        return bool(errors) and all(
            marker in str(error.get("msg") or "") for error in errors
        )

    @staticmethod
    def _missing_execution_lineage_fields(request_payload: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        if not str(request_payload.get("target_slot_id") or "").strip():
            missing.append("target_slot_id")
        lineage = request_payload.get("git_lineage")
        lineage = lineage if isinstance(lineage, dict) else {}
        for field_name in ("source_commit", "candidate_commit", "rollback_commit"):
            if not str(lineage.get(field_name) or "").strip():
                missing.append(f"git_lineage.{field_name}")
        changed_files = lineage.get("changed_files")
        if not isinstance(changed_files, list) or not any(
            str(path).strip() for path in changed_files
        ):
            missing.append("git_lineage.changed_files")
        return missing

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

    @classmethod
    def _governance_event_to_payload(cls, event: Any) -> Dict[str, Any]:
        if event is None:
            return {}
        if isinstance(event, dict):
            return dict(event)
        if hasattr(event, "to_dict"):
            try:
                payload = event.to_dict()
                if isinstance(payload, dict):
                    return dict(payload)
            except Exception:
                pass
        payload: Dict[str, Any] = {}
        for key in (
            "id",
            "event_type",
            "source_actor",
            "decision",
            "reason",
            "created_at",
            "task_id",
            "body_id",
            "risk_level",
            "confidence",
            "git_lineage",
            "probe_report_ref",
            "execution_result",
            "evidence_refs",
        ):
            if hasattr(event, key):
                payload[key] = getattr(event, key)
        return payload

    @classmethod
    def _governance_event_sort_key(cls, event: Dict[str, Any]) -> tuple[str, str]:
        created_at = cls._governance_event_datetime(event)
        event_id = str(event.get("id") or "")
        return (created_at.isoformat(), event_id)

    @staticmethod
    def _governance_event_datetime(event: Dict[str, Any]) -> datetime:
        raw = event.get("created_at")
        if isinstance(raw, datetime):
            return raw
        text = str(raw or "").strip()
        if not text:
            return datetime.utcnow()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return datetime.utcnow()

    @classmethod
    def _status_from_governance_event(
        cls,
        event: Dict[str, Any],
    ) -> Optional[AutonomousChainTaskStatus]:
        execution_result = cls._dict_value(event.get("execution_result"))
        task_projection = cls._dict_value(
            execution_result.get("autonomous_task_projection")
        )
        projected_status = str(task_projection.get("status") or "").strip().lower()
        if projected_status in cls._all_task_statuses():
            return projected_status  # type: ignore[return-value]
        decision = cls._enum_value(event.get("decision")).strip().lower()
        if decision in cls._GOVERNANCE_DECISION_TO_STATUS:
            return cls._GOVERNANCE_DECISION_TO_STATUS[decision]
        event_type = cls._enum_value(event.get("event_type")).strip().lower()
        if event_type == "memory_maintenance":
            return "approved"
        if event_type == "self_evolution_approval":
            return "approved"
        if event_type == "self_evolution_defer":
            return "deferred"
        if event_type == "self_evolution_cancel":
            return "cancelled"
        return None

    @classmethod
    def _all_task_statuses(cls) -> frozenset[str]:
        return frozenset(
            cls._AUTONOMOUS_CHAIN_LIVE_STATUSES
            | cls._WRITEBACK_HISTORY_STATUSES
            | {"cancelled"}
        )

    @classmethod
    def _task_from_governance_events(
        cls,
        task_id: str,
        rows: List[Dict[str, Any]],
    ) -> Optional[AutonomousChainTask]:
        concrete_rows = [
            row for row in rows if cls._status_from_governance_event(row) is not None
        ]
        if not concrete_rows:
            return None
        first = concrete_rows[0]
        latest = concrete_rows[-1]
        status = cls._status_from_governance_event(latest)
        if status is None:
            return None

        latest_execution_result = cls._dict_value(latest.get("execution_result"))
        latest_task_projection = cls._dict_value(
            latest_execution_result.get("autonomous_task_projection")
        )
        if latest_task_projection:
            task = AutonomousChainTask.model_validate(latest_task_projection)
            task.metadata = {
                **dict(task.metadata or {}),
                "recovered_from_mem_governance": True,
                "latest_governance_event_id": str(latest.get("id") or ""),
            }
            return task

        recovery_projection = cls._coalesce_governance_event_mapping(
            concrete_rows,
            "execution_result",
        )
        profile_event = {**latest, "execution_result": recovery_projection}
        runtime_profile = cls._runtime_profile_from_governance_event(profile_event)
        git_lineage = cls._coalesce_governance_event_mapping(
            concrete_rows,
            "git_lineage",
        )
        evidence_refs = list(
            dict.fromkeys(
                str(reference).strip()
                for row in concrete_rows
                for reference in list(row.get("evidence_refs") or [])
                if str(reference).strip()
            )
        )
        event_ids = [
            str(row.get("id") or "").strip()
            for row in concrete_rows
            if str(row.get("id") or "").strip()
        ]
        event_types = [
            cls._enum_value(row.get("event_type")).strip()
            for row in concrete_rows
            if cls._enum_value(row.get("event_type")).strip()
        ]
        title = (
            str(
                recovery_projection.get("title")
                or recovery_projection.get("task_title")
                or ""
            ).strip()
            or cls._reason_title(latest)
            or f"Recovered autonomous task {task_id[:8]}"
        )
        summary = (
            str(
                recovery_projection.get("summary")
                or recovery_projection.get("task_summary")
                or ""
            ).strip()
            or str(latest.get("reason") or "").strip()
            or "Recovered from Mem governance event history."
        )

        task = AutonomousChainTask(
            task_id=task_id,
            trace_id=str(recovery_projection.get("trace_id") or uuid.uuid4()),
            title=title,
            summary=summary,
            task_type=str(
                recovery_projection.get("task_type")
                or runtime_profile["governance_task_type"]
            ),
            governance_task_type=runtime_profile["governance_task_type"],
            task_family=runtime_profile["task_family"],
            execution_kind=runtime_profile["execution_kind"],
            source="mem_governance_recovery",
            priority=str(recovery_projection.get("priority") or "normal"),
            status=status,
            created_at=cls._governance_event_datetime(first),
            updated_at=cls._governance_event_datetime(latest),
            metadata={
                "source": "mem_governance_recovery",
                "recovered_from_mem_governance": True,
                "recovered_event_ids": event_ids,
                "recovered_event_types": event_types,
                "latest_governance_event_id": str(latest.get("id") or ""),
                "latest_governance_decision": cls._enum_value(latest.get("decision")),
                "body_id": cls._latest_nonempty_event_value(concrete_rows, "body_id"),
                "execution_result": latest_execution_result,
                "recovery_projection": recovery_projection,
            },
            evidence={
                "mem_governance": {
                    "event_ids": event_ids,
                    "latest_event_type": cls._enum_value(latest.get("event_type")),
                    "latest_reason": str(latest.get("reason") or ""),
                    "git_lineage": git_lineage,
                    "evidence_refs": evidence_refs,
                }
            },
            constraints=cls._dict_value(recovery_projection.get("constraints")),
        )
        task.decision_reason = str(latest.get("reason") or "")
        task.decision_history = [
            AutonomousChainTaskDecision(
                decision_id=str(row.get("id") or uuid.uuid4()),
                status=row_status,
                task_type=task.task_type,
                governance_task_type=task.governance_task_type,
                task_family=task.task_family,
                execution_kind=task.execution_kind,
                trace_id=task.trace_id,
                decided_at=cls._governance_event_datetime(row),
                actor=str(row.get("source_actor") or "mem_governance"),
                reason=str(row.get("reason") or ""),
                context={
                    "source": "mem_governance_recovery",
                    "event_type": cls._enum_value(row.get("event_type")),
                    "body_id": str(row.get("body_id") or ""),
                },
            )
            for row in concrete_rows
            if (row_status := cls._status_from_governance_event(row)) is not None
        ]
        return task

    @classmethod
    def _runtime_profile_from_governance_event(cls, event: Dict[str, Any]) -> Dict[str, Any]:
        execution_result = cls._dict_value(event.get("execution_result"))
        runtime_profile = cls._dict_value(execution_result.get("runtime_task_profile"))
        event_type = cls._enum_value(event.get("event_type")).strip().lower()
        default_family = "general_self_evolution"
        if event_type == "memory_maintenance":
            default_family = "memory_maintenance"
        return derive_runtime_task_profile(
            task_type=(
                runtime_profile.get("governance_task_type")
                or execution_result.get("task_type")
                or "self_evolution"
            ),
            governance_task_type=runtime_profile.get("governance_task_type"),
            task_family=(
                runtime_profile.get("task_family")
                or execution_result.get("task_family")
            ),
            execution_kind=(
                runtime_profile.get("execution_kind")
                or execution_result.get("execution_kind")
            ),
            default_task_family=default_family,
        )

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(value.value if hasattr(value, "value") else value or "")

    @staticmethod
    def _dict_value(value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "to_dict"):
            try:
                payload = value.to_dict()
                if isinstance(payload, dict):
                    return dict(payload)
            except Exception:
                pass
        return {}

    @classmethod
    def _coalesce_governance_event_mapping(
        cls,
        rows: Iterable[Dict[str, Any]],
        field: str,
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for row in rows:
            merged = cls._merge_nonempty_mapping(
                merged,
                cls._dict_value(row.get(field)),
            )
        return merged

    @classmethod
    def _merge_nonempty_mapping(
        cls,
        current: Dict[str, Any],
        incoming: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(current)
        for key, value in incoming.items():
            if isinstance(value, dict):
                nested = cls._merge_nonempty_mapping(
                    cls._dict_value(merged.get(key)),
                    value,
                )
                if nested:
                    merged[key] = nested
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, (list, tuple, set)) and not value:
                continue
            merged[key] = value
        return merged

    @staticmethod
    def _latest_nonempty_event_value(
        rows: Iterable[Dict[str, Any]],
        field: str,
    ) -> str:
        values = [str(row.get(field) or "").strip() for row in rows]
        return next((value for value in reversed(values) if value), "")

    @staticmethod
    def _reason_title(event: Dict[str, Any]) -> str:
        reason = str(event.get("reason") or "").strip()
        if not reason:
            return ""
        return reason[:80]

