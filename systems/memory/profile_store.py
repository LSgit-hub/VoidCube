"""Canonical persistence and revision rules for user profile memory."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _iso_value(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def upsert_profile_memory(
    conn,
    profile,
    *,
    owner_id: str,
    workspace_id: str,
    now: str,
) -> int:
    """Insert or revise one scoped profile fact without losing evidence."""
    valid_from = _iso_value(profile.valid_from)
    tombstone = conn.execute(
        "SELECT revoked_at FROM profile_memory_tombstones "
        "WHERE owner_id = ? AND workspace_id = ? AND subject = ? AND predicate = ?",
        (owner_id, workspace_id, profile.subject, profile.predicate),
    ).fetchone()
    if tombstone and _as_utc(valid_from) < _as_utc(str(tombstone[0])):
        return 0

    existing = conn.execute(
        "SELECT memory_id, value, confidence, evidence_refs, source_turns "
        "FROM profile_memories WHERE owner_id = ? AND workspace_id = ? "
        "AND subject = ? AND predicate = ? AND status = 'active' "
        "ORDER BY valid_from DESC LIMIT 1",
        (owner_id, workspace_id, profile.subject, profile.predicate),
    ).fetchone()
    supersedes = list(getattr(profile, "supersedes", []) or [])
    if existing and str(existing[1]) != str(profile.value):
        conn.execute(
            "UPDATE profile_memories SET status = 'superseded', valid_to = ?, "
            "updated_at = ? WHERE memory_id = ?",
            (valid_from, now, existing[0]),
        )
        supersedes.append(str(existing[0]))
    elif existing:
        evidence_refs = _merge_json_list(existing[3], profile.evidence_refs)
        source_turns = _merge_json_list(existing[4], profile.source_turns)
        confidence = max(float(existing[2] or 0.0), float(profile.confidence))
        certainty = (
            "confirmed"
            if len(source_turns) > 1 or _enum_value(profile.certainty_state) == "confirmed"
            else _enum_value(profile.certainty_state)
        )
        conn.execute(
            "UPDATE profile_memories SET summary = ?, confidence = ?, "
            "certainty_state = ?, evidence_refs = ?, source_turns = ?, updated_at = ? "
            "WHERE memory_id = ?",
            (
                profile.summary,
                confidence,
                certainty,
                json.dumps(evidence_refs, ensure_ascii=False),
                json.dumps(source_turns, ensure_ascii=False),
                now,
                existing[0],
            ),
        )
        return 0

    conn.execute(
        "INSERT OR REPLACE INTO profile_memories "
        "(memory_id, memory_kind, subject, predicate, value, summary, confidence, "
        "certainty_state, status, valid_from, valid_to, evidence_refs, source_turns, "
        "supersedes, conflict_refs, created_at, updated_at, owner_id, workspace_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            profile.id,
            _enum_value(profile.memory_kind),
            profile.subject,
            profile.predicate,
            profile.value,
            profile.summary,
            profile.confidence,
            _enum_value(profile.certainty_state),
            _enum_value(profile.status),
            valid_from,
            _iso_value(profile.valid_to) if profile.valid_to else None,
            json.dumps(list(profile.evidence_refs), ensure_ascii=False),
            json.dumps(list(profile.source_turns), ensure_ascii=False),
            json.dumps(list(dict.fromkeys(supersedes)), ensure_ascii=False),
            json.dumps(list(profile.conflict_refs), ensure_ascii=False),
            _iso_value(profile.created_at),
            now,
            owner_id,
            workspace_id,
        ),
    )
    return 1


def revoke_profile_predicates(
    conn,
    predicates: Iterable[str],
    *,
    owner_id: str,
    workspace_id: str,
    turn_id: str,
    now: str,
) -> dict[str, Any]:
    """Delete scoped profile facts and block older turns from recreating them."""
    normalized = tuple(
        dict.fromkeys(str(item).strip() for item in predicates if str(item).strip())
    )
    if not normalized:
        return {"action": "none", "predicates": [], "deleted": 0}
    prior_tombstones = conn.execute(
        "SELECT predicate, source_turn_id, evidence_turns "
        "FROM profile_memory_tombstones "
        "WHERE owner_id = ? AND workspace_id = ? AND subject = 'user'",
        (owner_id, workspace_id),
    ).fetchall()
    prior_by_predicate = {str(row[0]): str(row[1]) for row in prior_tombstones}
    prior_evidence = {
        str(row[0]): _parse_json_list(row[2]) for row in prior_tombstones
    }
    if all(prior_by_predicate.get(predicate) == turn_id for predicate in normalized):
        return {
            "action": "already_revoked",
            "predicates": list(normalized),
            "deleted": 0,
        }
    placeholders = ",".join("?" for _ in normalized)
    profile_rows = conn.execute(
        "SELECT memory_id, source_turns FROM profile_memories WHERE owner_id = ? "
        "AND workspace_id = ? AND subject = 'user' "
        f"AND predicate IN ({placeholders})",
        (owner_id, workspace_id, *normalized),
    ).fetchall()
    profile_memory_ids = [str(row[0]) for row in profile_rows]
    evidence_turns = list(
        dict.fromkeys(
            turn_id
            for row in profile_rows
            for turn_id in _parse_json_list(row[1])
        )
    )
    evidence_turns = list(dict.fromkeys((*evidence_turns, turn_id)))
    if profile_memory_ids:
        id_placeholders = ",".join("?" for _ in profile_memory_ids)
        conn.execute(
            "DELETE FROM memory_embeddings WHERE source_type = 'profile' "
            f"AND memory_id IN ({id_placeholders}) AND owner_id = ? AND workspace_id = ?",
            (*profile_memory_ids, owner_id, workspace_id),
        )
    deleted = conn.execute(
        "DELETE FROM profile_memories WHERE owner_id = ? AND workspace_id = ? "
        "AND subject = 'user' "
        f"AND predicate IN ({placeholders})",
        (owner_id, workspace_id, *normalized),
    ).rowcount
    if evidence_turns:
        turn_placeholders = ",".join("?" for _ in evidence_turns)
        conn.execute(
            "UPDATE turns SET compressed_to_tier2 = 1 WHERE owner_id = ? "
            "AND workspace_id = ? "
            f"AND turn_id IN ({turn_placeholders})",
            (owner_id, workspace_id, *evidence_turns),
        )
    derived_memory_ids: list[str] = []
    for evidence_turn_id in evidence_turns:
        derived_memory_ids.extend(
            str(row[0])
            for row in conn.execute(
                "SELECT memory_id FROM compressed_memories WHERE owner_id = ? "
                "AND workspace_id = ? AND EXISTS (SELECT 1 FROM "
                "json_each(compressed_memories.source_turns) WHERE value = ?)",
                (owner_id, workspace_id, evidence_turn_id),
            ).fetchall()
        )
    derived_memory_ids = list(dict.fromkeys(derived_memory_ids))
    if derived_memory_ids:
        derived_placeholders = ",".join("?" for _ in derived_memory_ids)
        conn.execute(
            "DELETE FROM compressed_memories WHERE owner_id = ? AND workspace_id = ? "
            f"AND memory_id IN ({derived_placeholders})",
            (owner_id, workspace_id, *derived_memory_ids),
        )
        conn.execute(
            "DELETE FROM memory_embeddings WHERE source_type = 'compressed' "
            f"AND memory_id IN ({derived_placeholders}) AND owner_id = ? "
            "AND workspace_id = ?",
            (*derived_memory_ids, owner_id, workspace_id),
        )
    trace_references = 0
    revoked_memory_ids = list(
        dict.fromkeys((*profile_memory_ids, *derived_memory_ids, *evidence_turns))
    )
    if revoked_memory_ids:
        trace_rows = conn.execute(
            "SELECT trace_id, selected_results FROM recall_traces WHERE "
            "owner_id = ? AND workspace_id = ?",
            (owner_id, workspace_id),
        ).fetchall()
        revoked_ids = set(revoked_memory_ids)
        for trace_id, selected_json in trace_rows:
            selected = _parse_json_objects(selected_json)
            filtered = [
                item for item in selected if str(item.get("id") or "") not in revoked_ids
            ]
            if len(filtered) == len(selected):
                continue
            conn.execute(
                "UPDATE recall_traces SET selected_results = ?, result_count = ? "
                "WHERE trace_id = ? AND owner_id = ? AND workspace_id = ?",
                (
                    json.dumps(filtered, ensure_ascii=False),
                    len(filtered),
                    trace_id,
                    owner_id,
                    workspace_id,
                ),
            )
            trace_references += 1
        id_placeholders = ",".join("?" for _ in revoked_memory_ids)
        conn.execute(
            "DELETE FROM recall_feedback WHERE owner_id = ? AND workspace_id = ? "
            f"AND memory_id IN ({id_placeholders})",
            (owner_id, workspace_id, *revoked_memory_ids),
        )
    reason_hash = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    for predicate in normalized:
        predicate_evidence = list(
            dict.fromkeys((*prior_evidence.get(predicate, []), *evidence_turns))
        )
        conn.execute(
            "INSERT INTO profile_memory_tombstones "
            "(owner_id, workspace_id, subject, predicate, revoked_at, "
            "source_turn_id, evidence_turns, reason_hash) "
            "VALUES (?, ?, 'user', ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, workspace_id, subject, predicate) DO UPDATE SET "
            "revoked_at = excluded.revoked_at, source_turn_id = excluded.source_turn_id, "
            "evidence_turns = excluded.evidence_turns, "
            "reason_hash = excluded.reason_hash",
            (
                owner_id,
                workspace_id,
                predicate,
                now,
                turn_id,
                json.dumps(predicate_evidence, ensure_ascii=False),
                reason_hash,
            ),
        )
    audit_id = f"profile-revoke-{uuid.uuid4()}"
    conn.execute(
        "INSERT INTO memory_deletion_audit "
        "(audit_id, target_kind, target_hash, reason, deleted_counts, owner_id, "
        "workspace_id, created_at) VALUES (?, 'profile_predicate', ?, ?, ?, ?, ?, ?)",
        (
            audit_id,
            reason_hash,
            "user_explicit_profile_revocation",
            json.dumps(
                {
                    "profile_memories": max(0, int(deleted or 0)),
                    "compressed_memories": len(derived_memory_ids),
                    "recall_trace_references": trace_references,
                }
            ),
            owner_id,
            workspace_id,
            now,
        ),
    )
    return {
        "action": "revoked",
        "predicates": list(normalized),
        "deleted": max(0, int(deleted or 0)),
        "audit_id": audit_id,
    }


def _merge_json_list(serialized: Any, incoming: Iterable[Any]) -> list[str]:
    try:
        current = json.loads(serialized or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        current = []
    return list(
        dict.fromkeys(
            str(item)
            for item in [*(current if isinstance(current, list) else []), *incoming]
            if str(item)
        )
    )


def _parse_json_list(serialized: Any) -> list[str]:
    try:
        parsed = json.loads(serialized or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _parse_json_objects(serialized: Any) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(serialized or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
