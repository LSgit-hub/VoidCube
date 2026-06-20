from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

LearningTrigger = Literal["manual", "scheduled", "idle", "event"]
ExperimentOutcome = Literal["pending", "passed", "failed", "inconclusive"]
RecommendationType = Literal["observe", "study_next", "propose_experiment", "propose_evolution_task"]


class LearningTopic(BaseModel):
    topic_id: str
    title: str
    reason: str = ""
    source: str = "self_learning"
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LearningSession(BaseModel):
    session_id: str
    topic_id: str
    trigger: LearningTrigger = "scheduled"
    planned_minutes: int = 30
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: Literal["planned", "running", "completed", "interrupted"] = "planned"
    notes: List[str] = Field(default_factory=list)


class ExperimentRecord(BaseModel):
    experiment_id: str
    topic_id: str
    session_id: str
    hypothesis: str
    method: str
    observations: List[str] = Field(default_factory=list)
    outcome: ExperimentOutcome = "pending"
    compared_against: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LearningRecommendation(BaseModel):
    recommendation_type: RecommendationType = "observe"
    title: str
    summary: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)


class LearningConclusion(BaseModel):
    conclusion_id: str
    topic: LearningTopic
    session: LearningSession
    experiments: List[ExperimentRecord] = Field(default_factory=list)
    comparisons: List[str] = Field(default_factory=list)
    summary: str
    verified: bool = False
    recommendations: List[LearningRecommendation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SupervisorTaskProposal(BaseModel):
    title: str
    summary: str = ""
    task_type: str | None = None
    governance_task_type: str | None = None
    task_family: str | None = None
    execution_kind: str | None = None
    source: str = "self_learning"
    priority: str = "normal"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)


class SupervisorConclusionSubmission(BaseModel):
    source: str = "self_learning"
    conclusion_id: str
    topic_id: str
    title: str
    summary: str
    verified: bool = False
    recommendations: List[LearningRecommendation] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    proposals: List[SupervisorTaskProposal] = Field(default_factory=list)
