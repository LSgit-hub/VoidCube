"""Small immutable event records for cross-module communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    session_id: str
    turn_id: str = ""
    response_length: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemorySyncQueued:
    session_id: str
    status: str = "queued"
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemorySyncFailed:
    session_id: str
    error: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextCompressed:
    session_id: str
    before_messages: int
    after_messages: int
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CheckpointPersistFailed:
    session_id: str
    error: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AutonomousTaskStarted:
    task_id: str
    cycle_id: str = ""
    lease_id: str = ""
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class AutonomousTaskReviewed:
    task_id: str
    status: str
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    cycle_id: str = ""
    lease_id: str = ""
    attempt: int = 0
    memory_write_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class GovernanceDecisionMade:
    task_id: str
    decision: str
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    cycle_id: str = ""
    lease_id: str = ""
    attempt: int = 0
    memory_write_status: str = "unknown"


__all__ = [
    "AutonomousTaskReviewed",
    "AutonomousTaskStarted",
    "CheckpointPersistFailed",
    "ContextCompressed",
    "GovernanceDecisionMade",
    "MemorySyncFailed",
    "MemorySyncQueued",
    "TurnCompleted",
]
