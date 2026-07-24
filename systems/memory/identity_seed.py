"""Seed the immutable founding identity into the canonical Mem store.

The seed is deliberately represented as pinned Tier 2 events so the existing
recall path can recover identity without a second retrieval protocol. The
operation is idempotent, repairs canonical fields on every startup, and leaves
ordinary conversation memories under the normal lifecycle.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any

from systems.memory.scope import GLOBAL_SCOPE_ID


_SOURCE_PREFIX = "founding-story:"


def load_founding_manifest() -> dict[str, Any]:
    """Load the versioned founding-memory manifest from the Mem package."""
    raw = files("memai").joinpath("identity", "founding_memory.json").read_text(
        encoding="utf-8"
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("identity memory manifest must be an object")
    return payload


def load_founding_memories() -> list[dict[str, Any]]:
    """Return the memory entries from the canonical manifest."""
    payload = load_founding_manifest()
    memories = payload.get("memories", [])
    if not isinstance(memories, list):
        raise ValueError("identity memory manifest must contain a memories list")
    return [item for item in memories if isinstance(item, dict) and item.get("memory_id")]


def founding_manifest_version() -> str:
    """Return a stable version token for governance baseline checks."""
    payload = load_founding_manifest()
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"v{payload.get('schema_version', 1)}-{digest}"


def is_founding_memory_id(memory_id: str) -> bool:
    return str(memory_id or "") in {
        str(item["memory_id"]) for item in load_founding_memories()
    }


def load_founding_story() -> str:
    """Return the canonical narrative that provides evidence for the seed."""
    return files("memai").joinpath("identity", "founding_story.md").read_text(
        encoding="utf-8"
    )


def reconcile_released_identity_revisions(conn) -> int:
    """Mark approved proposals named by the manifest as released."""
    manifest = load_founding_manifest()
    proposal_ids = [
        str(item).strip()
        for item in manifest.get("release_evidence", [])
        if str(item).strip().startswith("identity-revision-")
    ]
    if not proposal_ids:
        return 0
    released_at = str(
        manifest.get("released_at")
        or datetime.now(timezone.utc).isoformat()
    )
    release_version = founding_manifest_version()
    updated = 0
    for proposal_id in dict.fromkeys(proposal_ids):
        cursor = conn.execute(
            "UPDATE identity_revision_proposals "
            "SET status = 'released', release_version = ?, released_at = ? "
            "WHERE proposal_id = ? AND status = 'approved_pending_release'",
            (release_version, released_at, proposal_id),
        )
        updated += max(0, int(cursor.rowcount or 0))
    return updated


def ensure_founding_memories(conn) -> int:
    """Restore canonical founding memories and return the number inserted."""
    inserted = 0
    recorded_at = str(
        load_founding_manifest().get("recorded_at")
        or datetime.now(timezone.utc).isoformat()
    )
    now = datetime.now(timezone.utc).isoformat()
    for item in load_founding_memories():
        memory_id = str(item["memory_id"])
        exists = conn.execute(
            "SELECT 1 FROM compressed_memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO compressed_memories (
                memory_id, memory_type, title, summary,
                timespan_start, timespan_end, importance, confidence,
                topics, entities, source_turns, parent_id, compressed_at,
                compression_level, status, weight, event_kind, pinned, hidden,
                owner_id, workspace_id
            ) VALUES (?, 'event', ?, ?, ?, ?, 1.0, 1.0, ?, ?, ?, NULL, ?, 0,
                      'active', 1.0, ?, 1, 0, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                memory_type = excluded.memory_type,
                title = excluded.title,
                summary = excluded.summary,
                timespan_start = excluded.timespan_start,
                timespan_end = excluded.timespan_end,
                importance = excluded.importance,
                confidence = excluded.confidence,
                topics = excluded.topics,
                entities = excluded.entities,
                source_turns = excluded.source_turns,
                parent_id = NULL,
                compression_level = 0,
                status = 'active',
                superseded_by = NULL,
                weight = 1.0,
                event_kind = excluded.event_kind,
                pinned = 1,
                hidden = 0,
                owner_id = excluded.owner_id,
                workspace_id = excluded.workspace_id
            """,
            (
                memory_id,
                str(item.get("title") or memory_id),
                str(item.get("summary") or ""),
                recorded_at,
                recorded_at,
                json.dumps(item.get("topics") or [], ensure_ascii=False),
                json.dumps(item.get("entities") or [], ensure_ascii=False),
                json.dumps([_SOURCE_PREFIX + memory_id], ensure_ascii=False),
                now,
                str(item.get("event_kind") or "decision"),
                GLOBAL_SCOPE_ID,
                GLOBAL_SCOPE_ID,
            ),
        )
        inserted += int(exists is None)
    return inserted
