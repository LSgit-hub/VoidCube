from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def isoformat_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return isoformat_z(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


class TimePrecision(str, Enum):
    EXACT = "exact"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    APPROX = "approx"


class Status(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    CLOSED = "closed"
    SUPERSEDED = "superseded"


class MainOrSide(str, Enum):
    MAIN = "main"
    SIDE = "side"
    UNDETERMINED = "undetermined"


class EventKind(str, Enum):
    DECISION = "decision"
    PROGRESS = "progress"
    BLOCKER = "blocker"
    SHIFT = "shift"
    COMPLETION = "completion"
    CONFLICT = "conflict"
    CORRECTION = "correction"


class ImpactScope(str, Enum):
    LOCAL = "local"
    THREAD = "thread"
    ARC = "arc"
    EPOCH = "epoch"


class ArcState(str, Enum):
    EMERGING = "emerging"
    ACTIVE = "active"
    STALLED = "stalled"
    DORMANT = "dormant"
    RESOLVED = "resolved"


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    DEFINITION = "definition"
    FACT = "fact"


class CertaintyState(str, Enum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    PENDING_VERIFICATION = "pending_verification"
    DISPUTED = "disputed"
    CONFIRMED = "confirmed"


@dataclass(slots=True)
class TemporalSpan:
    start: datetime
    end: datetime
    precision: TimePrecision
    confidence: float
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": isoformat_z(self.start),
            "end": isoformat_z(self.end),
            "precision": self.precision.value,
            "confidence": self.confidence,
            "source_text": self.source_text,
        }


@dataclass(slots=True)
class TranscriptTurn:
    turn_id: str
    speaker: str
    text: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "speaker": self.speaker,
            "text": self.text,
            "timestamp": isoformat_z(self.timestamp),
        }


@dataclass(slots=True)
class BaseMemoryUnit:
    id: str
    type: str
    title: str
    summary: str
    timespan_start: datetime
    timespan_end: datetime
    time_precision: TimePrecision
    importance: float
    confidence: float
    status: Status = Status.ACTIVE
    main_or_side: MainOrSide = MainOrSide.UNDETERMINED
    topics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    parent_ids: list[str] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    compression_level: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_reviewed_at: datetime = field(default_factory=utc_now)

    def duration(self) -> timedelta:
        return self.timespan_end - self.timespan_start

    def touch(self) -> None:
        now = utc_now()
        self.updated_at = now
        self.last_reviewed_at = now

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for item in fields(self):
            data[item.name] = _serialize(getattr(self, item.name))
        return data


@dataclass(slots=True)
class Event(BaseMemoryUnit):
    event_kind: EventKind = EventKind.PROGRESS
    novelty: float = 0.5
    impact_scope: ImpactScope = ImpactScope.LOCAL
    source_turns: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        title: str,
        summary: str,
        timespan: TemporalSpan,
        *,
        importance: float,
        confidence: float,
        event_kind: EventKind,
        impact_scope: ImpactScope,
        topics: list[str],
        entities: list[str],
        evidence_refs: list[str],
        source_turns: list[str],
        main_or_side: MainOrSide = MainOrSide.UNDETERMINED,
        novelty: float = 0.5,
    ) -> "Event":
        return cls(
            id=new_id("event"),
            type="event",
            title=title,
            summary=summary,
            timespan_start=timespan.start,
            timespan_end=timespan.end,
            time_precision=timespan.precision,
            importance=importance,
            confidence=confidence,
            main_or_side=main_or_side,
            topics=topics,
            entities=entities,
            evidence_refs=evidence_refs,
            compression_level=0,
            event_kind=event_kind,
            novelty=novelty,
            impact_scope=impact_scope,
            source_turns=source_turns,
        )


@dataclass(slots=True)
class Scene(BaseMemoryUnit):
    scene_goal: str = ""
    key_events: list[str] = field(default_factory=list)
    local_turning_points: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        title: str,
        summary: str,
        timespan: TemporalSpan,
        *,
        importance: float,
        confidence: float,
        main_or_side: MainOrSide,
        topics: list[str],
        entities: list[str],
        evidence_refs: list[str],
        child_ids: list[str],
        scene_goal: str,
        key_events: list[str],
        local_turning_points: list[str],
        open_questions: list[str],
    ) -> "Scene":
        return cls(
            id=new_id("scene"),
            type="scene",
            title=title,
            summary=summary,
            timespan_start=timespan.start,
            timespan_end=timespan.end,
            time_precision=timespan.precision,
            importance=importance,
            confidence=confidence,
            main_or_side=main_or_side,
            topics=topics,
            entities=entities,
            evidence_refs=evidence_refs,
            child_ids=child_ids,
            compression_level=1,
            scene_goal=scene_goal,
            key_events=key_events,
            local_turning_points=local_turning_points,
            open_questions=open_questions,
        )


@dataclass(slots=True)
class Arc(BaseMemoryUnit):
    arc_goal: str = ""
    arc_state: ArcState = ArcState.EMERGING
    drivers: list[str] = field(default_factory=list)
    obstacles: list[str] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    turning_points: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Epoch(BaseMemoryUnit):
    epoch_theme: str = ""
    major_arcs: list[str] = field(default_factory=list)
    chapter_shift: str = ""
    long_term_effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProfileMemory:
    id: str
    type: str
    memory_kind: MemoryKind
    subject: str
    predicate: str
    value: str
    summary: str
    confidence: float
    certainty_state: CertaintyState
    status: Status = Status.ACTIVE
    valid_from: datetime = field(default_factory=utc_now)
    valid_to: datetime | None = None
    evidence_refs: list[str] = field(default_factory=list)
    source_turns: list[str] = field(default_factory=list)
    parent_timeline_refs: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    conflict_refs: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_reviewed_at: datetime = field(default_factory=utc_now)

    def touch(self) -> None:
        now = utc_now()
        self.updated_at = now
        self.last_reviewed_at = now

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for item in fields(self):
            data[item.name] = _serialize(getattr(self, item.name))
        return data

    @classmethod
    def create(
        cls,
        *,
        memory_kind: MemoryKind,
        subject: str,
        predicate: str,
        value: str,
        summary: str,
        confidence: float,
        certainty_state: CertaintyState = CertaintyState.OBSERVED,
        status: Status = Status.ACTIVE,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        evidence_refs: list[str] | None = None,
        source_turns: list[str] | None = None,
        parent_timeline_refs: list[str] | None = None,
        supersedes: list[str] | None = None,
        conflict_refs: list[str] | None = None,
    ) -> "ProfileMemory":
        anchor_time = valid_from or utc_now()
        return cls(
            id=new_id("profile"),
            type="profile_memory",
            memory_kind=memory_kind,
            subject=subject,
            predicate=predicate,
            value=value,
            summary=summary,
            confidence=confidence,
            certainty_state=certainty_state,
            status=status,
            valid_from=anchor_time,
            valid_to=valid_to,
            evidence_refs=list(evidence_refs or []),
            source_turns=list(source_turns or []),
            parent_timeline_refs=list(parent_timeline_refs or []),
            supersedes=list(supersedes or []),
            conflict_refs=list(conflict_refs or []),
        )
