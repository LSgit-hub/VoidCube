"""Immutable research knowledge and its repository boundary."""

from systems.research_knowledge.normalizer import (
    DEFAULT_FRESHNESS_TTL,
    DEFAULT_KNOWLEDGE_NORMALIZER_VERSION,
    KnowledgeNormalizationError,
    KnowledgeNormalizationReport,
    KnowledgeNormalizer,
    WebResearchClaim,
    WebResearchDocument,
    canonicalize_source_url,
    contains_prompt_injection,
    is_artifact_fresh,
)
from systems.research_knowledge.models import (
    KnowledgeArtifact,
    KnowledgeClaim,
    KnowledgeRelation,
    KnowledgeSource,
)
from systems.research_knowledge.repository import (
    JsonKnowledgeRepository,
    KnowledgeImmutableConflict,
    KnowledgeRecordCorrupted,
    KnowledgeRepository,
)

__all__ = [
    "JsonKnowledgeRepository",
    "DEFAULT_FRESHNESS_TTL",
    "DEFAULT_KNOWLEDGE_NORMALIZER_VERSION",
    "KnowledgeArtifact",
    "KnowledgeClaim",
    "KnowledgeImmutableConflict",
    "KnowledgeRecordCorrupted",
    "KnowledgeRelation",
    "KnowledgeRepository",
    "KnowledgeSource",
    "KnowledgeNormalizationError",
    "KnowledgeNormalizationReport",
    "KnowledgeNormalizer",
    "WebResearchClaim",
    "WebResearchDocument",
    "canonicalize_source_url",
    "contains_prompt_injection",
    "is_artifact_fresh",
]
