"""UI-independent contracts for shared turn admission and scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping


class TurnLane(str, Enum):
    """The two application turn lanes exposed by Gateway scene projection."""

    USER_CHAT = "user_chat"
    SUPERVISOR_TASK = "supervisor_task"


class TurnPriority(IntEnum):
    """Default admission priorities; larger values are admitted first."""

    AUTONOMOUS = 10
    USER = 100


class SchedulerState(str, Enum):
    """Lifecycle state of one admitted or queued turn."""

    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SchedulerEventKind(str, Enum):
    """Observable scheduler transitions; adapters must not infer state from text."""

    QUEUED = "queued"
    STARTED = "started"
    WAITING = "waiting"
    CANCEL_REQUESTED = "cancel_requested"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    GATE_CHANGED = "gate_changed"


def _lane(value: TurnLane | str) -> TurnLane:
    try:
        return value if isinstance(value, TurnLane) else TurnLane(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown turn lane: {value!r}") from exc


def _state(value: SchedulerState | str) -> SchedulerState:
    try:
        return value if isinstance(value, SchedulerState) else SchedulerState(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown scheduler state: {value!r}") from exc


def _event_kind(value: SchedulerEventKind | str) -> SchedulerEventKind:
    try:
        return value if isinstance(value, SchedulerEventKind) else SchedulerEventKind(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown scheduler event: {value!r}") from exc


def _default_priority(lane: TurnLane) -> int:
    return int(TurnPriority.USER if lane is TurnLane.USER_CHAT else TurnPriority.AUTONOMOUS)


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """Immutable input admitted by a scheduler.

    ``prompt`` intentionally remains opaque so text and multimodal payloads can
    share one contract. Tool policy is copied at the boundary so callers cannot
    mutate scheduler input after submission.
    """

    request_id: str
    lane: TurnLane
    session_id: str
    prompt: Any
    priority: int | None = None
    tool_policy: Mapping[str, Any] = field(default_factory=dict)
    source: str = ""

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        session_id = str(self.session_id or "").strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        if not session_id:
            raise ValueError("session_id must not be empty")
        lane = _lane(self.lane)
        priority = _default_priority(lane) if self.priority is None else int(self.priority)
        if priority < 0:
            raise ValueError("priority must be non-negative")
        if not isinstance(self.tool_policy, Mapping):
            raise TypeError("tool_policy must be a mapping")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "lane", lane)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "tool_policy", dict(self.tool_policy))
        object.__setattr__(self, "source", str(self.source or "").strip())

    def summary(self, *, state: SchedulerState = SchedulerState.QUEUED) -> "TurnSummary":
        return TurnSummary(
            request_id=self.request_id,
            lane=self.lane,
            priority=int(self.priority or 0),
            source=self.source,
            state=state,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "lane": self.lane.value,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "priority": int(self.priority or 0),
            "tool_policy": dict(self.tool_policy),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TurnRequest":
        return cls(
            request_id=payload.get("request_id", ""),
            lane=payload.get("lane", ""),
            session_id=payload.get("session_id", ""),
            prompt=payload.get("prompt"),
            priority=payload.get("priority"),
            tool_policy=payload.get("tool_policy") or {},
            source=payload.get("source", ""),
        )


@dataclass(frozen=True, slots=True)
class TurnSummary:
    """Non-sensitive queue/active projection used by presentation adapters."""

    request_id: str
    lane: TurnLane
    priority: int
    source: str = ""
    state: SchedulerState = SchedulerState.QUEUED

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        priority = int(self.priority)
        if priority < 0:
            raise ValueError("priority must be non-negative")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "lane", _lane(self.lane))
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "source", str(self.source or "").strip())
        object.__setattr__(self, "state", _state(self.state))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "lane": self.lane.value,
            "priority": self.priority,
            "source": self.source,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TurnSummary":
        return cls(
            request_id=payload.get("request_id", ""),
            lane=payload.get("lane", ""),
            priority=payload.get("priority", 0),
            source=payload.get("source", ""),
            state=payload.get("state", SchedulerState.QUEUED.value),
        )


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    """Read-only scheduler projection for TUI, Gateway and diagnostics."""

    active: TurnSummary | None
    queued: tuple[TurnSummary, ...] = ()
    autonomous_gate: bool = False
    blocked_reason: str = ""
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        active = self.active
        if active is not None and not isinstance(active, TurnSummary):
            active = TurnSummary.from_dict(active)
        queued = tuple(
            item if isinstance(item, TurnSummary) else TurnSummary.from_dict(item)
            for item in self.queued
        )
        object.__setattr__(self, "active", active)
        object.__setattr__(self, "queued", queued)
        object.__setattr__(self, "autonomous_gate", bool(self.autonomous_gate))
        object.__setattr__(self, "blocked_reason", str(self.blocked_reason or ""))
        object.__setattr__(self, "updated_at", float(self.updated_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active.to_dict() if self.active else None,
            "queued": [item.to_dict() for item in self.queued],
            "autonomous_gate": self.autonomous_gate,
            "blocked_reason": self.blocked_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SchedulerSnapshot":
        active = payload.get("active")
        queued = payload.get("queued") or ()
        return cls(
            active=TurnSummary.from_dict(active) if isinstance(active, Mapping) else None,
            queued=tuple(TurnSummary.from_dict(item) for item in queued),
            autonomous_gate=payload.get("autonomous_gate", False),
            blocked_reason=payload.get("blocked_reason", ""),
            updated_at=payload.get("updated_at", 0.0),
        )


@dataclass(frozen=True, slots=True)
class SchedulerEvent:
    """One explicit scheduler transition emitted to presentation adapters."""

    kind: SchedulerEventKind
    request_id: str = ""
    lane: TurnLane | None = None
    state: SchedulerState = SchedulerState.IDLE
    timestamp: float = 0.0
    reason: str = ""
    blocked_reason: str = ""
    autonomous_gate: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _event_kind(self.kind))
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        if self.lane is not None:
            object.__setattr__(self, "lane", _lane(self.lane))
        object.__setattr__(self, "state", _state(self.state))
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "reason", str(self.reason or ""))
        object.__setattr__(self, "blocked_reason", str(self.blocked_reason or ""))
        if self.autonomous_gate is not None:
            object.__setattr__(self, "autonomous_gate", bool(self.autonomous_gate))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "request_id": self.request_id,
            "lane": self.lane.value if self.lane else None,
            "state": self.state.value,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "blocked_reason": self.blocked_reason,
            "autonomous_gate": self.autonomous_gate,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SchedulerEvent":
        lane = payload.get("lane")
        return cls(
            kind=payload.get("kind", ""),
            request_id=payload.get("request_id", ""),
            lane=lane if lane else None,
            state=payload.get("state", SchedulerState.IDLE.value),
            timestamp=payload.get("timestamp", 0.0),
            reason=payload.get("reason", ""),
            blocked_reason=payload.get("blocked_reason", ""),
            autonomous_gate=payload.get("autonomous_gate"),
        )


__all__ = [
    "SchedulerEvent",
    "SchedulerEventKind",
    "SchedulerSnapshot",
    "SchedulerState",
    "TurnLane",
    "TurnPriority",
    "TurnRequest",
    "TurnSummary",
]
