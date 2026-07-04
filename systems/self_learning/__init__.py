"""Legacy self-learning conclusion contracts.

This package is a compatibility record/payload layer for historical learning
conclusions. It is not the autonomous task executor; current autonomous-chain
execution is owned by the API-A autonomous executor, with task production and
governance owned by Supervisor.
"""

from .models import (
    ExperimentRecord,
    LearningConclusion,
    LearningRecommendation,
    LearningSession,
    LearningTopic,
    SupervisorConclusionSubmission,
    SupervisorTaskProposal,
)

__all__ = [
    "ExperimentRecord",
    "LearningConclusion",
    "LearningRecommendation",
    "LearningSession",
    "LearningTopic",
    "SupervisorConclusionSubmission",
    "SupervisorTaskProposal",
]
