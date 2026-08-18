"""Explicit evidence normalization and quality calculations for endogenous drive."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import re


_TOPIC_WORD_RE = re.compile(r"[a-zA-Z0-9_]{3,}")


def parse_timestamp(raw_timestamp: Any) -> Optional[datetime]:
    if not raw_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def item_evidence_quality(
    *,
    item: Dict[str, Any],
    source_reliability: float,
    supports: List[str],
    contradicts: List[str],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    quality_component = 0.0
    try:
        quality_component = _clamp01(float(item.get("quality_score") or 0.0))
    except (TypeError, ValueError):
        pass
    evidence_summary = list(item.get("evidence_summary") or [])
    evidence_bonus = min(len(evidence_summary), 4) * 0.06
    freshness_bonus = 0.0
    published_at = item.get("published_at") or item.get("completed_at")
    parsed_time = parse_timestamp(published_at)
    if parsed_time is not None:
        reference_time = now or datetime.now(timezone.utc)
        age_days = max(0, (reference_time - parsed_time).days)
        if age_days <= 14:
            freshness_bonus = 0.18
        elif age_days <= 90:
            freshness_bonus = 0.1
        else:
            freshness_bonus = 0.03
    base = (
        0.22
        + _clamp01(source_reliability) * 0.45
        + quality_component * 0.18
        + evidence_bonus
        + freshness_bonus
    )
    text = " ".join(
        [str(item.get("title") or ""), str(item.get("summary") or "")]
    ).strip()
    token_count = len({token.lower() for token in _TOPIC_WORD_RE.findall(text)})
    novelty_score = 0.2 if not text else round(_clamp01(0.18 + min(token_count, 12) * 0.055), 4)
    return {
        "confidence_score": round(_clamp01(base), 4),
        "novelty_score": novelty_score,
        "source_reliability": round(_clamp01(source_reliability), 4),
        "supports": list(supports),
        "contradicts": list(contradicts),
    }


def normalize_external_research_entries(entries: List[Any]) -> List[Dict[str, Any]]:
    evidence_rows: List[Dict[str, Any]] = []
    for raw in entries[:12]:
        text = str(raw or "").strip()
        if not text:
            continue
        if "::" in text:
            title, detail = text.split("::", 1)
            row = {
                "title": title.strip(),
                "summary": detail.strip(),
                "source": "configured_external_research",
            }
            row.update(
                item_evidence_quality(
                    item=row,
                    source_reliability=0.62,
                    supports=["external_research", "forward_direction"],
                    contradicts=[],
                )
            )
        else:
            row = {
                "title": text[:80],
                "summary": text,
                "source": "configured_external_research",
            }
            row.update(
                item_evidence_quality(
                    item=row,
                    source_reliability=0.58,
                    supports=["external_research"],
                    contradicts=[],
                )
            )
        evidence_rows.append(row)
    return evidence_rows


def normalize_external_research_file_payload(
    data: Any,
    *,
    source_path: str,
) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        items = data.get("entries")
        if isinstance(items, list):
            return normalize_external_research_items(items, source_path=source_path)
        return normalize_external_research_items([data], source_path=source_path)
    if isinstance(data, list):
        return normalize_external_research_items(data, source_path=source_path)
    return []


def normalize_external_research_items(
    items: List[Any],
    *,
    source_path: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items[:12]:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("topic") or "").strip()
            summary = str(
                item.get("summary") or item.get("note") or item.get("content") or ""
            ).strip()
            if not title and not summary:
                continue
            row: Dict[str, Any] = {
                "title": title or summary[:80],
                "summary": summary or title,
                "source": str(item.get("source") or "external_research_file"),
                "source_path": source_path,
            }
            if item.get("url"):
                row["url"] = str(item.get("url"))
            if item.get("published_at"):
                row["published_at"] = str(item.get("published_at"))
            if item.get("tags"):
                row["tags"] = [
                    str(tag).strip()
                    for tag in list(item.get("tags") or [])
                    if str(tag).strip()
                ][:6]
            row.update(
                item_evidence_quality(
                    item=row,
                    source_reliability=0.74 if row.get("url") else 0.64,
                    supports=["external_research", "forward_direction"],
                    contradicts=[],
                )
            )
        else:
            text = str(item or "").strip()
            if not text:
                continue
            row = {
                "title": text[:80],
                "summary": text,
                "source": "external_research_file",
                "source_path": source_path,
            }
            row.update(
                item_evidence_quality(
                    item=row,
                    source_reliability=0.56,
                    supports=["external_research"],
                    contradicts=[],
                )
            )
        rows.append(row)
    return rows


def normalize_recent_learning_evidence(
    completed_learning_tasks: List[Any],
) -> List[Dict[str, Any]]:
    evidence_rows: List[Dict[str, Any]] = []
    for task in completed_learning_tasks[:5]:
        if not isinstance(task, dict):
            continue
        title = str(task.get("title") or task.get("topic") or "").strip()
        if not title:
            continue
        row: Dict[str, Any] = {
            "title": title,
            "summary": str(task.get("summary") or "").strip()[:280],
            "quality_score": task.get("quality_score"),
            "completed_at": task.get("completed_at"),
            "task_family": task.get("task_family"),
            "execution_kind": task.get("execution_kind"),
        }
        evidence = task.get("evidence")
        if isinstance(evidence, dict):
            row["evidence_summary"] = [
                str(item).strip()
                for item in list(evidence.get("evidence_summary") or [])
                if str(item).strip()
            ][:4]
        row.update(
            item_evidence_quality(
                item=row,
                source_reliability=0.84,
                supports=["self_understanding", "learning_trace"],
                contradicts=[],
            )
        )
        evidence_rows.append(row)
    return evidence_rows


def research_freshness_hint(
    items: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> str:
    published_tokens = [
        str(item.get("published_at") or "").strip()
        for item in items
        if str(item.get("published_at") or "").strip()
    ]
    if not published_tokens:
        return "unknown"
    latest_seen: Optional[datetime] = None
    for token in published_tokens:
        parsed = parse_timestamp(token)
        if parsed is not None and (latest_seen is None or parsed > latest_seen):
            latest_seen = parsed
    if latest_seen is None:
        return "unknown"
    reference_time = now or datetime.now(timezone.utc)
    age_days = max(0, (reference_time - latest_seen).days)
    if age_days <= 14:
        return "fresh"
    if age_days <= 90:
        return "recent"
    return "stale"


def channel_strength_from_learning(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "weak"
    quality_scores: List[float] = []
    for item in items[:5]:
        try:
            quality_scores.append(_clamp01(float(item.get("quality_score") or 0.0)))
        except (TypeError, ValueError):
            continue
    if not quality_scores:
        return "moderate"
    avg = sum(quality_scores) / len(quality_scores)
    if avg >= 0.75:
        return "strong"
    if avg >= 0.4:
        return "moderate"
    return "weak"


def channel_confidence_from_learning(items: List[Dict[str, Any]]) -> float:
    if not items:
        return 0.22
    quality_scores: List[float] = []
    for item in items[:5]:
        try:
            quality_scores.append(_clamp01(float(item.get("quality_score") or 0.0)))
        except (TypeError, ValueError):
            continue
    if not quality_scores:
        return 0.45
    avg = sum(quality_scores) / len(quality_scores)
    return round(_clamp01(0.3 + avg * 0.6), 4)


def channel_confidence_from_body(shell_body_profile: Dict[str, Any]) -> float:
    status = str(shell_body_profile.get("profile_status") or "").strip().lower()
    if status == "ready":
        return 0.86
    if status in {"missing_worktree", "worktree_missing_on_disk"}:
        return 0.2
    return 0.45


def channel_strength_from_research(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "weak"
    if len(items) >= 3 and research_freshness_hint(items) in {"fresh", "recent"}:
        return "strong"
    return "moderate"


def channel_confidence_from_research(items: List[Dict[str, Any]]) -> float:
    if not items:
        return 0.18
    freshness = research_freshness_hint(items)
    freshness_bonus = {
        "fresh": 0.3,
        "recent": 0.22,
        "stale": 0.08,
        "unknown": 0.14,
    }.get(freshness, 0.12)
    source_count = len(
        {
            str(item.get("source") or "").strip()
            for item in items
            if str(item.get("source") or "").strip()
        }
    )
    return round(
        _clamp01(
            0.24
            + min(len(items), 4) * 0.08
            + source_count * 0.05
            + freshness_bonus
        ),
        4,
    )


def evidence_conflict_flags(
    *,
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    shell_body_profile: Dict[str, Any],
) -> List[str]:
    flags: List[str] = []
    if not recent_learning_evidence:
        flags.append("learning_missing_recent_history")
    if (
        recent_learning_evidence
        and channel_strength_from_learning(recent_learning_evidence) == "weak"
    ):
        flags.append("learning_weak_quality_signal")
    if shell_body_profile.get("profile_status") != "ready":
        flags.append("body_profile_incomplete")
    if not external_research_evidence:
        flags.append("research_missing_external_support")
    elif research_freshness_hint(external_research_evidence) == "stale":
        flags.append("research_stale_support")
    return flags


def build_evidence_graph(
    *,
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    shell_body_profile: Dict[str, Any],
) -> Dict[str, Any]:
    evidence_items: List[Dict[str, Any]] = []
    evidence_items.extend(recent_learning_evidence[:5])
    evidence_items.extend(external_research_evidence[:8])
    if shell_body_profile:
        evidence_items.append(shell_body_profile)

    node_scores: Dict[str, Dict[str, Any]] = {}
    support_edges: List[Dict[str, Any]] = []
    contradiction_edges: List[Dict[str, Any]] = []

    for item in evidence_items:
        title = str(
            item.get("title") or item.get("slot_id") or "evidence_item"
        ).strip()
        confidence = _clamp01(
            item.get("confidence_score") or item.get("source_reliability") or 0.4
        )
        for topic in list(item.get("supports") or []):
            topic_name = str(topic or "").strip()
            if not topic_name:
                continue
            bucket = node_scores.setdefault(
                topic_name,
                {"support_count": 0, "contradict_count": 0, "confidence_sum": 0.0},
            )
            bucket["support_count"] += 1
            bucket["confidence_sum"] += confidence
            support_edges.append(
                {
                    "from": title,
                    "to": topic_name,
                    "relation": "supports",
                    "weight": round(confidence, 4),
                }
            )
        for topic in list(item.get("contradicts") or []):
            topic_name = str(topic or "").strip()
            if not topic_name:
                continue
            bucket = node_scores.setdefault(
                topic_name,
                {"support_count": 0, "contradict_count": 0, "confidence_sum": 0.0},
            )
            bucket["contradict_count"] += 1
            bucket["confidence_sum"] += confidence
            contradiction_edges.append(
                {
                    "from": title,
                    "to": topic_name,
                    "relation": "contradicts",
                    "weight": round(confidence, 4),
                }
            )

    nodes: List[Dict[str, Any]] = []
    for topic_name, bucket in sorted(node_scores.items()):
        total = bucket["support_count"] + bucket["contradict_count"]
        avg_confidence = bucket["confidence_sum"] / total if total > 0 else 0.0
        nodes.append(
            {
                "topic": topic_name,
                "support_count": bucket["support_count"],
                "contradict_count": bucket["contradict_count"],
                "net_signal": bucket["support_count"] - bucket["contradict_count"],
                "avg_confidence": round(_clamp01(avg_confidence), 4),
            }
        )

    return {
        "node_count": len(nodes),
        "edge_count": len(support_edges) + len(contradiction_edges),
        "nodes": nodes[:16],
        "support_edges": support_edges[:10],
        "contradiction_edges": contradiction_edges[:8],
    }


def build_evidence_channels(
    *,
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    shell_body_profile: Dict[str, Any],
    deliberation_dict: Dict[str, Any],
) -> Dict[str, Any]:
    learning_strength = channel_strength_from_learning(recent_learning_evidence)
    learning_confidence = channel_confidence_from_learning(recent_learning_evidence)
    body_confidence = channel_confidence_from_body(shell_body_profile)
    body_strength = (
        "strong" if shell_body_profile.get("profile_status") == "ready" else "weak"
    )
    research_strength = channel_strength_from_research(external_research_evidence)
    research_confidence = channel_confidence_from_research(external_research_evidence)
    research_freshness = research_freshness_hint(external_research_evidence)
    conflict_flags = evidence_conflict_flags(
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        shell_body_profile=shell_body_profile,
    )
    evidence_graph = build_evidence_graph(
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        shell_body_profile=shell_body_profile,
    )
    learning_channel = {
        "channel": "recent_learning",
        "kind": "internal_learning_evidence",
        "item_count": len(recent_learning_evidence),
        "freshness_hint": "recent",
        "confidence": learning_confidence,
        "evidence_strength": learning_strength,
        "conflict_flags": [
            flag for flag in conflict_flags if flag.startswith("learning_")
        ],
        "items": recent_learning_evidence[:5],
    }
    body_channel = {
        "channel": "shell_body_profile",
        "kind": "self_structure_evidence",
        "item_count": 1 if shell_body_profile else 0,
        "freshness_hint": "current",
        "confidence": body_confidence,
        "evidence_strength": body_strength,
        "conflict_flags": [
            flag for flag in conflict_flags if flag.startswith("body_")
        ],
        "items": [shell_body_profile] if shell_body_profile else [],
    }
    research_channel = {
        "channel": "external_research",
        "kind": "external_research_evidence",
        "item_count": len(external_research_evidence),
        "freshness_hint": research_freshness,
        "confidence": research_confidence,
        "evidence_strength": research_strength,
        "conflict_flags": [
            flag for flag in conflict_flags if flag.startswith("research_")
        ],
        "items": external_research_evidence[:8],
    }
    world_model = dict(deliberation_dict.get("world_model") or {})
    cognition_channel = {
        "channel": "deliberation_state",
        "kind": "internal_cognition_state",
        "item_count": 1,
        "freshness_hint": "current",
        "confidence": _clamp01(
            0.45 + float(world_model.get("self_confidence") or 0.0) * 0.4
        ),
        "evidence_strength": "moderate",
        "conflict_flags": [],
        "items": [
            {
                "perception": deliberation_dict.get("perception", {}),
                "world_model": deliberation_dict.get("world_model", {}),
                "reflection": deliberation_dict.get("reflection", {}),
                "adaptive_policy": deliberation_dict.get("adaptive_policy", {}),
            }
        ],
    }
    return {
        "channels": [
            learning_channel,
            body_channel,
            research_channel,
            cognition_channel,
        ],
        "research_digest": {
            "item_count": len(external_research_evidence),
            "freshness_hint": research_freshness,
            "confidence": research_confidence,
            "evidence_strength": research_strength,
            "conflict_flags": [
                flag for flag in conflict_flags if flag.startswith("research_")
            ],
            "sources": sorted(
                {
                    str(item.get("source") or "").strip()
                    for item in external_research_evidence
                    if str(item.get("source") or "").strip()
                }
            ),
            "topics": [
                str(item.get("title") or "").strip()
                for item in external_research_evidence[:6]
                if str(item.get("title") or "").strip()
            ],
        },
        "evidence_graph": evidence_graph,
    }


def _clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))
