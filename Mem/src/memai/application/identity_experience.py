"""Evidence-backed identity experience and self-narrative projection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from memai.domain.scope import (
    DEFAULT_OWNER_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_SCOPE_ID,
)


_IMPORTANT_TASK_FAMILIES = {
    "body_switch",
    "body_upgrade",
    "general_self_evolution",
    "memory_maintenance",
}

_IDENTITY_CORRECTION_SIGNALS = (
    "纠正一个身份历史",
    "纠正身份历史",
    "身份历史修订",
    "身份语义更新",
    "旧语义",
)
_EXPLICIT_MEMORY_SIGNALS = (
    "请记住",
    "要记住",
    "不要忘",
    "永远记录",
    "永久记录",
    "永久保存",
    "长期记忆",
    "长期记录",
    "这是项目的目的",
    "这是我做这个项目的目的",
)
_MILESTONE_SIGNALS = (
    "项目里程碑",
    "作为里程碑",
    "正式完成",
    "阶段完成",
)


def classify_explicit_conversation_experience(text: str) -> dict[str, Any] | None:
    """Classify only user-explicit, evidence-worthy experience signals."""
    normalized = "".join(str(text or "").split()).casefold()
    if not normalized:
        return None
    if any(signal in normalized for signal in _IDENTITY_CORRECTION_SIGNALS):
        return {
            "kind": "identity_correction",
            "title_prefix": "身份历史修订",
            "event_kind": "correction",
            "importance": 1.0,
            "topics": ["身份历史", "语义修订"],
        }
    if any(signal in normalized for signal in _MILESTONE_SIGNALS):
        return {
            "kind": "milestone",
            "title_prefix": "项目里程碑",
            "event_kind": "completion",
            "importance": 1.0,
            "topics": ["项目", "里程碑"],
        }
    if any(signal in normalized for signal in _EXPLICIT_MEMORY_SIGNALS):
        return {
            "kind": "explicit_memory",
            "title_prefix": "关键对话",
            "event_kind": "decision",
            "importance": 0.95,
            "topics": ["关键对话", "长期记忆"],
        }
    return None


def sync_identity_experiences(
    conn,
    *,
    governance_events: Iterable[Any] = (),
    now: datetime | None = None,
) -> dict[str, int]:
    """Project verified sources into identity experiences and a weekly narrative."""
    reference_time = now or datetime.now(timezone.utc)
    task_count = _ingest_completed_tasks(conn, governance_events, reference_time)
    revision_count = _ingest_released_revisions(conn, reference_time)
    conversation_count = _ingest_verified_conversations(conn, reference_time)
    narrative_count = _synthesize_weekly_narrative(conn, reference_time)
    conn.commit()
    return {
        "task_experiences": task_count,
        "revision_experiences": revision_count,
        "conversation_experiences": conversation_count,
        "self_narratives": narrative_count,
        "updated_count": (
            task_count + revision_count + conversation_count + narrative_count
        ),
    }


def _ingest_completed_tasks(conn, events: Iterable[Any], now: datetime) -> int:
    latest_by_task: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = _event_payload(event)
        if _enum_value(payload.get("decision")) != "completed":
            continue
        task_id = str(payload.get("task_id") or "").strip()
        projection = dict(
            dict(payload.get("execution_result") or {}).get(
                "autonomous_task_projection"
            )
            or {}
        )
        if not task_id or not projection or not _is_important_task(projection):
            continue
        latest_by_task[task_id] = payload

    written = 0
    for task_id, payload in latest_by_task.items():
        projection = dict(payload["execution_result"]["autonomous_task_projection"])
        metadata = dict(projection.get("metadata") or {})
        evidence = dict(projection.get("evidence") or {})
        lineage = dict(payload.get("git_lineage") or {})
        event_id = str(payload.get("id") or "").strip()
        occurred_at = str(
            projection.get("updated_at")
            or payload.get("created_at")
            or now.isoformat()
        )
        evidence_refs = [
            f"governance:{event_id}" if event_id else "",
            f"task:{task_id}",
            *[f"file:{path}" for path in lineage.get("changed_files") or []],
        ]
        summary = str(projection.get("summary") or "").strip()
        result = metadata.get("execution_result")
        result_status = str(dict(result or {}).get("status") or "").strip()
        if result_status:
            summary = f"{summary} 执行结果：{result_status}。".strip()
        written += _upsert_identity_memory(
            conn,
            memory_id=_stable_id("identity-experience-task", task_id),
            identity_layer="experience",
            origin_type="governance_task",
            origin_id=f"task:{task_id}",
            title=str(projection.get("title") or f"任务 {task_id[:8]}"),
            summary=summary or str(payload.get("reason") or "任务已经验证完成。"),
            occurred_at=occurred_at,
            topics=_unique_strings(
                [
                    "经历",
                    "任务完成",
                    projection.get("governance_task_type"),
                    projection.get("task_family"),
                    projection.get("execution_kind"),
                ]
            ),
            entities=_unique_strings(["星子", payload.get("body_id")]),
            evidence_refs=_unique_strings(evidence_refs),
            event_kind="completion",
            importance=_task_importance(projection),
            confidence=1.0,
            now=now,
            owner_id=GLOBAL_SCOPE_ID,
            workspace_id=GLOBAL_SCOPE_ID,
        )
    return written


def _ingest_released_revisions(conn, now: datetime) -> int:
    rows = conn.execute(
        "SELECT proposal_id, target_memory_id, reason, proposed_changes, "
        "evidence, release_version, released_at FROM identity_revision_proposals "
        "WHERE status = 'released' ORDER BY released_at"
    ).fetchall()
    written = 0
    for row in rows:
        proposal_id = str(row[0])
        changes = json.loads(row[3] or "{}")
        evidence = json.loads(row[4] or "[]")
        release_version = str(row[5] or "")
        written += _upsert_identity_memory(
            conn,
            memory_id=_stable_id("identity-experience-revision", proposal_id),
            identity_layer="experience",
            origin_type="identity_revision",
            origin_id=f"identity-revision:{proposal_id}",
            title=f"身份历史修订：{row[1]}",
            summary=(
                f"{row[2]} 发布内容："
                + json.dumps(changes, ensure_ascii=False, sort_keys=True)
            ),
            occurred_at=str(row[6] or now.isoformat()),
            topics=["经历", "身份修订", "身份历史"],
            entities=["星子", "Mem"],
            evidence_refs=_unique_strings(
                [f"identity-revision:{proposal_id}", f"release:{release_version}", *evidence]
            ),
            event_kind="correction",
            importance=1.0,
            confidence=1.0,
            now=now,
            owner_id=GLOBAL_SCOPE_ID,
            workspace_id=GLOBAL_SCOPE_ID,
        )
    return written


def _ingest_verified_conversations(conn, now: datetime) -> int:
    rows = conn.execute(
        "SELECT turn_id, speaker, text, timestamp, metadata, owner_id, workspace_id, memory_domain FROM turns "
        "WHERE metadata LIKE '%identity_experience%'"
    ).fetchall()
    written = 0
    for (
        turn_id,
        speaker,
        text,
        timestamp,
        metadata_raw,
        owner_id,
        workspace_id,
        memory_domain,
    ) in rows:
        try:
            metadata = json.loads(metadata_raw or "{}")
        except (TypeError, ValueError):
            continue
        if not (
            metadata.get("identity_experience") is True
            and metadata.get("verified") is True
        ):
            continue
        evidence = list(metadata.get("evidence_refs") or [])
        written += _upsert_identity_memory(
            conn,
            memory_id=_stable_id("identity-experience-turn", str(turn_id)),
            identity_layer="experience",
            origin_type="verified_conversation",
            origin_id=f"turn:{turn_id}",
            title=str(metadata.get("identity_title") or f"关键对话 · {str(speaker)}"),
            summary=str(metadata.get("identity_summary") or text),
            occurred_at=str(timestamp or now.isoformat()),
            topics=_unique_strings(["经历", "关键对话", *metadata.get("topics", [])]),
            entities=_unique_strings(["星子", *metadata.get("entities", [])]),
            evidence_refs=_unique_strings([f"turn:{turn_id}", *evidence]),
            event_kind=str(metadata.get("event_kind") or "decision"),
            importance=float(metadata.get("importance") or 0.9),
            confidence=1.0,
            now=now,
            owner_id=str(owner_id or DEFAULT_OWNER_ID),
            workspace_id=str(workspace_id or DEFAULT_WORKSPACE_ID),
            memory_domain=str(memory_domain or "agent_interaction"),
        )
    return written


def _synthesize_weekly_narrative(conn, now: datetime) -> int:
    start = now - timedelta(days=7)
    rows = conn.execute(
        "SELECT memory_id, title, summary, timespan_end, owner_id, workspace_id, memory_domain "
        "FROM compressed_memories "
        "WHERE identity_layer = 'experience' AND status = 'active' "
        "AND timespan_end >= ? ORDER BY timespan_end ASC, memory_id ASC",
        (start.isoformat(),),
    ).fetchall()
    if not rows:
        return 0
    year, week, _ = now.isocalendar()
    bucket = f"{year}-W{week:02d}"
    grouped: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        grouped.setdefault((str(row[4]), str(row[5]), str(row[6])), []).append(row)
    written = 0
    for (owner_id, workspace_id, memory_domain), scoped_rows in grouped.items():
        titles = [str(row[1]).strip() for row in scoped_rows if str(row[1]).strip()]
        evidence_refs = [str(row[0]) for row in scoped_rows]
        highlights = "；".join(titles[:6])
        if len(titles) > 6:
            highlights += f"；以及另外 {len(titles) - 6} 项经历"
        summary = (
            f"在 {bucket} 的认知周期里，我沉淀了 {len(scoped_rows)} 项经过验证的经历："
            f"{highlights}。这份自述只概括可追溯证据，不替代原始经历。"
        )
        scope_digest = hashlib.sha1(
            f"{owner_id}\0{workspace_id}\0{memory_domain}".encode("utf-8")
        ).hexdigest()[:12]
        written += _upsert_identity_memory(
            conn,
            memory_id=f"identity-self-narrative-{bucket}-{scope_digest}",
            identity_layer="self_narrative",
            origin_type="experience_synthesis",
            origin_id=f"self-narrative:{bucket}:{scope_digest}",
            title=f"星子自述 · {bucket}",
            summary=summary,
            occurred_at=str(scoped_rows[-1][3] or now.isoformat()),
            topics=["身份", "自我叙事", "经历回顾"],
            entities=["星子", "Mem"],
            evidence_refs=evidence_refs,
            event_kind="progress",
            importance=0.9,
            confidence=1.0,
            now=now,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
    return written


def _upsert_identity_memory(
    conn,
    *,
    memory_id: str,
    identity_layer: str,
    origin_type: str,
    origin_id: str,
    title: str,
    summary: str,
    occurred_at: str,
    topics: list[str],
    entities: list[str],
    evidence_refs: list[str],
    event_kind: str,
    importance: float,
    confidence: float,
    now: datetime,
    owner_id: str = GLOBAL_SCOPE_ID,
    workspace_id: str = GLOBAL_SCOPE_ID,
    memory_domain: str = "agent_interaction",
) -> int:
    normalized = {
        "title": title.strip()[:300],
        "summary": summary.strip()[:4000],
        "occurred_at": occurred_at,
        "importance": max(0.0, min(1.0, importance)),
        "confidence": max(0.0, min(1.0, confidence)),
        "topics": json.dumps(topics, ensure_ascii=False),
        "entities": json.dumps(entities, ensure_ascii=False),
        "evidence_refs": json.dumps(evidence_refs, ensure_ascii=False),
    }
    existing = conn.execute(
        "SELECT title, summary, timespan_start, timespan_end, importance, confidence, "
        "topics, entities, source_turns, event_kind, identity_layer, evidence_refs, "
        "origin_type, origin_id, owner_id, workspace_id "
        ", memory_domain "
        "FROM compressed_memories WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    expected = (
        normalized["title"], normalized["summary"], occurred_at, occurred_at,
        normalized["importance"], normalized["confidence"],
        normalized["topics"], normalized["entities"],
        normalized["evidence_refs"], event_kind, identity_layer,
        normalized["evidence_refs"], origin_type, origin_id, owner_id, workspace_id,
        memory_domain,
    )
    if existing is not None and tuple(existing) == expected:
        return 0
    conn.execute(
        """
        INSERT INTO compressed_memories (
            memory_id, memory_type, title, summary, timespan_start, timespan_end,
            importance, confidence, topics, entities, source_turns, timeline_parent_id,
            compressed_at, compression_level, status, superseded_by, weight,
            event_kind, pinned, hidden, identity_layer, evidence_refs,
            origin_type, origin_id, verified_at, owner_id, workspace_id, memory_domain
        ) VALUES (?, 'event', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 'active',
                  NULL, 1.0, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(memory_id) DO UPDATE SET
            title = excluded.title, summary = excluded.summary,
            timespan_start = excluded.timespan_start, timespan_end = excluded.timespan_end,
            importance = excluded.importance, confidence = excluded.confidence,
            topics = excluded.topics, entities = excluded.entities,
            source_turns = excluded.source_turns, event_kind = excluded.event_kind,
            status = 'active', superseded_by = NULL, hidden = 0,
            identity_layer = excluded.identity_layer, evidence_refs = excluded.evidence_refs,
            origin_type = excluded.origin_type, origin_id = excluded.origin_id,
            verified_at = excluded.verified_at,
            owner_id = excluded.owner_id, workspace_id = excluded.workspace_id,
            memory_domain = excluded.memory_domain
        """,
        (
            memory_id, normalized["title"], normalized["summary"], occurred_at,
            occurred_at, normalized["importance"], normalized["confidence"],
            normalized["topics"], normalized["entities"],
            normalized["evidence_refs"],
            now.isoformat(), event_kind, identity_layer,
            normalized["evidence_refs"], origin_type, origin_id,
            now.isoformat(), owner_id, workspace_id, memory_domain,
        ),
    )
    return 1


def _is_important_task(task: dict[str, Any]) -> bool:
    metadata = dict(task.get("metadata") or {})
    if metadata.get("identity_experience") is True or metadata.get("milestone") is True:
        return True
    family = str(task.get("task_family") or metadata.get("task_family") or "")
    if family in _IMPORTANT_TASK_FAMILIES:
        return True
    if str(task.get("priority") or "").lower() in {"high", "critical"}:
        return True
    try:
        return float(metadata.get("quality_score") or 0.0) >= 0.7
    except (TypeError, ValueError):
        return False


def _task_importance(task: dict[str, Any]) -> float:
    metadata = dict(task.get("metadata") or {})
    if metadata.get("milestone") is True:
        return 1.0
    try:
        quality = float(metadata.get("quality_score") or 0.0)
    except (TypeError, ValueError):
        quality = 0.0
    return max(0.8, min(1.0, quality))


def _event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return dict(event)
    if hasattr(event, "to_dict"):
        return dict(event.to_dict())
    return {}


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


def _stable_id(prefix: str, origin: str) -> str:
    digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _unique_strings(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(str(value).strip() for value in values if str(value or "").strip())
    )
