"""Stable data contracts for local screen perception.

These records are intentionally model-neutral.  A capture backend, OCR
engine, YOLO/OmniParser adapter, or local VL model may populate them, but none
of those implementations is allowed to change the timeline contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


SCHEMA_VERSION = "perception.v1"


def utc_now() -> datetime:
    """Return an aware UTC timestamp used by perception records."""

    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    """Serialize timestamps consistently for JSON and local storage."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _clean_text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class PerceptionRecord:
    """One short-lived structured observation produced from a frame."""

    record_id: str
    observed_at: datetime
    source: tuple[str, ...] = ()
    application: str = ""
    window_title: str = ""
    scene: str = "unknown"
    summary: str = ""
    visible_text: tuple[str, ...] = ()
    objects: tuple[dict[str, Any], ...] = ()
    confidence: float = 0.0
    sensitivity: str = "normal"
    capture_session_id: str = ""
    sequence: int = 0
    uncertainties: tuple[str, ...] = ()
    coverage_gaps: tuple[str, ...] = ()
    expires_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id must not be empty")
        self.source = tuple(_clean_text(item, limit=80) for item in self.source if str(item).strip())
        self.application = _clean_text(self.application, limit=200)
        self.window_title = _clean_text(self.window_title, limit=500)
        self.scene = _clean_text(self.scene, limit=120).lower() or "unknown"
        self.summary = _clean_text(self.summary)
        self.visible_text = tuple(_clean_text(item) for item in self.visible_text if str(item).strip())
        self.objects = tuple(dict(item) for item in self.objects if isinstance(item, Mapping))
        self.confidence = _ratio(self.confidence)
        self.sensitivity = _clean_text(self.sensitivity, limit=40).lower() or "normal"
        self.uncertainties = tuple(_clean_text(item) for item in self.uncertainties if str(item).strip())
        self.coverage_gaps = tuple(_clean_text(item) for item in self.coverage_gaps if str(item).strip())

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = iso_timestamp(self.observed_at)
        if self.expires_at is not None:
            payload["expires_at"] = iso_timestamp(self.expires_at)
        payload["source"] = list(self.source)
        payload["visible_text"] = list(self.visible_text)
        payload["objects"] = [dict(item) for item in self.objects]
        payload["uncertainties"] = list(self.uncertainties)
        payload["coverage_gaps"] = list(self.coverage_gaps)
        return payload


@dataclass(slots=True)
class SceneState:
    """Latest scene projection, not a replacement for historical records."""

    scene: str = "unknown"
    application: str = ""
    window_title: str = ""
    activity: str = ""
    media_position_seconds: float | None = None
    last_change_at: datetime | None = None
    observed_at: datetime | None = None
    confidence: float = 0.0
    status: str = "unavailable"
    age_ms: int | None = None
    sequence: int = 0

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or utc_now()
        age_ms = self.age_ms
        if self.observed_at is not None:
            observed = self.observed_at
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age_ms = max(0, int((current - observed).total_seconds() * 1000))
        return {
            "schema_version": SCHEMA_VERSION,
            "scene": self.scene,
            "application": self.application,
            "window_title": self.window_title,
            "activity": self.activity,
            "media_position_seconds": self.media_position_seconds,
            "last_change_at": iso_timestamp(self.last_change_at) if self.last_change_at else None,
            "observed_at": iso_timestamp(self.observed_at) if self.observed_at else None,
            "confidence": _ratio(self.confidence),
            "status": self.status,
            "age_ms": age_ms,
            "sequence": self.sequence,
        }


@dataclass(slots=True)
class PerceptionEvent:
    """A deduplicatable event that may trigger local or remote reasoning."""

    event_id: str
    event_type: str
    observed_at: datetime
    scene: str = "unknown"
    summary: str = ""
    confidence: float = 0.0
    source_record_ids: tuple[str, ...] = ()
    sensitivity: str = "normal"
    remote_eligible: bool = False
    dedupe_key: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.event_type.strip():
            raise ValueError("event_id and event_type must not be empty")
        self.event_type = _clean_text(self.event_type, limit=80).lower()
        self.scene = _clean_text(self.scene, limit=120).lower() or "unknown"
        self.summary = _clean_text(self.summary)
        self.confidence = _ratio(self.confidence)
        self.source_record_ids = tuple(
            _clean_text(item, limit=120) for item in self.source_record_ids if str(item).strip()
        )
        self.sensitivity = _clean_text(self.sensitivity, limit=40).lower() or "normal"
        self.dedupe_key = _clean_text(self.dedupe_key, limit=240)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = iso_timestamp(self.observed_at)
        payload["source_record_ids"] = list(self.source_record_ids)
        return payload


@dataclass(slots=True)
class TimelineSegment:
    """A durable candidate summary produced from a bounded record interval."""

    segment_id: str
    start_at: datetime
    end_at: datetime
    scene: str = "unknown"
    application: str = ""
    summary: str = ""
    key_events: tuple[str, ...] = ()
    source_record_ids: tuple[str, ...] = ()
    source_count: int = 0
    confidence: float = 0.0
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    coverage_gaps: tuple[str, ...] = ()
    provisional: bool = False
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start_at"] = iso_timestamp(self.start_at)
        payload["end_at"] = iso_timestamp(self.end_at)
        for key in (
            "key_events",
            "source_record_ids",
            "facts",
            "inferences",
            "unknowns",
            "coverage_gaps",
        ):
            payload[key] = list(getattr(self, key))
        return payload


__all__ = [
    "SCHEMA_VERSION",
    "PerceptionEvent",
    "PerceptionRecord",
    "SceneState",
    "TimelineSegment",
    "iso_timestamp",
    "utc_now",
]
