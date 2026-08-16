"""Tier 1 → Tier 2 Bridge: Feed expired SQLite turns into Mem ChroniclePipeline.

This module provides a standalone bridge that converts Tier 1 (short-term
SQLite conversation store) turns into Tier 2 (long-term chronicle memory)
structured events, scenes, arcs, and epochs via the existing Mem pipeline.

Integration points:
- Called by ``memory_service._tier2_bridge_cycle()`` automatically
- Can also be invoked manually via the ``/tier2/compress`` API endpoint
- Reuses ``ChroniclePipeline.ingest()`` without any modification
"""

from __future__ import annotations

import json
import logging
import sqlite3
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from systems.memory.database import open_memory_sqlite
from systems.memory.scope import DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID
from systems.memory.quality_signals import (
    has_explicit_negation as _has_explicit_negation,
    identifiers as _identifiers,
    source_support as _source_support,
)

logger = logging.getLogger(__name__)

_MAX_QUALITY_RETRIES = 3
_NON_EVALUATION_TURN_SQL = (
    "(json_valid(COALESCE(tags, '[]')) = 0 OR NOT EXISTS ("
    "SELECT 1 FROM json_each(COALESCE(tags, '[]')) "
    "WHERE lower(CAST(value AS TEXT)) = 'evaluation'))"
)
_RETRY_ELIGIBLE_TURN_SQL = (
    "compression_retry_count < ? AND "
    "(compression_retry_after IS NULL OR compression_retry_after <= ?)"
)


