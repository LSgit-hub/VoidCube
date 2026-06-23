from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from VoidCube_core.utils import atomic_json_write
from systems.evolution_boundary import validate_agent_evolution_changes
from systems.runtime_task_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_type,
)

SelfEvolutionTaskStatus = Literal["planned", "deferred", "approved", "running", "paused", "cancelled", "completed", "failed"]
SelfEvolutionExecutionRequestKind = Literal[
    "body_upgrade",
    "body_switch",
    "memory_maintenance",
    "general_self_evolution",
]
SelfEvolutionExecutionRequestStatus = Literal["approved_for_execution"]


class SelfEvolutionGitLineage(BaseModel):
    source_branch: Optional[str] = None
    source_commit: Optional[str] = None
    candidate_branch: Optional[str] = None
    candidate_commit: Optional[str] = None
    active_ref: Optional[str] = None
    rollback_ref: Optional[str] = None
    rollback_commit: Optional[str] = None
    diff_summary: str = ""
    changed_files: List[str] = Field(default_factory=list)


class SelfEvolutionExecutionRequest(BaseModel):
    """Formal Mem/supervisor handoff contract consumed by executors.

    CLI and HTTP operations may test the executor surface, but a formal
    self-evolution execution needs this governance snapshot.
    """

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = "self_evolution"
    governance_task_type: Optional[str] = None
    task_family: Optional[str] = None
    execution_kind: Optional[str] = None
    decision_id: Optional[str] = None
    kind: SelfEvolutionExecutionRequestKind = "body_upgrade"
    status: SelfEvolutionExecutionRequestStatus = "approved_for_execution"
    source_actor: str = "mem_supervisor"
    source_service: Optional[str] = None
    target_slot_id: Optional[str] = None
    target_service: Optional[str] = None
    session_id: Optional[str] = None
    git_lineage: SelfEvolutionGitLineage = Field(default_factory=SelfEvolutionGitLineage)
    probe_report_ref: Optional[str] = None
    idle_window_evidence: Dict[str, Any] = Field(default_factory=dict)
    governor_decision: Dict[str, Any] = Field(default_factory=dict)
    rollback_plan: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _require_self_evolution_safety_evidence(self) -> "SelfEvolutionExecutionRequest":
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
        if self.kind not in {"body_upgrade", "body_switch"}:
            return self
        missing = []
        if not self.target_slot_id:
            missing.append("target_slot_id")
        if not self.git_lineage.candidate_commit:
            missing.append("git_lineage.candidate_commit")
        if not self.git_lineage.rollback_commit:
            missing.append("git_lineage.rollback_commit")
        if not self.git_lineage.changed_files:
            missing.append("git_lineage.changed_files")
        if missing:
            raise ValueError(
                "Formal body self-evolution execution request is missing: "
                + ", ".join(missing)
            )
        validate_agent_evolution_changes(self.git_lineage.changed_files)
        return self


class SelfEvolutionTaskDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: SelfEvolutionTaskStatus
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
    def _normalize_runtime_profile(self) -> "SelfEvolutionTaskDecision":
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


class SelfEvolutionTask(BaseModel):
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
    status: SelfEvolutionTaskStatus = "planned"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    decision_reason: str = ""
    decision_history: List[SelfEvolutionTaskDecision] = Field(default_factory=list)
    execution_request: Optional[SelfEvolutionExecutionRequest] = None

    @model_validator(mode="after")
    def _normalize_runtime_profile(self) -> "SelfEvolutionTask":
        runtime_task_profile = derive_runtime_task_profile(
            task_type=self.task_type,
            governance_task_type=(
                self.governance_task_type
                or self.metadata.get("governance_task_type")
            ),
            task_family=self.task_family or self.metadata.get("task_family"),
            execution_kind=(
                self.execution_kind
                or self.metadata.get("execution_kind")
                or self.metadata.get("task_family")
            ),
            default_task_family="general_self_evolution",
        )
        self.governance_task_type = runtime_task_profile["governance_task_type"]
        self.task_family = runtime_task_profile["task_family"]
        self.execution_kind = runtime_task_profile["execution_kind"]
        return self



# NOTE(SB-02): The self-evolution queue JSON file is runtime coordination state,
# not an authoritative store.  It can be rebuilt from Mem governance history
# if lost.  The Mem repository (governance_event table) is the true source of
# record for governance decisions.  See state-boundary.md §4.

class SelfEvolutionTaskQueueSnapshot(BaseModel):
    version: int = 1
    tasks: List[SelfEvolutionTask] = Field(default_factory=list)


class SelfEvolutionTaskQueue:
    def __init__(self, storage_path: str | Path) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.storage_path.exists():
            self._write_snapshot(SelfEvolutionTaskQueueSnapshot())

    def list_tasks(self, *, status: Optional[SelfEvolutionTaskStatus] = None) -> List[SelfEvolutionTask]:
        snapshot = self._load_snapshot()
        tasks = snapshot.tasks
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return tasks

    def get_task(self, task_id: str) -> Optional[SelfEvolutionTask]:
        snapshot = self._load_snapshot()
        for task in snapshot.tasks:
            if task.task_id == task_id:
                return task
        return None

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
    ) -> SelfEvolutionTask:
        with self._lock:
            snapshot = self._load_snapshot()
            task = SelfEvolutionTask(
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
        status: SelfEvolutionTaskStatus,
        decision_id: Optional[str] = None,
        actor: str = "supervisor",
        reason: str = "",
        context: Optional[Dict[str, Any]] = None,
        execution_request: Optional[SelfEvolutionExecutionRequest] = None,
    ) -> SelfEvolutionTask:
        with self._lock:
            snapshot = self._load_snapshot()
            for index, task in enumerate(snapshot.tasks):
                if task.task_id != task_id:
                    continue
                task.status = status
                task.updated_at = datetime.utcnow()
                task.decision_reason = reason
                task.decision_history.append(
                    SelfEvolutionTaskDecision(
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
        execution_request: Optional[SelfEvolutionExecutionRequest] = None,
    ) -> SelfEvolutionTask:
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

    def _load_snapshot(self) -> SelfEvolutionTaskQueueSnapshot:
        if not self.storage_path.exists():
            return SelfEvolutionTaskQueueSnapshot()
        raw = self.storage_path.read_text(encoding="utf-8").strip()
        if not raw:
            return SelfEvolutionTaskQueueSnapshot()
        return SelfEvolutionTaskQueueSnapshot.model_validate_json(raw)

    def _write_snapshot(self, snapshot: SelfEvolutionTaskQueueSnapshot) -> None:
        atomic_json_write(
            self.storage_path,
            snapshot.model_dump(mode="json"),
        )
