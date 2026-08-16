"""Canonical resource and lifecycle contracts for Memory persistence."""

from __future__ import annotations

import hashlib
from enum import Enum


class TurnCompressionStatus(str, Enum):
    PENDING = "pending"
    RETRY_WAIT = "retry_wait"
    COMPRESSED = "compressed"
    QUALITY_QUARANTINED = "quality_quarantined"


COMPRESSION_CANDIDATE_STATUSES = (
    TurnCompressionStatus.PENDING.value,
    TurnCompressionStatus.RETRY_WAIT.value,
)
TIER1_RECALLABLE_STATUSES = (
    TurnCompressionStatus.PENDING.value,
    TurnCompressionStatus.RETRY_WAIT.value,
    TurnCompressionStatus.QUALITY_QUARANTINED.value,
)

TIMELINE_PARENT_TYPE = {
    "event": "scene",
    "scene": "arc",
    "arc": "epoch",
}

# These predicates describe collections. A new value adds a slot instead of
# replacing a different value in the same collection.
SET_VALUED_PROFILE_PREDICATES = frozenset(
    {
        "allergy",
        "long_term_preference",
        "prefers",
        "requires",
    }
)

# These broad predicates came from the retired Tier 2 heuristic profile path.
LEGACY_HEURISTIC_PROFILE_PREDICATES = frozenset(
    {
        "is_default",
        "is_optional",
        "means",
        "prefers",
        "requires",
        "update_mode",
    }
)


def profile_slot_key(predicate: object, value: object) -> str:
    normalized_predicate = str(predicate or "").strip().lower()
    if normalized_predicate not in SET_VALUED_PROFILE_PREDICATES:
        return normalized_predicate
    normalized_value = " ".join(str(value or "").strip().lower().split())
    digest = hashlib.sha256(normalized_value.encode("utf-8")).hexdigest()[:24]
    return f"{normalized_predicate}:{digest}"


def expected_timeline_parent_type(memory_type: object) -> str | None:
    return TIMELINE_PARENT_TYPE.get(str(memory_type or "").strip().lower())


def is_derived_relation(child_type: object, referenced_type: object) -> bool:
    child = str(child_type or "").strip().lower()
    referenced = str(referenced_type or "").strip().lower()
    return TIMELINE_PARENT_TYPE.get(referenced) == child