def _parse_utc_timestamp(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp and normalize it to UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_value(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _stable_refs(item: Any, stable_ids: Dict[str, str] | None = None) -> list[str]:
    stable_ids = stable_ids or {}
    refs: list[str] = []
    for attr in ("source_turns", "evidence_refs", "child_ids"):
        values = getattr(item, attr, None)
        if values:
            refs.extend(stable_ids.get(str(value), str(value)) for value in values if str(value))
    return sorted(set(refs))


def _stable_compressed_memory_id(
    memory_type: str,
    item: Any,
    stable_ids: Dict[str, str] | None = None,
    scope_key: str = "",
) -> str:
    payload = {
        "memory_type": memory_type,
        "title": str(getattr(item, "title", "") or "").strip(),
        "summary": str(getattr(item, "summary", "") or "").strip(),
        "refs": _stable_refs(item, stable_ids),
        "timespan_start": _iso_value(getattr(item, "timespan_start", "")),
        "timespan_end": _iso_value(getattr(item, "timespan_end", "")),
        "scope": scope_key,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{memory_type}_{digest}"


def _build_stable_cmem_ids(
    pipeline_result,
    *,
    scope_key: str = "",
) -> Dict[str, str]:
    stable_ids: Dict[str, str] = {}
    for memory_type, items in (
        ("event", getattr(pipeline_result, "events", [])),
        ("scene", getattr(pipeline_result, "scenes", [])),
        ("arc", getattr(pipeline_result, "arcs", [])),
        ("epoch", getattr(pipeline_result, "epochs", [])),
    ):
        for item in items:
            original_id = str(getattr(item, "id", "") or "").strip()
            if original_id:
                stable_ids[original_id] = _stable_compressed_memory_id(
                    memory_type,
                    item,
                    stable_ids,
                    scope_key,
                )
    return stable_ids


def _write_compressed_memories_to_db(
    conn,
    pipeline_result,
    now: str,
    *,
    scope: tuple[str, str, str] | None = None,
) -> int:
    """Write the complete pipeline result into the canonical scoped tables.

    Lifecycle: Event(L0,w=1.0) → Scene(L1,w=0.7) → Arc(L2,w=0.4) → Epoch(L3,w=0.2).
    """
    written = 0
    owner_id, workspace_id, memory_domain = scope or _pipeline_scope(
        conn, pipeline_result
    )
    scope_key = f"{owner_id}\0{workspace_id}\0{memory_domain}"
    stable_ids = _build_stable_cmem_ids(pipeline_result, scope_key=scope_key)
    memory_type_by_pipeline_id = {
        str(item.id): memory_type
        for memory_type, items in (
            ("event", pipeline_result.events),
            ("scene", pipeline_result.scenes),
            ("arc", pipeline_result.arcs),
            ("epoch", pipeline_result.epochs),
        )
        for item in items
    }
    inferred_parent_by_child: dict[tuple[str, str], str] = {}
    for child_type, parents in (
        ("event", pipeline_result.scenes),
        ("scene", pipeline_result.arcs),
        ("arc", pipeline_result.epochs),
    ):
        for parent in parents:
            for child_id in list(getattr(parent, "child_ids", []) or []):
                key = (child_type, str(child_id))
                inferred_parent_by_child.setdefault(key, str(parent.id))

    def timeline_parent_id(
        item,
        *,
        memory_type: str,
        expected_type: str,
    ) -> str | None:
        parent_ids = list(getattr(item, "parent_ids", []) or [])
        source_id = (
            str(parent_ids[0])
            if parent_ids
            else inferred_parent_by_child.get((memory_type, str(item.id)))
        )
        if not source_id:
            return None
        if memory_type_by_pipeline_id.get(source_id) != expected_type:
            logger.warning(
                "Discarded invalid %s -> %s timeline relation for %s",
                getattr(item, "type", "memory"),
                memory_type_by_pipeline_id.get(source_id, "missing"),
                item.id,
            )
            return None
        return stable_ids[source_id]
    event_kind_by_id = {
        str(event.id): (
            event.event_kind.value
            if hasattr(event.event_kind, "value")
            else str(event.event_kind)
        )
        for event in pipeline_result.events
        if getattr(event, "event_kind", None)
    }
    scene_kind_by_id: dict[str, str | None] = {}
    arc_kind_by_id: dict[str, str | None] = {}
    for event in pipeline_result.events:
        resolved_parent_id = timeline_parent_id(
            event,
            memory_type="event",
            expected_type="scene",
        )
        ek = event.event_kind.value if hasattr(event.event_kind, 'value') else str(event.event_kind)
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "timeline_parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(event.id, event.id), "event", event.title, event.summary,
                event.timespan_start.isoformat(), event.timespan_end.isoformat(),
                event.importance, event.confidence,
                json.dumps(event.topics), json.dumps(event.entities),
                json.dumps(event.source_turns), resolved_parent_id, now,
                0, "active", 1.0, ek, owner_id, workspace_id, memory_domain, now,
            ),
        )
        written += 1
    for scene in pipeline_result.scenes:
        resolved_parent_id = timeline_parent_id(
            scene,
            memory_type="scene",
            expected_type="arc",
        )
        child_ids = set(getattr(scene, "child_ids", []) or [])
        child_kinds = [
            event_kind_by_id[str(item)]
            for item in child_ids
            if str(item) in event_kind_by_id
        ]
        scene_kind = max(set(child_kinds), key=child_kinds.count) if child_kinds else None
        scene_kind_by_id[str(scene.id)] = scene_kind
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "timeline_parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(scene.id, scene.id), "scene", scene.title, scene.summary,
                scene.timespan_start.isoformat(), scene.timespan_end.isoformat(),
                scene.importance, scene.confidence,
                json.dumps(scene.topics), json.dumps(scene.entities),
                json.dumps(scene.evidence_refs), resolved_parent_id, now,
                1, "active", 0.7, scene_kind, owner_id, workspace_id, memory_domain, now,
            ),
        )
        written += 1
    for arc in pipeline_result.arcs:
        resolved_parent_id = timeline_parent_id(
            arc,
            memory_type="arc",
            expected_type="epoch",
        )
        arc_child_kinds = [
            scene_kind_by_id[str(item)]
            for item in (getattr(arc, "child_ids", []) or [])
            if scene_kind_by_id.get(str(item))
        ]
        arc_kind = max(set(arc_child_kinds), key=arc_child_kinds.count) if arc_child_kinds else None
        arc_kind_by_id[str(arc.id)] = arc_kind
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "timeline_parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(arc.id, arc.id), "arc", arc.title, arc.summary,
                arc.timespan_start.isoformat(), arc.timespan_end.isoformat(),
                arc.importance, arc.confidence,
                json.dumps(arc.topics), json.dumps(arc.entities),
                json.dumps(arc.evidence_refs), resolved_parent_id, now,
                2, "active", 0.4, arc_kind, owner_id, workspace_id, memory_domain, now,
            ),
        )
        written += 1
    for epoch in pipeline_result.epochs:
        epoch_child_kinds = [
            arc_kind_by_id[str(item)]
            for item in (getattr(epoch, "child_ids", []) or [])
            if arc_kind_by_id.get(str(item))
        ]
        epoch_kind = max(set(epoch_child_kinds), key=epoch_child_kinds.count) if epoch_child_kinds else None
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "timeline_parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(epoch.id, epoch.id), "epoch", epoch.title, epoch.summary,
                epoch.timespan_start.isoformat(), epoch.timespan_end.isoformat(),
                epoch.importance, epoch.confidence,
                json.dumps(epoch.topics), json.dumps(epoch.entities),
                json.dumps(epoch.evidence_refs), None, now,
                3, "active", 0.2, epoch_kind, owner_id, workspace_id, memory_domain, now,
            ),
        )
        written += 1
    # Backfill the immutable transaction-time anchor for rows written without
    # an explicit created_at (COALESCE(created_at, compressed_at) readers).
    conn.execute(
        "UPDATE compressed_memories SET created_at = compressed_at "
        "WHERE created_at IS NULL"
    )
    # Build the entity graph from this pipeline output (co-occurrence edges).
    from systems.memory.entity_graph import update_entity_graph

    for memory_type, items in (
        ("event", pipeline_result.events),
        ("scene", pipeline_result.scenes),
        ("arc", pipeline_result.arcs),
        ("epoch", pipeline_result.epochs),
    ):
        for item in items:
            update_entity_graph(
                conn,
                memory_id=stable_ids.get(item.id, item.id),
                memory_type=memory_type,
                entities=getattr(item, "entities", []) or [],
                owner_id=owner_id,
                workspace_id=workspace_id,
                memory_domain=memory_domain,
                now=now,
            )
    return written


