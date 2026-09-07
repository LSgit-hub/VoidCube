"""Cross-batch topic consolidation for Tier 2 memories.

The Tier 1 bridge builds a hierarchy inside one batch. This module is the
separate, slower pass that finds recurring topics across batches. Auto mode
writes an additive derived memory; source memories remain available as
evidence and are never deleted by consolidation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any, Sequence


_MEMORY_TYPES = ("event", "scene", "arc", "epoch")
_TARGET_TYPE = {"event": "scene", "scene": "arc", "arc": "epoch", "epoch": "epoch"}
_CONFLICT_PAIRS = (
    ("approved", "rejected"),
    ("success", "failed"),
    ("成功", "失败"),
    ("启用", "禁用"),
    ("保留", "删除"),
)


@dataclass(frozen=True, slots=True)
class ConsolidationProposal:
    proposal_id: str
    owner_id: str
    workspace_id: str
    memory_domain: str
    source_memory_ids: tuple[str, ...]
    target_type: str
    title: str
    summary: str
    evidence_count: int
    conflict_count: int
    status: str = "shadow"
    applied_now: bool = False
    recorded_now: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "owner_id": self.owner_id,
            "workspace_id": self.workspace_id,
            "memory_domain": self.memory_domain,
            "source_memory_ids": list(self.source_memory_ids),
            "target_type": self.target_type,
            "title": self.title,
            "summary": self.summary,
            "evidence_count": self.evidence_count,
            "conflict_count": self.conflict_count,
            "status": self.status,
            "applied_now": self.applied_now,
            "recorded_now": self.recorded_now,
        }


def _tokens(value: object) -> set[str]:
    text = str(value or "").lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9._-]{1,}|[\u4e00-\u9fff]", text))
    return {word for word in words if len(word) > 1 or "\u4e00" <= word <= "\u9fff"}


def _json_list(value: object) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _related(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        _overlap(first["topic_tokens"], second["topic_tokens"]) >= 0.5
        or _overlap(first["text_tokens"], second["text_tokens"]) >= 0.6
    )


def _cohesive(records: Sequence[dict[str, Any]]) -> bool:
    return all(
        _related(records[left], records[right])
        for left in range(len(records))
        for right in range(left + 1, len(records))
    )


def _cluster(records: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, first in enumerate(records):
        for right in range(left + 1, len(records)):
            second = records[right]
            # Do not join unrelated abstraction levels in one proposal.  The
            # next target is derived from the common level, avoiding duplicate
            # Event->Scene upgrades from the retired lifecycle path.
            if first["memory_type"] != second["memory_type"]:
                continue
            # A maintenance run may scan all tenants. Never create a proposal
            # whose evidence crosses an owner, workspace, or domain boundary.
            if any(
                first[key] != second[key]
                for key in ("owner_id", "workspace_id", "memory_domain")
            ):
                continue
            if _related(first, second):
                union(left, right)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)
    return list(groups.values())


def _conflict_count(records: Sequence[dict[str, Any]]) -> int:
    """Count opposing-status signals for automatic conflict rejection."""
    text = " ".join(str(record.get("text", "")).lower() for record in records)
    return sum(1 for left, right in _CONFLICT_PAIRS if left in text and right in text)


def propose_consolidations(
    conn,
    *,
    owner_id: str | None = None,
    workspace_id: str | None = None,
    memory_domain: str | None = None,
    limit: int = 20,
    min_cluster_size: int = 3,
    min_confidence: float = 0.7,
    mode: str = "auto",
) -> list[ConsolidationProposal]:
    """Find recurring same-level topics and optionally materialize them."""
    if mode not in {"auto", "shadow", "disabled"}:
        raise ValueError("consolidation mode must be 'auto', 'shadow', or 'disabled'")
    if mode == "disabled":
        return []
    clauses = [
        "status = 'active'",
        "hidden = 0",
        "COALESCE(identity_layer, '') != 'founding'",
        f"memory_type IN ({','.join('?' for _ in _MEMORY_TYPES)})",
    ]
    params: list[Any] = [*_MEMORY_TYPES]
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(str(owner_id))
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(str(workspace_id))
    if memory_domain is not None:
        clauses.append("memory_domain = ?")
        params.append(str(memory_domain))
    rows = conn.execute(
        "SELECT memory_id, memory_type, title, summary, topics, entities, "
        "source_turns, evidence_refs, event_kind, timespan_start, timespan_end, "
        "importance, confidence, owner_id, workspace_id, memory_domain "
        "FROM compressed_memories WHERE " + " AND ".join(clauses)
        + " ORDER BY compressed_at DESC, memory_id LIMIT ?",
        [*params, max(1, min(int(limit) * 20, 2000))],
    ).fetchall()
    records = []
    for row in rows:
        text_tokens = _tokens(f"{row[2]} {row[3]} {row[8] or ''}")
        topic_tokens = _tokens(" ".join(_json_list(row[4]) + _json_list(row[5])))
        records.append(
            {
                "memory_id": str(row[0]),
                "memory_type": str(row[1]),
                "title": str(row[2] or "").strip(),
                "summary": str(row[3] or "").strip(),
                "text": f"{row[2] or ''} {row[3] or ''} {row[8] or ''}",
                "topic_tokens": topic_tokens,
                "text_tokens": text_tokens,
                "source_turns": _json_list(row[6]),
                "evidence_refs": _json_list(row[7]),
                "entities": _json_list(row[5]),
                "timespan_start": row[9],
                "timespan_end": row[10],
                "importance": float(row[11] or 0.0),
                "confidence": float(row[12] or 0.0),
                "owner_id": str(row[13]),
                "workspace_id": str(row[14]),
                "memory_domain": str(row[15]),
            }
        )
    proposals: list[ConsolidationProposal] = []
    for group in _cluster(records):
        if len(group) < max(2, int(min_cluster_size)):
            continue
        if not _cohesive(group):
            continue
        if min(item["confidence"] for item in group) < float(min_confidence):
            continue
        source_ids = tuple(sorted(item["memory_id"] for item in group))
        seed = "\0".join(
            [group[0]["owner_id"], group[0]["workspace_id"], group[0]["memory_domain"], *source_ids]
        )
        proposal_id = "mcp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        memory_type = group[0]["memory_type"]
        target_type = _TARGET_TYPE[memory_type]
        title = f"Consolidated {target_type}: {group[0]['title'] or memory_type}"
        summaries = [item["summary"] for item in group if item["summary"]]
        summary = " ".join(dict.fromkeys(summaries))[:4000]
        source_turns = {turn for item in group for turn in item["source_turns"]}
        evidence_refs = {ref for item in group for ref in item["evidence_refs"]}
        conflict_count = _conflict_count(group)
        status = "shadow" if mode == "shadow" else ("rejected" if conflict_count else "applied")
        proposals.append(
            ConsolidationProposal(
                proposal_id=proposal_id,
                owner_id=group[0]["owner_id"],
                workspace_id=group[0]["workspace_id"],
                memory_domain=group[0]["memory_domain"],
                source_memory_ids=source_ids,
                target_type=target_type,
                title=title,
                summary=summary,
                evidence_count=len(source_turns | evidence_refs),
                conflict_count=conflict_count,
                status=status,
            )
        )
        if len(proposals) >= max(1, int(limit)):
            break

    groups_by_source = {
        tuple(sorted(item["memory_id"] for item in group)): group
        for group in _cluster(records)
    }
    applied_scopes: set[tuple[str, str, str]] = set()
    resolved_proposals: list[ConsolidationProposal] = []
    for proposal in proposals:
        existing = conn.execute(
            "SELECT source_memory_ids, target_type, title, summary, evidence_count, "
            "conflict_count, status FROM memory_consolidation_proposals "
            "WHERE proposal_id = ?",
            (proposal.proposal_id,),
        ).fetchone()
        memory_id = "mcl_" + hashlib.sha256(
            proposal.proposal_id.encode("utf-8")
        ).hexdigest()[:24]
        derived_active = conn.execute(
            "SELECT 1 FROM compressed_memories WHERE memory_id = ? AND owner_id = ? "
            "AND workspace_id = ? AND memory_domain = ? AND status = 'active'",
            (
                memory_id,
                proposal.owner_id,
                proposal.workspace_id,
                proposal.memory_domain,
            ),
        ).fetchone()
        current_values = (
            json.dumps(proposal.source_memory_ids, ensure_ascii=False),
            proposal.target_type,
            proposal.title,
            proposal.summary,
            proposal.evidence_count,
            proposal.conflict_count,
            proposal.status,
        )
        existing_values = tuple(existing) if existing is not None else None
        record_changed = existing_values != current_values
        already_materialized = (
            mode == "auto"
            and proposal.status == "applied"
            and not record_changed
            and derived_active is not None
        )
        if mode == "auto" and proposal.status == "applied":
            if already_materialized:
                resolved_proposals.append(proposal)
                continue
            group = groups_by_source[proposal.source_memory_ids]
            target_level = {"scene": 1, "arc": 2, "epoch": 3}[proposal.target_type]
            source_turns = sorted({turn for item in group for turn in item["source_turns"]})
            evidence_refs = sorted(
                {ref for item in group for ref in item["evidence_refs"]}
                | {f"memory:{source_id}" for source_id in proposal.source_memory_ids}
            )
            topics = sorted({token for item in group for token in item["topic_tokens"]})
            entities = sorted({entity for item in group for entity in item["entities"]})
            starts = [item["timespan_start"] for item in group if item["timespan_start"]]
            ends = [item["timespan_end"] for item in group if item["timespan_end"]]
            conn.execute(
                "INSERT INTO compressed_memories "
                "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                "importance, confidence, topics, entities, source_turns, evidence_refs, "
                "origin_type, origin_id, derived_from_id, compressed_at, compression_level, "
                "status, weight, owner_id, workspace_id, memory_domain, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'longitudinal_consolidation', ?, ?, "
                "datetime('now'), ?, 'active', ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(memory_id) DO UPDATE SET "
                "title=excluded.title, summary=excluded.summary, timespan_start=excluded.timespan_start, "
                "timespan_end=excluded.timespan_end, importance=excluded.importance, confidence=excluded.confidence, "
                "topics=excluded.topics, entities=excluded.entities, source_turns=excluded.source_turns, "
                "evidence_refs=excluded.evidence_refs, compressed_at=excluded.compressed_at, "
                "compression_level=excluded.compression_level "
                "WHERE compressed_memories.status = 'active'",
                (
                    memory_id, proposal.target_type, proposal.title, proposal.summary,
                    min(starts) if starts else None, max(ends) if ends else None,
                    max(item["importance"] for item in group),
                    sum(item["confidence"] for item in group) / len(group),
                    json.dumps(topics, ensure_ascii=False), json.dumps(entities, ensure_ascii=False),
                    json.dumps(source_turns, ensure_ascii=False), json.dumps(evidence_refs, ensure_ascii=False),
                    proposal.proposal_id, proposal.source_memory_ids[0], target_level,
                    {1: 0.7, 2: 0.4, 3: 0.2}[target_level], proposal.owner_id,
                    proposal.workspace_id, proposal.memory_domain,
                ),
            )
            applied_scopes.add((proposal.owner_id, proposal.workspace_id, proposal.memory_domain))
            proposal = replace(proposal, applied_now=True)
        conn.execute(
            "INSERT INTO memory_consolidation_proposals "
            "(proposal_id, owner_id, workspace_id, memory_domain, source_memory_ids, "
            "target_type, title, summary, evidence_count, conflict_count, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(proposal_id) DO UPDATE SET "
            "source_memory_ids=excluded.source_memory_ids, target_type=excluded.target_type, "
            "title=excluded.title, summary=excluded.summary, evidence_count=excluded.evidence_count, "
            "conflict_count=excluded.conflict_count, status=excluded.status "
            "WHERE memory_consolidation_proposals.source_memory_ids IS NOT excluded.source_memory_ids "
            "OR memory_consolidation_proposals.target_type IS NOT excluded.target_type "
            "OR memory_consolidation_proposals.title IS NOT excluded.title "
            "OR memory_consolidation_proposals.summary IS NOT excluded.summary "
            "OR memory_consolidation_proposals.evidence_count IS NOT excluded.evidence_count "
            "OR memory_consolidation_proposals.conflict_count IS NOT excluded.conflict_count "
            "OR memory_consolidation_proposals.status IS NOT excluded.status",
            (
                proposal.proposal_id,
                proposal.owner_id,
                proposal.workspace_id,
                proposal.memory_domain,
                json.dumps(proposal.source_memory_ids, ensure_ascii=False),
                proposal.target_type,
                proposal.title,
                proposal.summary,
                proposal.evidence_count,
                proposal.conflict_count,
                proposal.status,
            ),
        )
        proposal = replace(proposal, recorded_now=record_changed)
        resolved_proposals.append(proposal)
    if applied_scopes:
        from memai.indexes.entity_graph import rebuild_entity_graph

        for scope_owner, scope_workspace, scope_domain in sorted(applied_scopes):
            rebuild_entity_graph(
                conn,
                owner_id=scope_owner,
                workspace_id=scope_workspace,
                memory_domain=scope_domain,
            )
    return resolved_proposals


def run_consolidation(
    conn,
    *,
    owner_id: str | None = None,
    workspace_id: str | None = None,
    memory_domain: str | None = None,
    limit: int = 20,
    min_cluster_size: int = 3,
    min_confidence: float = 0.7,
    mode: str = "auto",
) -> dict[str, Any]:
    proposals = propose_consolidations(
        conn,
        owner_id=owner_id,
        workspace_id=workspace_id,
        memory_domain=memory_domain,
        limit=limit,
        min_cluster_size=min_cluster_size,
        min_confidence=min_confidence,
        mode=mode,
    )
    return {
        "mode": mode,
        "proposals_generated": len(proposals),
        "applied_count": sum(proposal.applied_now for proposal in proposals),
        "conflict_count": sum(proposal.status == "rejected" for proposal in proposals),
        "changed_count": sum(
            proposal.applied_now or proposal.recorded_now for proposal in proposals
        ),
        "proposals": [proposal.to_dict() for proposal in proposals],
    }
