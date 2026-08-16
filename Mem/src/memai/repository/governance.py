from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import threading

from ..governance import (
    GovernanceDecision,
    GovernanceEvent,
    GovernanceEventType,
    GovernanceFailureType,
)

logger = logging.getLogger("memai.governance")


@dataclass(slots=True)
class GovernanceEventQuery:
    event_type: GovernanceEventType | str | None = None
    decision: GovernanceDecision | str | None = None
    task_id: str | None = None
    body_id: str | None = None
    candidate_commit: str | None = None
    rollback_commit: str | None = None
    changed_file: str | None = None
    violation: str | None = None
    failure_type: GovernanceFailureType | str | None = None
    similarity_key: str | None = None
    limit: int = 0


@dataclass(slots=True)
class GovernanceFailureSampleQuery:
    changed_files: list[str]
    failure_type: GovernanceFailureType | str | None = None
    similarity_keys: list[str] | None = None
    limit: int = 5


@dataclass(slots=True)
class GovernanceFailureSample:
    event: GovernanceEvent
    score: int
    matched_files: list[str]
    matched_similarity_keys: list[str]
    risk_flags: list[str]


@dataclass(slots=True)
class GovernanceEvidenceSummary:
    summary: str
    relevant_event_ids: list[str]
    risk_flags: list[str]
    recommendation: GovernanceDecision
    confidence: float
    samples: list[GovernanceFailureSample]


class GovernanceEventRepository:
    """Append-only governance event store for Mem's future decision memory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.retry_path = self.path.with_suffix(".retry.jsonl")
        self._lock = threading.RLock()

    def append(self, event: GovernanceEvent) -> GovernanceEvent:
        """Append with idempotency, write protection, and retry-log fallback.

        On write failure the event is appended to ``<path>.retry.jsonl`` so
        no governance event is silently lost (M-06).
        """
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if event.id in {item.id for item in self.list_events()}:
                return event
            try:
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            except Exception:
                logger.warning(
                    "Failed to write governance event %s — writing to retry log", event.id
                )
                try:
                    with self.retry_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                except Exception as retry_exc:
                    logger.error(
                        "Failed to write governance event %s to retry log — event lost", event.id
                    )
                    raise RuntimeError(
                        f"Governance event {event.id} could not be persisted"
                    ) from retry_exc
        return event

    def list_events(self, limit: int = 0) -> list[GovernanceEvent]:
        with self._lock:
            events: list[GovernanceEvent] = []
            seen_ids: set[str] = set()
            for path in (self.path, self.retry_path):
                if not path.exists():
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    event = GovernanceEvent.from_dict(json.loads(line))
                    if event.id in seen_ids:
                        continue
                    seen_ids.add(event.id)
                    events.append(event)
        if limit > 0:
            return events[-limit:]
        return events

    def query(self, query: GovernanceEventQuery) -> list[GovernanceEvent]:
        events = [event for event in self.list_events() if self._matches(event, query)]
        if query.limit > 0:
            return events[-query.limit :]
        return events

    def query_failure_samples(
        self, query: GovernanceFailureSampleQuery
    ) -> list[GovernanceFailureSample]:
        samples = [
            sample
            for event in self.list_events()
            if (sample := self._failure_sample(event, query)) is not None
        ]
        samples.sort(key=lambda item: (item.score, item.event.created_at), reverse=True)
        if query.limit > 0:
            return samples[: query.limit]
        return samples

    def summarize_governance_context(
        self, query: GovernanceFailureSampleQuery
    ) -> GovernanceEvidenceSummary:
        samples = self.query_failure_samples(query)
        event_ids = [sample.event.id for sample in samples]
        risk_flags = sorted(
            {
                risk_flag
                for sample in samples
                for risk_flag in sample.risk_flags
            }
        )
        highest_score = max((sample.score for sample in samples), default=0)
        if highest_score >= 8:
            recommendation = GovernanceDecision.DEFER
            confidence = 0.86
        elif highest_score >= 3:
            recommendation = GovernanceDecision.APPROVE_WITH_WATCH
            confidence = 0.68
        else:
            recommendation = GovernanceDecision.RECORD_ONLY
            confidence = 0.35

        if not samples:
            summary = "No similar governance failure samples were found."
        else:
            matched_files = sorted(
                {
                    matched_file
                    for sample in samples
                    for matched_file in sample.matched_files
                }
            )
            summary = (
                f"Found {len(samples)} similar governance failure sample(s). "
                f"Matched files: {', '.join(matched_files) if matched_files else 'none'}. "
                f"Risk flags: {', '.join(risk_flags) if risk_flags else 'none'}."
            )

        return GovernanceEvidenceSummary(
            summary=summary,
            relevant_event_ids=event_ids,
            risk_flags=risk_flags,
            recommendation=recommendation,
            confidence=confidence,
            samples=samples,
        )

    def _matches(self, event: GovernanceEvent, query: GovernanceEventQuery) -> bool:
        if query.event_type and event.event_type.value != _query_value(query.event_type):
            return False
        if query.decision and event.decision.value != _query_value(query.decision):
            return False
        if query.task_id and event.task_id != query.task_id:
            return False
        if query.body_id and event.body_id != query.body_id:
            return False
        if query.candidate_commit and (
            event.git_lineage.candidate_commit != query.candidate_commit
        ):
            return False
        if query.rollback_commit and (
            event.git_lineage.rollback_commit != query.rollback_commit
        ):
            return False
        if query.changed_file and query.changed_file not in event.git_lineage.changed_files:
            return False
        if query.violation and not (
            event.evolution_boundary
            and query.violation in event.evolution_boundary.violations
        ):
            return False
        if query.failure_type and not (
            event.failure_signature
            and event.failure_signature.failure_type.value == _query_value(query.failure_type)
        ):
            return False
        if query.similarity_key and not (
            event.failure_signature
            and query.similarity_key in event.failure_signature.similarity_keys
        ):
            return False
        return True

    def _failure_sample(
        self, event: GovernanceEvent, query: GovernanceFailureSampleQuery
    ) -> GovernanceFailureSample | None:
        if not event.failure_signature:
            return None
        if query.failure_type and (
            event.failure_signature.failure_type.value != _query_value(query.failure_type)
        ):
            return None

        query_files = set(query.changed_files)
        event_files = set(event.git_lineage.changed_files)
        event_files.update(event.failure_signature.primary_paths)
        if event.evolution_boundary:
            event_files.update(event.evolution_boundary.violations)

        query_similarity_keys = set(query.similarity_keys or [])
        event_similarity_keys = set(event.failure_signature.similarity_keys)
        matched_files = sorted(query_files & event_files)
        matched_similarity_keys = sorted(query_similarity_keys & event_similarity_keys)

        score = 0
        score += len(matched_files) * 3
        score += len(matched_similarity_keys) * 5
        if query.failure_type:
            score += 1

        if score <= 0:
            return None

        return GovernanceFailureSample(
            event=event,
            score=score,
            matched_files=matched_files,
            matched_similarity_keys=matched_similarity_keys,
            risk_flags=list(event.failure_signature.risk_flags),
        )


def _query_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