def _pipeline_scope(conn, pipeline_result) -> tuple[str, str, str]:
    source_turn_ids: list[str] = []
    for collection_name in ("events",):
        for item in getattr(pipeline_result, collection_name, []) or []:
            source_turn_ids.extend(
                str(value)
                for value in getattr(item, "source_turns", []) or []
                if str(value)
            )
    for turn_id in dict.fromkeys(source_turn_ids):
        row = conn.execute(
            "SELECT owner_id, workspace_id, memory_domain FROM turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        if row:
            return str(row[0]), str(row[1]), str(row[2])
    return DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID, "agent_interaction"


@dataclass
class BridgeResult:
    """Result of a Tier 1 → Tier 2 compression cycle."""

    turns_processed: int
    events_generated: int
    scenes_generated: int
    arcs_generated: int
    epochs_generated: int
    profiles_generated: int
    status: str = "compressed"
    dry_run: bool = False
    candidate_count: int = 0
    cutoff: str = ""
    force_oldest: bool = False
    low_relevance_fallback: bool = False
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_domain: str = "agent_interaction"
    sample_turn_ids: List[str] = None
    errors: List[str] = None
    quality_evidence: Dict[str, Any] | None = None

    def __post_init__(self):
        if self.sample_turn_ids is None:
            self.sample_turn_ids = []
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turns_processed": self.turns_processed,
            "events_generated": self.events_generated,
            "scenes_generated": self.scenes_generated,
            "arcs_generated": self.arcs_generated,
            "epochs_generated": self.epochs_generated,
            "profiles_generated": self.profiles_generated,
            "status": self.status,
            "dry_run": self.dry_run,
            "candidate_count": self.candidate_count,
            "cutoff": self.cutoff,
            "force_oldest": self.force_oldest,
            "low_relevance_fallback": self.low_relevance_fallback,
            "owner_id": self.owner_id,
            "workspace_id": self.workspace_id,
            "memory_domain": self.memory_domain,
            "sample_turn_ids": self.sample_turn_ids,
            "errors": self.errors,
            "quality_evidence": self.quality_evidence,
        }


@dataclass(frozen=True)
class CandidateBatch:
    turns: List[Dict[str, Any]]
    cutoff: str
    force_oldest: bool
    low_relevance_fallback: bool
    owner_id: str
    workspace_id: str
    memory_domain: str


