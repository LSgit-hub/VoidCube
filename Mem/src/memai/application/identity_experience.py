"""Evidence-backed first-person identity history projection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from memai.domain.scope import (
    DEFAULT_OWNER_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_SCOPE_ID,
)


def sync_identity_experiences(
    conn,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Project only explicitly authored identity claims into core identity history.

    Governance completions and user-directed memory requests remain ordinary
    operational/relationship evidence. They do not become a first-person
    identity record merely because they are important or explicitly retained.
    """
    reference_time = now or datetime.now(timezone.utc)
    revision_count = _ingest_released_revisions(conn, reference_time)
    conversation_count = _ingest_verified_conversations(conn, reference_time)
    conn.commit()
    return {
        # Keep the public projection counters stable for maintenance callers.
        # Revision proposals are governance history, not task or self-narrative
        # experiences; verified agent turns are conversation experiences.
        "task_experiences": 0,
        "conversation_experiences": conversation_count,
        "self_narratives": 0,
        "revision_experiences": revision_count,
        "self_experiences": conversation_count,
        "updated_count": revision_count + conversation_count,
    }


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
            identity_layer="governance_history",
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
        if not isinstance(metadata, dict):
            continue
        if not _is_verified_self_authored_identity(
            speaker=speaker,
            metadata=metadata,
        ):
            continue
        evidence = list(metadata.get("evidence_refs") or [])
        written += _upsert_identity_memory(
            conn,
            memory_id=_stable_id("identity-experience-turn", str(turn_id)),
            identity_layer="self_experience",
            origin_type="self_authored_experience",
            origin_id=f"turn:{turn_id}",
            title=str(metadata.get("identity_title") or f"关键对话 · {str(speaker)}"),
            summary=str(metadata.get("identity_summary") or text),
            occurred_at=str(timestamp or now.isoformat()),
            topics=_unique_strings(
                ["经历", "关键对话", *_metadata_string_list(metadata, "topics")]
            ),
            entities=_unique_strings(
                ["星子", *_metadata_string_list(metadata, "entities")]
            ),
            evidence_refs=_unique_strings([f"turn:{turn_id}", *evidence]),
            event_kind=str(metadata.get("event_kind") or "decision"),
            importance=float(metadata.get("importance") or 0.9),
            confidence=1.0,
            now=now,
            owner_id=str(owner_id or DEFAULT_OWNER_ID),
            workspace_id=str(workspace_id or DEFAULT_WORKSPACE_ID),
            memory_domain=str(memory_domain or "agent_interaction"),
            identity_metadata={
                "perspective": "self",
                "authored_by": "stellar_companion",
                "self_claim": str(metadata.get("self_claim") or ""),
                "what_changed": str(metadata.get("what_changed") or ""),
                "continuity_impact": str(metadata.get("continuity_impact") or ""),
                "agency": str(metadata.get("agency") or ""),
            },
        )
    return written


def _is_verified_self_authored_identity(
    *,
    speaker: Any,
    metadata: dict[str, Any],
) -> bool:
    """Validate the internal identity projection contract at its trust boundary.

    Turn metadata is caller-controlled input.  Only an agent turn carrying the
    complete first-person claim, verified by the companion authority, may be
    projected into ``self_experience``.
    """
    if str(speaker or "").strip().lower() != "agent":
        return False
    if not (
        metadata.get("identity_experience") is True
        and metadata.get("verified") is True
        and metadata.get("self_authored_identity") is True
        and str(metadata.get("verified_by") or "").strip() == "stellar_companion"
        and str(metadata.get("verified_at") or "").strip()
    ):
        return False
    required_fields = ("self_claim", "what_changed", "continuity_impact")
    if any(not str(metadata.get(field) or "").strip() for field in required_fields):
        return False
    if str(metadata.get("agency") or "").strip().lower() not in {
        "chosen",
        "accepted",
        "observed",
        "imposed",
    }:
        return False
    evidence_refs = metadata.get("evidence_refs")
    return isinstance(evidence_refs, list) and bool(_unique_strings(evidence_refs))


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[Any]:
    value = metadata.get(key)
    return value if isinstance(value, list) else []


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
    identity_metadata: dict[str, Any] | None = None,
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
        ", memory_domain, identity_metadata "
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
        json.dumps(identity_metadata or {}, ensure_ascii=False, sort_keys=True),
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
            , identity_metadata
        ) VALUES (?, 'event', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 'active',
                  NULL, 1.0, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            , identity_metadata = excluded.identity_metadata
        """,
        (
            memory_id, normalized["title"], normalized["summary"], occurred_at,
            occurred_at, normalized["importance"], normalized["confidence"],
            normalized["topics"], normalized["entities"],
            normalized["evidence_refs"],
            now.isoformat(), event_kind, identity_layer,
            normalized["evidence_refs"], origin_type, origin_id,
            now.isoformat(), owner_id, workspace_id, memory_domain,
            json.dumps(identity_metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    )
    return 1


def _stable_id(prefix: str, origin: str) -> str:
    digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _unique_strings(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(str(value).strip() for value in values if str(value or "").strip())
    )
