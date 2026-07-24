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
import re
import sqlite3
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from systems.memory.scope import DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID

logger = logging.getLogger(__name__)

_LATIN_QUALITY_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]{2,}", re.IGNORECASE)
_CJK_QUALITY_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_IDENTIFIER_RE = re.compile(
    r"(?<![\w])(?:https?://[^\s]+|[a-z][a-z0-9._:/-]*\d[a-z0-9._:/-]*|"
    r"\d+(?:\.\d+)+(?:[a-z0-9._-]*)?|\d{2,})(?![\w])",
    re.IGNORECASE,
)
_QUALITY_STOP_WORDS = {
    "about", "after", "also", "and", "are", "been", "before", "being",
    "from", "into", "that", "the", "then", "this", "was", "were", "with",
}
_NEGATION_MARKERS = (
    "must not", "do not", "does not", "did not", "should not", "cannot",
    "can't", "never", "forbid", "forbidden", "prohibit", "prohibited", "avoid",
    "不得", "不要", "不能", "不允许", "禁止", "严禁", "从未", "没有", "未能",
)


def _quality_tokens(value: object) -> set[str]:
    text = str(value or "").lower()
    tokens = {
        token for token in _LATIN_QUALITY_TOKEN_RE.findall(text)
        if token not in _QUALITY_STOP_WORDS
    }
    for run in _CJK_QUALITY_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.add(run)
            continue
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _source_support(summary: str, source_text: str) -> float:
    summary_tokens = _quality_tokens(summary)
    if not summary_tokens:
        return 0.0
    source_tokens = _quality_tokens(source_text)
    return len(summary_tokens & source_tokens) / len(summary_tokens)


def _identifiers(value: object) -> set[str]:
    return {
        match.rstrip(".,;:!?)]}").lower()
        for match in _IDENTIFIER_RE.findall(str(value or ""))
    }


def _has_explicit_negation(value: object) -> bool:
    normalized = " ".join(str(value or "").lower().split())
    return any(marker in normalized for marker in _NEGATION_MARKERS)


def open_memory_sqlite(db_path: str | Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as exc:
        logger.debug("SQLite WAL pragma was not applied for %s: %s", db_path, exc)
    return conn


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
) -> str:
    payload = {
        "memory_type": memory_type,
        "title": str(getattr(item, "title", "") or "").strip(),
        "summary": str(getattr(item, "summary", "") or "").strip(),
        "refs": _stable_refs(item, stable_ids),
        "timespan_start": _iso_value(getattr(item, "timespan_start", "")),
        "timespan_end": _iso_value(getattr(item, "timespan_end", "")),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{memory_type}_{digest}"


def _build_stable_cmem_ids(pipeline_result) -> Dict[str, str]:
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
                )
    return stable_ids


def _write_compressed_memories_to_db(conn, pipeline_result, now: str) -> int:
    """Write the complete pipeline result into the canonical scoped tables.

    Lifecycle: Event(L0,w=1.0) → Scene(L1,w=0.7) → Arc(L2,w=0.4) → Epoch(L3,w=0.2).
    """
    written = 0
    stable_ids = _build_stable_cmem_ids(pipeline_result)
    owner_id, workspace_id = _pipeline_scope(conn, pipeline_result)
    for event in pipeline_result.events:
        parent_id = stable_ids.get(event.parent_ids[0], event.parent_ids[0]) if event.parent_ids else None
        ek = event.event_kind.value if hasattr(event.event_kind, 'value') else str(event.event_kind)
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(event.id, event.id), "event", event.title, event.summary,
                event.timespan_start.isoformat(), event.timespan_end.isoformat(),
                event.importance, event.confidence,
                json.dumps(event.topics), json.dumps(event.entities),
                json.dumps(event.source_turns), parent_id, now,
                0, "active", 1.0, ek, owner_id, workspace_id,
            ),
        )
        written += 1
    for scene in pipeline_result.scenes:
        parent_id = stable_ids.get(scene.parent_ids[0], scene.parent_ids[0]) if scene.parent_ids else None
        child_ids = set(getattr(scene, "child_ids", []) or [])
        child_kinds = [
            event.event_kind.value if hasattr(event.event_kind, "value") else str(event.event_kind)
            for event in pipeline_result.events
            if event.id in child_ids
        ]
        scene_kind = max(set(child_kinds), key=child_kinds.count) if child_kinds else None
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(scene.id, scene.id), "scene", scene.title, scene.summary,
                scene.timespan_start.isoformat(), scene.timespan_end.isoformat(),
                scene.importance, scene.confidence,
                json.dumps(scene.topics), json.dumps(scene.entities),
                json.dumps(scene.evidence_refs), parent_id, now,
                1, "active", 0.7, scene_kind, owner_id, workspace_id,
            ),
        )
        written += 1
    for arc in pipeline_result.arcs:
        parent_id = stable_ids.get(arc.parent_ids[0], arc.parent_ids[0]) if arc.parent_ids else None
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(arc.id, arc.id), "arc", arc.title, arc.summary,
                arc.timespan_start.isoformat(), arc.timespan_end.isoformat(),
                arc.importance, arc.confidence,
                json.dumps(arc.topics), json.dumps(arc.entities),
                json.dumps(arc.evidence_refs), parent_id, now,
                2, "active", 0.4, None, owner_id, workspace_id,
            ),
        )
        written += 1
    for epoch in pipeline_result.epochs:
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind, "
            "owner_id, workspace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(epoch.id, epoch.id), "epoch", epoch.title, epoch.summary,
                epoch.timespan_start.isoformat(), epoch.timespan_end.isoformat(),
                epoch.importance, epoch.confidence,
                json.dumps(epoch.topics), json.dumps(epoch.entities),
                json.dumps(epoch.evidence_refs), None, now,
                3, "active", 0.2, None, owner_id, workspace_id,
            ),
        )
        written += 1
    for profile in getattr(pipeline_result, "profile_memories", []) or []:
        written += _upsert_profile_memory(
            conn,
            profile,
            owner_id=owner_id,
            workspace_id=workspace_id,
            now=now,
        )
    return written