class Tier1ToTier2Bridge:
    """Bridge Tier 1 SQLite turns into Tier 2 Mem Pipeline structured memory.

    Usage::

        bridge = Tier1ToTier2Bridge(db_path="<VOIDCUBE_HOME>/runtime/memory/memory.db")
        result = bridge.run_cycle()
        print(result.to_dict())
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        retention_days: int = 30,
        batch_size: int = 25,
        min_relevance: float = 0.1,
        archive_keep_original: bool = True,
        max_turns: int = 10000,
        pipeline_factory: Callable[[], Any] | None = None,
        compression_degraded: bool | None = None,
        min_backlink_completeness: float = 1.0,
        max_compression_ratio: float = 1.0,
        max_degraded_fraction: float = 0.0,
        min_source_support: float = 0.35,
        min_identifier_fidelity: float = 1.0,
        min_polarity_consistency: float = 1.0,
        memory_domain: str = "agent_interaction",
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.min_relevance = min_relevance
        self.archive_keep_original = archive_keep_original
        self.max_turns = max_turns
        self.memory_domain = str(memory_domain)
        self.owner_id = str(owner_id)
        self.workspace_id = str(workspace_id)
        self.pipeline_factory = pipeline_factory
        self.compression_degraded = compression_degraded
        self.quality_thresholds = {
            "min_backlink_completeness": min_backlink_completeness,
            "max_compression_ratio": max_compression_ratio,
            "max_degraded_fraction": max_degraded_fraction,
            "min_source_support": min_source_support,
            "min_identifier_fidelity": min_identifier_fidelity,
            "min_polarity_consistency": min_polarity_consistency,
        }

    # ── Query candidates ──────────────────────────────────────────

    def select_candidate_turns(self, *, force_oldest: bool = False) -> CandidateBatch:
        """Select one deterministic candidate batch and report fallback semantics."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        conn = open_memory_sqlite(self.db_path)
        base_conditions = [
            "compression_status IN ('pending', 'retry_wait')",
            _RETRY_ELIGIBLE_TURN_SQL,
            "memory_domain = ?",
            _NON_EVALUATION_TURN_SQL,
        ]
        base_params: list[Any] = [
            _MAX_QUALITY_RETRIES,
            datetime.now(timezone.utc).isoformat(),
            self.memory_domain,
        ]
        if self.owner_id is not None:
            base_conditions.append("owner_id = ?")
            base_params.append(self.owner_id)
        if self.workspace_id is not None:
            base_conditions.append("workspace_id = ?")
            base_params.append(self.workspace_id)
        time_clause = [] if force_oldest else ["timestamp < ?"]
        time_params: list[Any] = [] if force_oldest else [cutoff]
        eligible_conditions = [*time_clause, *base_conditions, "relevance_score >= ?"]
        eligible_params = [*time_params, *base_params, self.min_relevance]
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "owner_id, workspace_id "
            ", memory_domain FROM turns WHERE "
            + " AND ".join(eligible_conditions)
            + " ORDER BY timestamp ASC LIMIT ?",
            [*eligible_params, self.batch_size],
        ).fetchall()
        low_relevance_fallback = False
        if not rows:
            rows = conn.execute(
                "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
                "owner_id, workspace_id "
                ", memory_domain FROM turns WHERE "
                + " AND ".join([*time_clause, *base_conditions])
                + " ORDER BY timestamp ASC LIMIT ?",
                [*time_params, *base_params, self.batch_size],
            ).fetchall()
            low_relevance_fallback = bool(rows)
        owner_id = str(rows[0][6]) if rows else DEFAULT_OWNER_ID
        workspace_id = str(rows[0][7]) if rows else DEFAULT_WORKSPACE_ID
        memory_domain = str(rows[0][8]) if rows else self.memory_domain
        conn.close()
        turns = [
            {
                "turn_id": r[0],
                "session_id": r[1],
                "speaker": r[2],
                "text": r[3],
                "timestamp": r[4],
                "relevance_score": r[5],
                "owner_id": r[6],
                "workspace_id": r[7],
                "memory_domain": r[8],
            }
            for r in rows
        ]
        return CandidateBatch(
            turns=turns,
            cutoff=cutoff,
            force_oldest=force_oldest,
            low_relevance_fallback=low_relevance_fallback,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )

    def find_candidate_turns(self) -> List[Dict[str, Any]]:
        """Find age-eligible turns, or oldest turns after volume overflow."""
        force_oldest = self._active_turn_count() >= self.max_turns
        return self.select_candidate_turns(force_oldest=force_oldest).turns

    def _active_turn_count(self, *, conn=None) -> int:
        owns_connection = conn is None
        if conn is None:
            conn = open_memory_sqlite(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compression_status IN ('pending', 'retry_wait') "
            "AND memory_domain = ? AND " + _NON_EVALUATION_TURN_SQL
            + self._scope_sql_suffix(),
            [self.memory_domain, *self._scope_params()],
        ).fetchone()[0]
        if owns_connection:
            conn.close()
        return int(count)

    def count_candidates(self) -> int:
        """Count turns that can be selected for compression right now."""
        return int(self.candidate_health_snapshot()["eligible_count"])

    def candidate_health_snapshot(self) -> Dict[str, Any]:
        """Describe the exact candidate set the bridge can select right now."""
        current_time = datetime.now(timezone.utc)
        now = current_time.isoformat()
        cutoff = (current_time - timedelta(days=self.retention_days)).isoformat()
        conn = open_memory_sqlite(self.db_path)
        retry_params: list[Any] = [_MAX_QUALITY_RETRIES, now]
        row = conn.execute(
            "SELECT COUNT(*), MIN(timestamp) FROM turns "
            "WHERE timestamp < ? AND compression_status IN ('pending', 'retry_wait') "
            "AND " + _RETRY_ELIGIBLE_TURN_SQL + " AND memory_domain = ? AND "
            + _NON_EVALUATION_TURN_SQL + self._scope_sql_suffix(),
            [cutoff, *retry_params, self.memory_domain, *self._scope_params()],
        ).fetchone()
        count, oldest_at = int(row[0]), row[1]
        force_oldest = False
        if count == 0:
            total = self._active_turn_count(conn=conn)
            if total >= self.max_turns:
                force_oldest = True
                row = conn.execute(
                    "SELECT COUNT(*), MIN(timestamp) FROM turns "
                    "WHERE compression_status IN ('pending', 'retry_wait') AND "
                    + _RETRY_ELIGIBLE_TURN_SQL + " AND memory_domain = ? AND "
                    + _NON_EVALUATION_TURN_SQL + self._scope_sql_suffix(),
                    [*retry_params, self.memory_domain, *self._scope_params()],
                ).fetchone()
                count, oldest_at = int(row[0]), row[1]
        conn.close()
        oldest = _parse_utc_timestamp(oldest_at)
        oldest_age_seconds = (
            max(0.0, (current_time - oldest).total_seconds()) if oldest else 0.0
        )
        return {
            "eligible_count": count,
            "oldest_candidate_at": oldest_at,
            "oldest_candidate_age_seconds": round(oldest_age_seconds, 3),
            "force_oldest": force_oldest,
        }

    def needs_compression(self) -> bool:
        """Return whether at least one compression batch can run now."""
        return self.count_candidates() > 0

    def _scope_sql_suffix(self) -> str:
        conditions = []
        if self.owner_id is not None:
            conditions.append(" AND owner_id = ?")
        if self.workspace_id is not None:
            conditions.append(" AND workspace_id = ?")
        return "".join(conditions)

    def _scope_params(self) -> list[str]:
        params: list[str] = []
        if self.owner_id is not None:
            params.append(self.owner_id)
        if self.workspace_id is not None:
            params.append(self.workspace_id)
        return params

    # ── Bridge to Tier 2 ──────────────────────────────────────────

    def _build_pipeline(self):
        """Build ChroniclePipeline — LLM-first with heuristic fallback.

        Uses the shared ``llm_extraction.build_llm_first_pipeline`` so the
        LLM extraction result is cached by (task, model, input hash), bounding
        the cost of re-compressing the same turn batch. LLM credentials are
        resolved by ``memai.model_config.resolve_mem_llm_client`` (the
        ``extraction`` role) — the same source the supervisor and the rest of
        ``MemoryService`` use, so the model selection is consistent and
        controlled entirely by the CLI ``/api`` command's ``memory.llm.*``.
        """
        if self.pipeline_factory is not None:
            return self.pipeline_factory()

        from systems.memory.llm_extraction import build_llm_first_pipeline

        return build_llm_first_pipeline(self.db_path, role="extraction")

    def _build_tier2_output(self, turns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert turns to TranscriptTurn and feed into ChroniclePipeline."""
        from memai.schema import TranscriptTurn

        transcript_turns = []
        for t in turns:
            parsed_ts = datetime.fromisoformat(t["timestamp"])
            if parsed_ts.tzinfo is None:
                parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
            transcript_turns.append(
                TranscriptTurn(
                    turn_id=t["turn_id"],
                    speaker=t["speaker"],
                    text=t["text"],
                    timestamp=parsed_ts,
                )
            )

        pipeline = self._build_pipeline()
        result = pipeline.ingest(transcript_turns)
        # VoidCube Profile memory is captured at user-turn ingestion. Tier 2
        # extraction owns only timeline resources.
        result.profile_memories = []
        compression_degraded = self.compression_degraded
        if compression_degraded is None:
            backend = getattr(getattr(pipeline, "event_extractor", None), "backend", None)
            compression_degraded = getattr(backend, "name", None) == "heuristic"

        return {
            "events": [e.to_dict() for e in result.events],
            "scenes": [s.to_dict() for s in result.scenes],
            "arcs": [a.to_dict() for a in result.arcs],
            "epochs": [ep.to_dict() for ep in result.epochs],
            "_pipeline_result": result,
            "_compression_degraded": bool(compression_degraded),
        }

    def _evaluate_quality(
        self,
        turns: List[Dict[str, Any]],
        tier2_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = tier2_output.get("_pipeline_result")
        events = list(getattr(result, "events", []) or [])
        candidate_ids = {str(turn["turn_id"]) for turn in turns}
        covered_turn_ids: set[str] = set()
        backlinked_events = 0
        event_summary_chars = 0
        source_support_scores: list[float] = []
        identifier_fidelity_scores: list[float] = []
        polarity_consistency_scores: list[float] = []
        accepted_event_ids: list[str] = []
        rejected_event_reasons: dict[str, list[str]] = {}
        unsupported_identifiers: set[str] = set()
        turn_index = {str(turn["turn_id"]): turn for turn in turns}

        for event in events:
            source_turns = {
                str(turn_id)
                for turn_id in (getattr(event, "source_turns", []) or [])
                if str(turn_id)
            }
            valid_source_turns = source_turns & candidate_ids
            covered_turn_ids.update(valid_source_turns)
            if source_turns and source_turns <= candidate_ids:
                backlinked_events += 1
            summary_text = str(getattr(event, "summary", "") or "").strip()
            summary = " ".join(
                part
                for part in (
                    str(getattr(event, "title", "") or "").strip(),
                    summary_text,
                )
                if part
            )
            event_summary_chars += len(summary_text)
            source_records = [turn_index[turn_id] for turn_id in valid_source_turns]
            source_text = "\n".join(
                str(turn.get("text", "") or "") for turn in source_records
            )
            source_support_scores.append(_source_support(summary, source_text))
            summary_identifiers = _identifiers(summary)
            source_identifiers = _identifiers(source_text)
            unsupported = summary_identifiers - source_identifiers
            unsupported_identifiers.update(unsupported)
            identifier_fidelity_scores.append(
                1.0
                if not summary_identifiers
                else len(summary_identifiers & source_identifiers)
                / len(summary_identifiers)
            )
            source_polarities = {
                _has_explicit_negation(turn.get("text", ""))
                for turn in source_records
            }
            polarity_consistency_scores.append(
                1.0
                if len(source_polarities) != 1
                or _has_explicit_negation(summary) in source_polarities
                else 0.0
            )
            event_reasons: list[str] = []
            event_backlink_completeness = (
                1.0 if source_turns and source_turns == valid_source_turns else 0.0
            )
            if event_backlink_completeness < self.quality_thresholds["min_backlink_completeness"]:
                event_reasons.append("backlink_completeness")
            if source_support_scores[-1] < self.quality_thresholds["min_source_support"]:
                event_reasons.append("source_support")
            if identifier_fidelity_scores[-1] < self.quality_thresholds["min_identifier_fidelity"]:
                event_reasons.append("identifier_fidelity")
            if polarity_consistency_scores[-1] < self.quality_thresholds["min_polarity_consistency"]:
                event_reasons.append("polarity_consistency")
            event_id = str(getattr(event, "id", ""))
            if not event_reasons and event_id:
                accepted_event_ids.append(event_id)
            elif event_id:
                rejected_event_reasons[event_id] = event_reasons

        candidate_count = len(turns)
        event_count = len(events)
        source_chars = sum(len(str(turn.get("text", "") or "").strip()) for turn in turns)
        event_coverage = len(covered_turn_ids) / candidate_count if candidate_count else 1.0
        backlink_completeness = backlinked_events / event_count if event_count else 0.0
        compression_ratio = event_summary_chars / source_chars if source_chars else 0.0
        degraded_event_count = (
            event_count if tier2_output.get("_compression_degraded") else 0
        )
        degraded_fraction = degraded_event_count / event_count if event_count else 0.0
        source_support = (
            sum(source_support_scores) / len(source_support_scores)
            if source_support_scores else 0.0
        )
        identifier_fidelity = (
            sum(identifier_fidelity_scores) / len(identifier_fidelity_scores)
            if identifier_fidelity_scores else 0.0
        )
        polarity_consistency = (
            sum(polarity_consistency_scores) / len(polarity_consistency_scores)
            if polarity_consistency_scores else 0.0
        )
        valid_event_fraction = len(accepted_event_ids) / event_count if event_count else 0.0
        source_supported_event_count = sum(
            score >= self.quality_thresholds["min_source_support"]
            for score in source_support_scores
        )

        failed_checks: list[str] = []
        if compression_ratio > self.quality_thresholds["max_compression_ratio"]:
            failed_checks.append("compression_ratio")
        if degraded_fraction > self.quality_thresholds["max_degraded_fraction"]:
            failed_checks.append("degraded_fraction")
        if not accepted_event_ids:
            failed_checks.append("no_valid_events")

        evaluated_at = datetime.now(timezone.utc).isoformat()
        audit_payload = {
            "evaluated_at": evaluated_at,
            "candidate_count": candidate_count,
            "event_count": event_count,
            "covered_turn_count": len(covered_turn_ids),
            "event_coverage": round(event_coverage, 6),
            "valid_event_count": len(accepted_event_ids),
            "valid_event_fraction": round(valid_event_fraction, 6),
            "accepted_event_ids": accepted_event_ids,
            "rejected_event_reasons": rejected_event_reasons,
            "backlinked_event_count": backlinked_events,
            "backlink_completeness": round(backlink_completeness, 6),
            "source_chars": source_chars,
            "event_summary_chars": event_summary_chars,
            "compression_ratio": round(compression_ratio, 6),
            "degraded_event_count": degraded_event_count,
            "degraded_fraction": round(degraded_fraction, 6),
            "source_supported_event_count": source_supported_event_count,
            "source_support": round(source_support, 6),
            "identifier_fidelity": round(identifier_fidelity, 6),
            "polarity_consistency": round(polarity_consistency, 6),
            "unsupported_identifiers": sorted(unsupported_identifiers),
            "thresholds": dict(self.quality_thresholds),
            "failed_checks": failed_checks,
            "sample_turn_ids": [turn["turn_id"] for turn in turns[:5]],
            "memory_domain": (
                str(turns[0].get("memory_domain") or "agent_interaction")
                if turns
                else self.memory_domain
            ),
            "owner_id": (
                str(turns[0].get("owner_id") or DEFAULT_OWNER_ID)
                if turns else (self.owner_id or DEFAULT_OWNER_ID)
            ),
            "workspace_id": (
                str(turns[0].get("workspace_id") or DEFAULT_WORKSPACE_ID)
                if turns else (self.workspace_id or DEFAULT_WORKSPACE_ID)
            ),
        }
        audit_seed = json.dumps(audit_payload, ensure_ascii=False, sort_keys=True)
        audit_payload["audit_id"] = "cqa_" + hashlib.sha1(
            audit_seed.encode("utf-8")
        ).hexdigest()[:20]
        audit_payload["passed"] = not failed_checks
        return audit_payload

    @staticmethod
    def _filter_invalid_events(
        tier2_output: Dict[str, Any],
        accepted_event_ids: Sequence[str],
    ) -> None:
        """Remove bad events and every higher summary they may have influenced."""
        result = tier2_output.get("_pipeline_result")
        if result is None:
            return
        accepted = {str(item) for item in accepted_event_ids}
        events = [
            event
            for event in getattr(result, "events", [])
            if str(getattr(event, "id", "")) in accepted
        ]
        event_ids = {str(getattr(event, "id", "")) for event in events}
        original_scene_ids = {
            str(getattr(scene, "id", ""))
            for scene in getattr(result, "scenes", [])
        }
        scenes = []
        for scene in getattr(result, "scenes", []):
            children = {
                str(item) for item in (getattr(scene, "child_ids", []) or [])
            }
            if not children or not children <= event_ids:
                continue
            scenes.append(scene)
        scene_ids = {str(getattr(scene, "id", "")) for scene in scenes}
        arcs = []
        for arc in getattr(result, "arcs", []):
            children = {
                str(item) for item in (getattr(arc, "child_ids", []) or [])
            }
            if not children or not children <= scene_ids:
                continue
            arcs.append(arc)
        arc_ids = {str(getattr(arc, "id", "")) for arc in arcs}
        epochs = []
        for epoch in getattr(result, "epochs", []):
            children = {
                str(item) for item in (getattr(epoch, "child_ids", []) or [])
            }
            if not children or not children <= arc_ids:
                continue
            epochs.append(epoch)
        result.events, result.scenes, result.arcs, result.epochs = events, scenes, arcs, epochs
        tier2_output["events"] = [event.to_dict() for event in events]
        tier2_output["scenes"] = [scene.to_dict() for scene in scenes]
        tier2_output["arcs"] = [arc.to_dict() for arc in arcs]
        tier2_output["epochs"] = [epoch.to_dict() for epoch in epochs]

    @staticmethod
    def _write_quality_audit(
        conn: sqlite3.Connection,
        quality_evidence: Dict[str, Any],
        status: str,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO compression_quality_audit "
            "(audit_id, memory_domain, owner_id, workspace_id, evaluated_at, status, candidate_count, event_count, "
            "covered_turn_count, event_coverage, backlinked_event_count, "
            "backlink_completeness, source_chars, event_summary_chars, "
            "compression_ratio, degraded_event_count, degraded_fraction, "
            "source_supported_event_count, source_support, identifier_fidelity, "
            "polarity_consistency, unsupported_identifiers, thresholds, failed_checks, "
            "sample_turn_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quality_evidence["audit_id"],
                quality_evidence["memory_domain"],
                quality_evidence["owner_id"],
                quality_evidence["workspace_id"],
                quality_evidence["evaluated_at"],
                status,
                quality_evidence["candidate_count"],
                quality_evidence["event_count"],
                quality_evidence["covered_turn_count"],
                quality_evidence["event_coverage"],
                quality_evidence["backlinked_event_count"],
                quality_evidence["backlink_completeness"],
                quality_evidence["source_chars"],
                quality_evidence["event_summary_chars"],
                quality_evidence["compression_ratio"],
                quality_evidence["degraded_event_count"],
                quality_evidence["degraded_fraction"],
                quality_evidence["source_supported_event_count"],
                quality_evidence["source_support"],
                quality_evidence["identifier_fidelity"],
                quality_evidence["polarity_consistency"],
                json.dumps(quality_evidence["unsupported_identifiers"]),
                json.dumps(quality_evidence["thresholds"], sort_keys=True),
                json.dumps(quality_evidence["failed_checks"]),
                json.dumps(quality_evidence["sample_turn_ids"]),
            ),
        )

    def _persist_quality_audit(
        self,
        quality_evidence: Dict[str, Any],
        status: str,
    ) -> None:
        conn = open_memory_sqlite(self.db_path)
        try:
            self._write_quality_audit(conn, quality_evidence, status)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _record_quality_rejection(self, turns: Sequence[Dict[str, Any]]) -> None:
        """Bound retries without accepting a summary that failed its gate."""
        if not turns:
            return
        conn = open_memory_sqlite(self.db_path)
        try:
            for turn in turns:
                row = conn.execute(
                    "SELECT compression_retry_count FROM turns WHERE turn_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (turn["turn_id"], turn["owner_id"], turn["workspace_id"], turn["memory_domain"]),
                ).fetchone()
                retry_count = int(row[0] or 0) + 1 if row else 1
                if retry_count >= _MAX_QUALITY_RETRIES:
                    state = "quality_quarantined"
                    retry_after = None
                else:
                    state = "retry_wait"
                    retry_after = (
                        datetime.now(timezone.utc) + timedelta(hours=2 ** (retry_count - 1))
                    ).isoformat()
                conn.execute(
                    "UPDATE turns SET compression_retry_count = ?, "
                    "compression_retry_after = ?, compression_status = ? "
                    "WHERE turn_id = ? AND owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ?",
                    (retry_count, retry_after, state, turn["turn_id"], turn["owner_id"],
                     turn["workspace_id"], turn["memory_domain"]),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Archive processed turns ───────────────────────────────────

    def _commit_tier2_output(
        self,
        turns: List[Dict[str, Any]],
        tier2_output: Dict[str, Any],
        quality_evidence: Dict[str, Any],
    ) -> None:
        """Move processed turns to archive with Tier 2 back-references."""
        result = tier2_output.get("_pipeline_result")
        if result is None:
            return
        if not getattr(result, "events", None):
            logger.warning(
                "Tier 2 bridge produced no events for %d turns; keeping turns uncompressed.",
                len(turns),
            )
            return

        # Build turn_id → event_ids / scene_ids mappings
        first_turn = turns[0]
        scope = (
            str(first_turn["owner_id"]),
            str(first_turn["workspace_id"]),
            str(first_turn["memory_domain"]),
        )
        scope_key = "\0".join(scope)
        stable_ids = _build_stable_cmem_ids(result, scope_key=scope_key)
        turn_to_events: Dict[str, List[str]] = {}
        for event in result.events:
            for src_turn_id in event.source_turns:
                turn_to_events.setdefault(src_turn_id, []).append(stable_ids.get(event.id, event.id))

        turn_to_scenes: Dict[str, List[str]] = {}
        for scene in result.scenes:
            for ev_id in scene.child_ids:
                stable_ev_id = stable_ids.get(ev_id, ev_id)
                for turn_id, ev_ids in turn_to_events.items():
                    if stable_ev_id in ev_ids:
                        turn_to_scenes.setdefault(turn_id, []).append(stable_ids.get(scene.id, scene.id))

        now = datetime.now(timezone.utc).isoformat()
        conn = open_memory_sqlite(self.db_path)
        try:
            try:
                for t in turns:
                    turn_id = t["turn_id"]
                    event_ids = turn_to_events.get(turn_id, [])
                    scene_ids = turn_to_scenes.get(turn_id, [])
                    original_text = t["text"] if self.archive_keep_original else None
                    text_summary = t["text"][:500]

                    conn.execute(
                        "INSERT OR REPLACE INTO turns_archive "
                        "(turn_id, session_id, speaker, text_summary, original_text, "
                        "timestamp, compressed_at, event_ids, scene_ids, owner_id, workspace_id, "
                        "memory_domain) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            turn_id, t["session_id"], t["speaker"],
                            text_summary, original_text,
                            t["timestamp"], now,
                            json.dumps(event_ids), json.dumps(scene_ids),
                            t["owner_id"], t["workspace_id"],
                            t["memory_domain"],
                        ),
                    )
                    conn.execute(
                        "UPDATE turns SET compression_status = 'compressed' WHERE turn_id = ? "
                        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                        (turn_id, t["owner_id"], t["workspace_id"], t["memory_domain"]),
                    )

                # ── Write compressed memories back to SQLite ─────────────
                _write_compressed_memories_to_db(conn, result, now, scope=scope)
                self._write_quality_audit(conn, quality_evidence, "passed")

                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()
        logger.info("Archived %d turns with Tier 2 back-references + compressed memories", len(turns))

    # ── Full cycle ────────────────────────────────────────────────

    def run_cycle(
        self,
        *,
        dry_run: bool = False,
        force_oldest: bool | None = None,
    ) -> BridgeResult:
        """Execute the only Tier 1 → Tier 2 compression transaction."""
        if force_oldest is None:
            force_oldest = self._active_turn_count() >= self.max_turns
        batch = self.select_candidate_turns(force_oldest=force_oldest)
        candidates = batch.turns
        metadata = {
            "candidate_count": len(candidates),
            "cutoff": batch.cutoff,
            "force_oldest": batch.force_oldest,
            "low_relevance_fallback": batch.low_relevance_fallback,
            "sample_turn_ids": [item["turn_id"] for item in candidates[:5]],
            "owner_id": batch.owner_id,
            "workspace_id": batch.workspace_id,
            "memory_domain": batch.memory_domain,
        }
        if not candidates:
            return BridgeResult(
                turns_processed=0, events_generated=0, scenes_generated=0,
                arcs_generated=0, epochs_generated=0, profiles_generated=0,
                status="no_candidates", dry_run=dry_run, **metadata,
            )

        if dry_run:
            return BridgeResult(
                turns_processed=0, events_generated=0, scenes_generated=0,
                arcs_generated=0, epochs_generated=0, profiles_generated=0,
                status="dry_run", dry_run=True, **metadata,
            )

        errors: List[str] = []
        try:
            tier2_output = self._build_tier2_output(candidates)
        except Exception as exc:
            logger.exception("Tier 2 bridge failed")
            errors.append(str(exc))
            return BridgeResult(
                turns_processed=0, events_generated=0, scenes_generated=0,
                arcs_generated=0, epochs_generated=0, profiles_generated=0,
                status="failed", errors=errors, **metadata,
            )

        quality_evidence = self._evaluate_quality(candidates, tier2_output)
        if not tier2_output.get("events"):
            logger.warning(
                "Tier 2 bridge cycle produced no events for %d candidate turns; "
                "leaving Tier1 turns uncompressed.",
                len(candidates),
            )
            self._persist_quality_audit(quality_evidence, "rejected")
            return BridgeResult(
                turns_processed=0,
                events_generated=0,
                scenes_generated=0,
                arcs_generated=0,
                epochs_generated=0,
                profiles_generated=0,
                status="no_events_generated",
                quality_evidence=quality_evidence,
                **metadata,
            )

        if not quality_evidence["passed"]:
            logger.warning(
                "Tier 2 compression quality gate rejected %d turns: %s",
                len(candidates),
                ", ".join(quality_evidence["failed_checks"]),
            )
            self._persist_quality_audit(quality_evidence, "rejected")
            self._record_quality_rejection(candidates)
            return BridgeResult(
                turns_processed=0,
                events_generated=len(tier2_output.get("events", [])),
                scenes_generated=len(tier2_output.get("scenes", [])),
                arcs_generated=len(tier2_output.get("arcs", [])),
                epochs_generated=len(tier2_output.get("epochs", [])),
                profiles_generated=len(tier2_output.get("profile_memories", [])),
                status="quality_rejected",
                quality_evidence=quality_evidence,
                **metadata,
            )

        self._filter_invalid_events(
            tier2_output,
            quality_evidence["accepted_event_ids"],
        )
        try:
            self._commit_tier2_output(candidates, tier2_output, quality_evidence)
        except Exception as exc:
            logger.exception("Archive failed")
            errors.append(str(exc))
            self._persist_quality_audit(quality_evidence, "commit_failed")

        events_count = len(tier2_output.get("events", []))
        scenes_count = len(tier2_output.get("scenes", []))
        arcs_count = len(tier2_output.get("arcs", []))
        epochs_count = len(tier2_output.get("epochs", []))
        profiles_count = len(tier2_output.get("profile_memories", []))
        status = "failed" if errors else "compressed"
        turns_processed = 0 if errors else len(candidates)

        logger.info(
            "Tier 2 bridge cycle: %d turns → %dE/%dS/%dA/%dEp/%dP (%d errors)",
            turns_processed, events_count, scenes_count, arcs_count,
            epochs_count, profiles_count, len(errors),
        )

        return BridgeResult(
            turns_processed=turns_processed,
            events_generated=events_count,
            scenes_generated=scenes_count,
            arcs_generated=arcs_count,
            epochs_generated=epochs_count,
            profiles_generated=profiles_count,
            status=status,
            errors=errors,
            quality_evidence=quality_evidence,
            **metadata,
        )

    # ── Stats ─────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return Tier 1 storage statistics."""
        conn = open_memory_sqlite(self.db_path)
        total_turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        active_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compression_status != 'compressed'"
        ).fetchone()[0]
        archived = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        oldest = conn.execute(
            "SELECT MIN(timestamp) FROM turns WHERE compression_status != 'compressed'"
        ).fetchone()[0]
        conn.close()
        return {
            "total_turns": total_turns,
            "active_turns": active_turns,
            "compressed_turns": total_turns - active_turns,
            "archived_turns": archived,
            "total_sessions": sessions,
            "oldest_active_turn": oldest,
            "retention_days": self.retention_days,
            "candidates_waiting": self.count_candidates(),
        }


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from VoidCube_core.runtime_paths import get_runtime_layout

    parser = argparse.ArgumentParser(description="Tier 1 → Tier 2 Bridge")
    parser.add_argument("--db-path", default=str(get_runtime_layout().memory_db))
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    bridge = Tier1ToTier2Bridge(
        db_path=args.db_path,
        retention_days=args.retention_days,
        batch_size=args.batch_size,
    )

    if args.stats:
        import pprint
        pprint.pprint(bridge.stats())
    else:
        result = bridge.run_cycle(dry_run=args.dry_run)
        import pprint
        pprint.pprint(result.to_dict())
