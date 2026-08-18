"""Governed candidate authoring for autonomous evolution."""

from systems.evolution_authoring.agent_adapter import (
    AUTHORING_TOOL_NAMES,
    AUTHORING_TOOLSETS,
    AIAgentAuthoringAdapter,
)

from systems.evolution_authoring.models import (
    AuthoringAgentReport,
    AuthoringCommandEvidence,
    EvolutionAuthoringContext,
    EvolutionAuthoringResult,
    EvolutionAuthoringSpec,
    candidate_ref_for_task,
)
from systems.evolution_authoring.executor import (
    AuthoringAgent,
    EvolutionAuthoringExecutor,
)
from systems.evolution_authoring.repository import (
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
