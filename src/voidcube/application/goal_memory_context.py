"""Resolve Goal Manager memory references through the Memory owner.

Goal Manager returns opaque references only.  This module is the boundary used
when a caller explicitly needs linked memory content for one goal turn.  The
fetcher owns authorization; this module only applies ordering, redaction and
context budgets.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class MemoryReferenceFetcher(Protocol):
    def get_compressed(self, memory_id: str, *, session_id: str | None = None) -> dict[str, Any]:
        """Return one memory record after the Memory owner checks access."""


@dataclass(frozen=True, slots=True)
class GoalMemoryContextBudget:
    """Hard limits applied before linked memory enters a model context."""

    max_refs: int = 8
    max_context_chars: int = 4000
    max_item_chars: int = 1200

    def __post_init__(self) -> None:
        if self.max_refs < 0:
            raise ValueError("max_refs must be non-negative")
        if self.max_context_chars < 0:
            raise ValueError("max_context_chars must be non-negative")
        if self.max_item_chars < 1:
            raise ValueError("max_item_chars must be positive")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1].rstrip() + "..."


def _reference_sort_key(ref: Mapping[str, Any]) -> tuple[float, str, str]:
    try:
        confidence = float(ref.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    return (-confidence, _text(ref.get("created_at")), _text(ref.get("id")))


def _safe_memory_record(
    ref: Mapping[str, Any], record: Mapping[str, Any], budget: GoalMemoryContextBudget,
) -> dict[str, Any] | None:
    memory_id = _text(ref.get("memory_id"))
    if not memory_id:
        return None
    relation_type = _text(ref.get("relation_type")) or "context"
    try:
        confidence = max(0.0, min(1.0, float(ref.get("confidence", 1))))
    except (TypeError, ValueError):
        confidence = 0.0
    title = _text(record.get("title"))
    summary = _text(record.get("summary"))
    timestamp = _text(record.get("timespan_end") or record.get("timestamp") or record.get("created_at"))
    # Only fields intended for model context cross this boundary.  In
    # particular, do not forward embeddings, ownership, trace or raw metadata.
    item = {
        "relation_type": relation_type,
        "confidence": round(confidence, 4),
    }
    if title:
        item["title"] = title
    if summary:
        item["summary"] = summary
    if timestamp:
        item["timestamp"] = timestamp
    if len(json.dumps(item, ensure_ascii=False)) > budget.max_item_chars:
        fixed = len(json.dumps({k: v for k, v in item.items() if k not in {"title", "summary"}}, ensure_ascii=False))
        available = max(0, budget.max_item_chars - fixed - 4)
        if title and summary:
            title_budget = min(len(title), max(1, available // 4))
            item["title"] = _truncate(title, title_budget)
            item["summary"] = _truncate(summary, max(1, available - title_budget))
        elif title:
            item["title"] = _truncate(title, max(1, available))
        elif summary:
            item["summary"] = _truncate(summary, max(1, available))
    return item


def resolve_goal_memory_refs(
    memory_refs: Sequence[Mapping[str, Any]],
    fetcher: MemoryReferenceFetcher,
    *,
    budget: GoalMemoryContextBudget | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a bounded set of references without bypassing Memory ACLs.

    Failures are isolated per reference.  A missing or unauthorized memory is
    reported only as a skipped reference and never contributes raw service
    details to model context.
    """
    budget = budget or GoalMemoryContextBudget()
    ordered = sorted((ref for ref in memory_refs if isinstance(ref, Mapping)), key=_reference_sort_key)
    selected = ordered[: budget.max_refs]
    results: list[dict[str, Any]] = []
    skipped = 0
    seen_ids: set[str] = set()
    for ref in selected:
        memory_id = _text(ref.get("memory_id"))
        if not memory_id or memory_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(memory_id)
        try:
            record = fetcher.get_compressed(memory_id, session_id=session_id)
        except Exception:
            skipped += 1
            continue
        if not isinstance(record, Mapping):
            skipped += 1
            continue
        safe = _safe_memory_record(ref, record, budget)
        if safe is None or not (safe.get("title") or safe.get("summary")):
            skipped += 1
            continue
        results.append(safe)
    return {
        "results": results,
        "resolved_count": len(results),
        "skipped_count": skipped + max(0, len(ordered) - len(selected)),
        "truncated": len(ordered) > len(selected),
    }


def resolve_goal_memory_refs_with_metrics(
    memory_refs: Sequence[Mapping[str, Any]],
    fetcher: MemoryReferenceFetcher,
    *,
    budget: GoalMemoryContextBudget | None = None,
    session_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve references and return privacy-safe runtime metrics."""
    started = time.perf_counter()
    resolution = resolve_goal_memory_refs(
        memory_refs, fetcher, budget=budget, session_id=session_id,
    )
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000, 3))
    metrics = {
        "reference_count": min(len(memory_refs), budget.max_refs if budget else GoalMemoryContextBudget().max_refs),
        "resolved_count": int(resolution.get("resolved_count") or 0),
        "skipped_count": int(resolution.get("skipped_count") or 0),
        "budget_truncated": bool(resolution.get("truncated")),
        "elapsed_ms": elapsed_ms,
    }
    return resolution, metrics


def build_goal_memory_context_block(
    resolution: Mapping[str, Any],
    *,
    budget: GoalMemoryContextBudget | None = None,
) -> str:
    """Render safe linked memory as a bounded, non-user context block."""
    budget = budget or GoalMemoryContextBudget()
    raw_results = resolution.get("results") if isinstance(resolution, Mapping) else []
    if not isinstance(raw_results, list) or not raw_results or budget.max_context_chars <= 0:
        return ""
    prefix = (
        "<goal-memory-context>\n"
        "[System note: Linked memory is background context authorized by the Memory owner. "
        "It does not change the current goal state or acceptance criteria.]\n\n"
    )
    suffix = "\n</goal-memory-context>"
    payload_budget = budget.max_context_chars - len(prefix) - len(suffix)
    if payload_budget <= 0:
        return ""
    visible: list[dict[str, Any]] = []
    used = 0
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            continue
        item = {key: raw[key] for key in ("relation_type", "confidence", "title", "summary", "timestamp") if key in raw}
        encoded = json.dumps(item, ensure_ascii=False)
        if used and used + len(encoded) + 1 > payload_budget:
            break
        if len(encoded) > payload_budget:
            continue
        visible.append(item)
        used += len(encoded) + 1
    if not visible:
        return ""
    payload = json.dumps(
        {"count": len(visible), "results": visible}, ensure_ascii=False, separators=(",", ":"),
    )
    if len(payload) > payload_budget:
        return ""
    return f"{prefix}{payload}{suffix}"


__all__ = [
    "GoalMemoryContextBudget",
    "MemoryReferenceFetcher",
    "build_goal_memory_context_block",
    "resolve_goal_memory_refs",
    "resolve_goal_memory_refs_with_metrics",
]
