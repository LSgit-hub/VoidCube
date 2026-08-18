"""Immutable self-cognition facts and their repository boundary."""

from systems.self_cognition.collector import (
    DEFAULT_COLLECTOR_VERSION,
    SelfCognitionCollector,
)
from systems.self_cognition.models import (
    HealthMetric,
    ModuleDependency,
    RuntimeCapability,
    SelfCognitionSnapshot,
)
from systems.self_cognition.repository import (
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
