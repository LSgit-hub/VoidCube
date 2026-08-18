"""Governed candidate authoring for autonomous evolution."""

from .agent_adapter import (
    AUTHORING_TOOL_NAMES,
    AUTHORING_TOOLSETS,
    AIAgentAuthoringAdapter,
)

from .models import (
    AuthoringAgentReport,
    AuthoringCommandEvidence,
    EvolutionAuthoringContext,
    EvolutionAuthoringResult,
    EvolutionAuthoringSpec,
    candidate_ref_for_task,
)
from .executor import (
    AuthoringAgent,
    EvolutionAuthoringExecutor,
)
from .repository import (
    EvolutionAuthoringImmutableConflict,
    EvolutionAuthoringRecordCorrupted,
    EvolutionAuthoringRepository,
    EvolutionAuthoringRepositoryError,
    JsonEvolutionAuthoringRepository,
)

__all__ = [
    "AUTHORING_TOOL_NAMES",
    "AUTHORING_TOOLSETS",
    "AIAgentAuthoringAdapter",
    "AuthoringAgentReport",
    "AuthoringAgent",
    "AuthoringCommandEvidence",
    "EvolutionAuthoringContext",
    "EvolutionAuthoringExecutor",
    "EvolutionAuthoringImmutableConflict",
    "EvolutionAuthoringRecordCorrupted",
    "EvolutionAuthoringRepository",
    "EvolutionAuthoringRepositoryError",
    "EvolutionAuthoringResult",
    "EvolutionAuthoringSpec",
    "JsonEvolutionAuthoringRepository",
    "candidate_ref_for_task",
]