def _pipeline_scope(conn, pipeline_result) -> tuple[str, str]:
    source_turn_ids: list[str] = []
    for collection_name in ("events", "profile_memories"):
        for item in getattr(pipeline_result, collection_name, []) or []:
            source_turn_ids.extend(
                str(value)
                for value in getattr(item, "source_turns", []) or []
                if str(value)
            )
    for turn_id in dict.fromkeys(source_turn_ids):
        row = conn.execute(
            "SELECT owner_id, workspace_id FROM turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        if row:
            return str(row[0]), str(row[1])
    return DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _upsert_profile_memory(
    conn,
    profile,
    *,
    owner_id: str,
    workspace_id: str,
    now: str,
) -> int:
    existing = conn.execute(
        "SELECT memory_id, value FROM profile_memories "
        "WHERE owner_id = ? AND workspace_id = ? AND subject = ? AND predicate = ? "
        "AND status = 'active' ORDER BY valid_from DESC LIMIT 1",
        (owner_id, workspace_id, profile.subject, profile.predicate),
    ).fetchone()
    supersedes = list(getattr(profile, "supersedes", []) or [])
    if existing and str(existing[1]) != str(profile.value):
        conn.execute(
            "UPDATE profile_memories SET status = 'superseded', valid_to = ?, "
            "updated_at = ? WHERE memory_id = ?",
            (profile.valid_from.isoformat(), now, existing[0]),
        )
        supersedes.append(str(existing[0]))
    elif existing:
        conn.execute(
            "UPDATE profile_memories SET summary = ?, confidence = ?, evidence_refs = ?, "
            "source_turns = ?, updated_at = ? WHERE memory_id = ?",
            (
                profile.summary,
                profile.confidence,
                json.dumps(list(profile.evidence_refs), ensure_ascii=False),
                json.dumps(list(profile.source_turns), ensure_ascii=False),
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
            profile.valid_from.isoformat(),
            profile.valid_to.isoformat() if profile.valid_to else None,
            json.dumps(list(profile.evidence_refs), ensure_ascii=False),
            json.dumps(list(profile.source_turns), ensure_ascii=False),
            json.dumps(list(dict.fromkeys(supersedes)), ensure_ascii=False),
            json.dumps(list(profile.conflict_refs), ensure_ascii=False),
            profile.created_at.isoformat(),
            now,
            owner_id,
            workspace_id,
        ),
    )
    return 1


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
        batch_size: int = 100,
        min_relevance: float = 0.1,
        archive_keep_original: bool = True,
        max_turns: int = 10000,
        pipeline_factory: Callable[[], Any] | None = None,
        compression_degraded: bool | None = None,
        min_event_coverage: float = 0.8,
        min_backlink_completeness: float = 1.0,
        max_compression_ratio: float = 1.0,
        max_degraded_fraction: float = 0.0,
        min_source_support: float = 0.35,
        min_identifier_fidelity: float = 1.0,
        min_polarity_consistency: float = 1.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.min_relevance = min_relevance
        self.archive_keep_original = archive_keep_original
        self.max_turns = max_turns
        self.pipeline_factory = pipeline_factory
        self.compression_degraded = compression_degraded
        self.quality_thresholds = {
            "min_event_coverage": min_event_coverage,
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
        time_clause = "" if force_oldest else "timestamp < ? AND "
        params: tuple[Any, ...] = (
            (self.min_relevance, self.batch_size)
            if force_oldest
            else (cutoff, self.min_relevance, self.batch_size)
        )
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "owner_id, workspace_id "
            f"FROM turns WHERE {time_clause}compressed_to_tier2 = 0 "
            "AND relevance_score >= ? ORDER BY timestamp ASC LIMIT ?",
            params,
        ).fetchall()
        low_relevance_fallback = False
        if not rows:
            fallback_params: tuple[Any, ...] = (
                (self.batch_size,)
                if force_oldest
                else (cutoff, self.batch_size)
            )
            rows = conn.execute(
                "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
                "owner_id, workspace_id "
                f"FROM turns WHERE {time_clause}compressed_to_tier2 = 0 "
                "ORDER BY timestamp ASC LIMIT ?",
                fallback_params,
            ).fetchall()
            low_relevance_fallback = bool(rows)
        owner_id = str(rows[0][6]) if rows else DEFAULT_OWNER_ID
        workspace_id = str(rows[0][7]) if rows else DEFAULT_WORKSPACE_ID
        rows = [
            row
            for row in rows
            if str(row[6]) == owner_id and str(row[7]) == workspace_id
        ]
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
        )

    def find_candidate_turns(self) -> List[Dict[str, Any]]:
        """Find age-eligible turns, or oldest turns after volume overflow."""
        force_oldest = self._active_turn_count() >= self.max_turns
        return self.select_candidate_turns(force_oldest=force_oldest).turns

    def _active_turn_count(self) -> int:
        conn = open_memory_sqlite(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        conn.close()
        return int(count)

    def count_candidates(self) -> int:
        """Count how many turns are eligible for compression."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        conn = open_memory_sqlite(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0",
            (cutoff,),
        ).fetchone()[0]
        if count == 0:
            total = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
            ).fetchone()[0]
            if total >= self.max_turns:
                count = total
        conn.close()
        return count

    def needs_compression(self) -> bool:
        """Check if compression is needed (by age or by volume)."""
        if self.count_candidates() > 0:
            return True
        conn = open_memory_sqlite(self.db_path)
        total = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        conn.close()
        return total >= self.max_turns

    # ── Bridge to Tier 2 ──────────────────────────────────────────

    def _build_pipeline(self):
        """Build ChroniclePipeline — LLM-first with heuristic fallback.

        LLM credentials are resolved by
        ``memai.model_config.resolve_mem_llm_client`` — the same source
        the supervisor's endogenous drive and the rest of
        ``MemoryService`` use, so the model selection is consistent
        across all Mem LLM callers and is controlled entirely by the
        CLI ``/api`` command's writes to ``memory.llm.*``.
        """
        if self.pipeline_factory is not None:
            return self.pipeline_factory()

        from memai.pipeline import ChroniclePipeline

        try:
            from memai.model_config import resolve_mem_llm_client
            llm_client, _ = resolve_mem_llm_client(role="default")
        except Exception:
            llm_client = None

        if llm_client is None:
            return ChroniclePipeline()

        try:
            from memai.extraction import EventExtractor, LLMEventExtractionBackend
            from memai.scholar import LLMScholarBackend

            class _LLMExtractionAdapter:
                def __init__(self, llm):
                    self._llm = llm
                def extract_events(self, turns):
                    turn_texts = [f"[{t.turn_id}] {t.speaker}: {t.text}" for t in turns]
                    prompt = (
                        "Extract memory-worthy events from the conversation. "
                        "Output JSON array with: title, summary, event_kind, "
                        "importance, confidence, topics, entities, source_turns.\n\n"
                        + "\n".join(turn_texts)
                    )
                    result = self._llm.complete_json(
                        system_prompt="You are a precise memory extraction assistant.",
                        user_payload={"conversation": prompt},
                        task="extractor.events",
                    )
                    if isinstance(result, list):
                        return result
                    if isinstance(result, dict):
                        return result.get("events") or result.get("result") or []
                    return []

            extraction_backend = LLMEventExtractionBackend(client=_LLMExtractionAdapter(llm_client))  # type: ignore[arg-type]
            scholar_backend = LLMScholarBackend(client=llm_client)
            return ChroniclePipeline(
                event_extractor=EventExtractor(backend=extraction_backend),
                scholar_backend=scholar_backend,
            )
        except Exception:
            return ChroniclePipeline()

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
        compression_degraded = self.compression_degraded
        if compression_degraded is None:
            backend = getattr(getattr(pipeline, "event_extractor", None), "backend", None)
            compression_degraded = getattr(backend, "name", None) == "heuristic"

        return {
            "events": [e.to_dict() for e in result.events],
            "scenes": [s.to_dict() for s in result.scenes],
            "arcs": [a.to_dict() for a in result.arcs],
            "epochs": [ep.to_dict() for ep in result.epochs],
            "profile_memories": [p.to_dict() for p in result.profile_memories],
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
        source_support = min(source_support_scores, default=0.0)
        identifier_fidelity = min(identifier_fidelity_scores, default=0.0)
        polarity_consistency = min(polarity_consistency_scores, default=0.0)
        source_supported_event_count = sum(
            score >= self.quality_thresholds["min_source_support"]
            for score in source_support_scores
        )

        failed_checks: list[str] = []
        if event_coverage < self.quality_thresholds["min_event_coverage"]:
            failed_checks.append("event_coverage")
        if backlink_completeness < self.quality_thresholds["min_backlink_completeness"]:
            failed_checks.append("backlink_completeness")
        if compression_ratio > self.quality_thresholds["max_compression_ratio"]:
            failed_checks.append("compression_ratio")
        if degraded_fraction > self.quality_thresholds["max_degraded_fraction"]:
            failed_checks.append("degraded_fraction")
        if source_support < self.quality_thresholds["min_source_support"]:
            failed_checks.append("source_support")
        if identifier_fidelity < self.quality_thresholds["min_identifier_fidelity"]:
            failed_checks.append("identifier_fidelity")
        if polarity_consistency < self.quality_thresholds["min_polarity_consistency"]:
            failed_checks.append("polarity_consistency")

        evaluated_at = datetime.now(timezone.utc).isoformat()
        audit_payload = {
            "evaluated_at": evaluated_at,
            "candidate_count": candidate_count,
            "event_count": event_count,
            "covered_turn_count": len(covered_turn_ids),
            "event_coverage": round(event_coverage, 6),
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
        }
        audit_seed = json.dumps(audit_payload, ensure_ascii=False, sort_keys=True)
        audit_payload["audit_id"] = "cqa_" + hashlib.sha1(
            audit_seed.encode("utf-8")
        ).hexdigest()[:20]
        audit_payload["passed"] = not failed_checks
        return audit_payload

    @staticmethod
    def _write_quality_audit(
        conn: sqlite3.Connection,
        quality_evidence: Dict[str, Any],
        status: str,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO compression_quality_audit "
            "(audit_id, evaluated_at, status, candidate_count, event_count, "
            "covered_turn_count, event_coverage, backlinked_event_count, "
            "backlink_completeness, source_chars, event_summary_chars, "
            "compression_ratio, degraded_event_count, degraded_fraction, "
            "source_supported_event_count, source_support, identifier_fidelity, "
            "polarity_consistency, unsupported_identifiers, thresholds, failed_checks, "
            "sample_turn_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quality_evidence["audit_id"],
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
        stable_ids = _build_stable_cmem_ids(result)
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
                        "timestamp, compressed_at, event_ids, scene_ids, owner_id, workspace_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            turn_id, t["session_id"], t["speaker"],
                            text_summary, original_text,
                            t["timestamp"], now,
                            json.dumps(event_ids), json.dumps(scene_ids),
                            t["owner_id"], t["workspace_id"],
                        ),
                    )
                    conn.execute(
                        "UPDATE turns SET compressed_to_tier2 = 1 WHERE turn_id = ?",
                        (turn_id,),
                    )

                # ── Write compressed memories back to SQLite ─────────────
                _write_compressed_memories_to_db(conn, result, now)
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
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        archived = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        oldest = conn.execute(
            "SELECT MIN(timestamp) FROM turns WHERE compressed_to_tier2 = 0"
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
    parser.add_argument("--batch-size", type=int, default=100)
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
