"""Immutable self-cognition facts and their repository boundary."""

from .collector import (
    DEFAULT_COLLECTOR_VERSION,
    SelfCognitionCollector,
)
from .models import (
    HealthMetric,
    ModuleDependency,
    RuntimeCapability,
    SelfCognitionSnapshot,
)
from .repository import (
    JsonSelfCognitionRepository,
    SelfCognitionImmutableConflict,
    SelfCognitionRecordCorrupted,
    SelfCognitionRepository,
)

__all__ = [
    "HealthMetric",
    "DEFAULT_COLLECTOR_VERSION",
    "JsonSelfCognitionRepository",
    "ModuleDependency",
    "RuntimeCapability",
    "SelfCognitionImmutableConflict",
    "SelfCognitionRecordCorrupted",
    "SelfCognitionRepository",
    "SelfCognitionSnapshot",
    "SelfCognitionCollector",
]
