"""Quality and retry policy for compressed-memory lifecycle escalation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from systems.memory.quality_signals import identifiers, source_support


@dataclass(frozen=True, slots=True)
class LifecycleQuality:
    passed: bool
    source_support: float
    identifier_fidelity: float
    unsupported_identifiers: tuple[str, ...]
    failed_checks: tuple[str, ...]


def lifecycle_age_thresholds(config) -> tuple[tuple[tuple[str, int], int], ...]:
    """Return the configured Event-to-Final lifecycle thresholds."""
    return (
        (("event", 0), int(config.lifecycle_event_to_scene_days)),
        (("scene", 1), int(config.lifecycle_scene_to_arc_days)),
        (("arc", 2), int(config.lifecycle_arc_to_epoch_days)),
        (("epoch", 3), int(config.lifecycle_epoch_to_final_days)),
        (("epoch", 4), int(config.lifecycle_final_review_days)),
    )


def evaluate_lifecycle_quality(
    *,
    source_title: str,
    source_summary: str,
    proposed_title: str,
    proposed_summary: str,
    min_source_support: float,
    min_identifier_fidelity: float,
) -> LifecycleQuality:
    """Validate an abstraction against its immediate lower-level summary.

    Lifecycle escalation compares abstract text with already compressed text,
    so its thresholds are intentionally distinct from turn-to-event quality.
    """
    source_text = f"{source_title} {source_summary}".strip()
    proposed_text = f"{proposed_title} {proposed_summary}".strip()
    support = source_support(proposed_text, source_text)
    proposed_ids = identifiers(proposed_text)
    source_ids = identifiers(source_text)
    unsupported = tuple(sorted(proposed_ids - source_ids))
    fidelity = (
        1.0 if not proposed_ids else len(proposed_ids & source_ids) / len(proposed_ids)
    )
    failed: list[str] = []
    if support < min_source_support:
        failed.append("source_support")
    if fidelity < min_identifier_fidelity:
        failed.append("identifier_fidelity")
    return LifecycleQuality(
        passed=not failed,
        source_support=round(support, 6),
        identifier_fidelity=round(fidelity, 6),
        unsupported_identifiers=unsupported,
        failed_checks=tuple(failed),
    )


def record_lifecycle_rejection(
    conn,
    *,
    memory_id: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
    reason: str,
    now: datetime,
    max_retries: int,
    retry_base_hours: float,
) -> int:
    row = conn.execute(
        "SELECT lifecycle_retry_count FROM compressed_memories WHERE memory_id = ? "
        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
        (memory_id, owner_id, workspace_id, memory_domain),
    ).fetchone()
    retry_count = int(row[0] or 0) + 1 if row else 1
    retry_after = None
    if retry_count < max_retries:
        retry_after = (
            now + timedelta(hours=retry_base_hours * (2 ** (retry_count - 1)))
        ).isoformat()
    conn.execute(
        "UPDATE compressed_memories SET lifecycle_retry_count = ?, "
        "lifecycle_retry_after = ?, lifecycle_last_error = ? WHERE memory_id = ? "
        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
        (retry_count, retry_after, reason, memory_id, owner_id, workspace_id, memory_domain),
    )
    return retry_count
