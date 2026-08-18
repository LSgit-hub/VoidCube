"""Structured events shared by application adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, TypeAlias

from .artifacts import Artifact
from .interaction import ApprovalRequest, ClarificationRequest
from .tool_events import ToolEvent


class SessionEventKind(str, Enum):
    STARTED = "session.started"
    RESUMED = "session.resumed"
    ENDED = "session.ended"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    kind: SessionEventKind
    session_id: str
    resumed: bool = False
    reason: str = ""


class TurnEventKind(str, Enum):
    STARTED = "turn.started"
    COMPLETED = "turn.completed"
    FAILED = "turn.failed"
    INTERRUPTED = "turn.interrupted"


@dataclass(frozen=True, slots=True)
class TurnEvent:
    kind: TurnEventKind
    session_id: str
    turn_id: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class MessageDelta:
    session_id: str
    turn_id: str
    text: str
    role: str = "assistant"


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    session_id: str
    turn_id: str
    usage: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactCreated:
    session_id: str
    turn_id: str
    artifact: Artifact


@dataclass(frozen=True, slots=True)
class ApprovalRequested:
    session_id: str
    turn_id: str
    request: ApprovalRequest


@dataclass(frozen=True, slots=True)
class ClarificationRequested:
    session_id: str
    turn_id: str
    request: ClarificationRequest


ApplicationEvent: TypeAlias = (
    SessionEvent
    | TurnEvent
    | MessageDelta
    | ToolEvent
    | ApprovalRequested
    | ClarificationRequested
    | UsageUpdated
    | ArtifactCreated
)
ApplicationEventSink: TypeAlias = Callable[[ApplicationEvent], None]


__all__ = [
    "ApplicationEvent",
    "ApplicationEventSink",
    "ApprovalRequested",
    "ArtifactCreated",
    "ClarificationRequested",
    "MessageDelta",
    "SessionEvent",
    "SessionEventKind",
    "ToolEvent",
    "TurnEvent",
    "TurnEventKind",
    "UsageUpdated",
]
