from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any

from .schema import _serialize, new_id, parse_datetime, utc_now


class GovernanceEventType(str, Enum):
    AUTONOMOUS_TASK_TRANSITION = "autonomous_task_transition"
    AUTONOMOUS_TASK_CLEAR = "autonomous_task_clear"
    CANDIDATE_REVIEW = "candidate_review"
    PROBE_APPROVAL = "probe_approval"
    PROBE_FAILURE = "probe_failure"
    SWITCH_APPROVAL = "switch_approval"
    SWITCH_REJECTION = "switch_rejection"
    WATCH_WINDOW_PASS = "watch_window_pass"
    WATCH_WINDOW_ROLLBACK = "watch_window_rollback"
    BOUNDARY_DEFER = "boundary_defer"
    SELF_EVOLUTION_APPROVAL = "self_evolution_approval"
    SELF_EVOLUTION_DEFER = "self_evolution_defer"
    SELF_EVOLUTION_CANCEL = "self_evolution_cancel"
    EXECUTION_OUTCOME = "execution_outcome"
    ROLLBACK_OUTCOME = "rollback_outcome"
    MEMORY_MAINTENANCE = "memory_maintenance"


class GovernanceDecision(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_WATCH = "approve_with_watch"
    DEFER = "defer"
    REJECT = "reject"
    CANCEL = "cancel"
    PAUSE = "pause"
    ROLLBACK_REQUIRED = "rollback_required"
    COMPLETED = "completed"
    FAILED = "failed"
    RECORD_ONLY = "record_only"


class GovernanceRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class GovernanceFailureType(str, Enum):
    BOUNDARY_VIOLATION = "boundary_violation"
    PROBE_FAILURE = "probe_failure"
    WATCH_WINDOW_FAILURE = "watch_window_failure"
    EXECUTION_FAILURE = "execution_failure"
    ROLLBACK_FAILURE = "rollback_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(slots=True)
class GovernanceGitLineage:
    source_branch: str | None = None
    source_commit: str | None = None
    candidate_branch: str | None = None
    candidate_commit: str | None = None
    active_ref: str | None = None
    rollback_ref: str | None = None
    rollback_commit: str | None = None
    diff_summary: str = ""
    changed_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialize(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "GovernanceGitLineage":
        return cls(**dict(payload or {}))


@dataclass(slots=True)
class GovernanceBoundaryReport:
    ok: bool
    changed_files: list[str] = field(default_factory=list)
    allowed_files: list[str] = field(default_factory=list)
    forbidden_files: list[str] = field(default_factory=list)
    unknown_files: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialize(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "GovernanceBoundaryReport | None":
        if payload is None:
            return None
        return cls(**dict(payload))


@dataclass(slots=True)
class GovernanceFailureSignature:
    failure_type: GovernanceFailureType
    primary_paths: list[str] = field(default_factory=list)
    probe_checks: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    similarity_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialize(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "GovernanceFailureSignature | None":
        if payload is None:
            return None
        data = dict(payload)
        data["failure_type"] = GovernanceFailureType(data["failure_type"])
        return cls(**data)


@dataclass(slots=True)
class GovernanceEvent:
    id: str
    type: str
    event_type: GovernanceEventType
    source_actor: str
    decision: GovernanceDecision
    reason: str
    created_at: datetime = field(default_factory=utc_now)
    task_id: str | None = None
    body_id: str | None = None
    risk_level: GovernanceRiskLevel = GovernanceRiskLevel.UNKNOWN
    confidence: float = 0.0
    git_lineage: GovernanceGitLineage = field(default_factory=GovernanceGitLineage)
    probe_report_ref: str | None = None
    evolution_boundary: GovernanceBoundaryReport | None = None
    execution_result: dict[str, Any] | None = None
    failure_signature: GovernanceFailureSignature | None = None
    evidence_refs: list[str] = field(default_factory=list)
    related_event_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        event_type: GovernanceEventType,
        source_actor: str,
        decision: GovernanceDecision,
        reason: str,
        task_id: str | None = None,
        body_id: str | None = None,
        risk_level: GovernanceRiskLevel = GovernanceRiskLevel.UNKNOWN,
        confidence: float = 0.0,
        git_lineage: GovernanceGitLineage | None = None,
        probe_report_ref: str | None = None,
        evolution_boundary: GovernanceBoundaryReport | None = None,
        execution_result: dict[str, Any] | None = None,
        failure_signature: GovernanceFailureSignature | None = None,
        evidence_refs: list[str] | None = None,
        related_event_ids: list[str] | None = None,
    ) -> "GovernanceEvent":
        return cls(
            id=new_id("gov"),
            type="governance_event",
            event_type=event_type,
            task_id=task_id,
            body_id=body_id,
            source_actor=source_actor,
            decision=decision,
            reason=reason,
            risk_level=risk_level,
            confidence=confidence,
            git_lineage=git_lineage or GovernanceGitLineage(),
            probe_report_ref=probe_report_ref,
            evolution_boundary=evolution_boundary,
            execution_result=execution_result,
            failure_signature=failure_signature,
            evidence_refs=list(evidence_refs or []),
            related_event_ids=list(related_event_ids or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialize(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GovernanceEvent":
        data = dict(payload)
        data["event_type"] = GovernanceEventType(data["event_type"])
        data["decision"] = GovernanceDecision(data["decision"])
        data["risk_level"] = GovernanceRiskLevel(data.get("risk_level", "unknown"))
        data["created_at"] = parse_datetime(data["created_at"])
        data["git_lineage"] = GovernanceGitLineage.from_dict(data.get("git_lineage"))
        data["evolution_boundary"] = GovernanceBoundaryReport.from_dict(
            data.get("evolution_boundary")
        )
        data["failure_signature"] = GovernanceFailureSignature.from_dict(
            data.get("failure_signature")
        )
        return cls(**data)
