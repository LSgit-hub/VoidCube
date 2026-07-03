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
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


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
    """Write Event/Scene/Arc/Epoch from PipelineResult into compressed_memories table.

    Lifecycle: Event(L0,w=1.0) → Scene(L1,w=0.7) → Arc(L2,w=0.4) → Epoch(L3,w=0.2).
    """
    written = 0
    stable_ids = _build_stable_cmem_ids(pipeline_result)
    for event in pipeline_result.events:
        parent_id = stable_ids.get(event.parent_ids[0], event.parent_ids[0]) if event.parent_ids else None
        ek = event.event_kind.value if hasattr(event.event_kind, 'value') else str(event.event_kind)
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(event.id, event.id), "event", event.title, event.summary,
                event.timespan_start.isoformat(), event.timespan_end.isoformat(),
                event.importance, event.confidence,
                json.dumps(event.topics), json.dumps(event.entities),
                json.dumps(event.source_turns), parent_id, now,
                0, "active", 1.0, ek,
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
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(scene.id, scene.id), "scene", scene.title, scene.summary,
                scene.timespan_start.isoformat(), scene.timespan_end.isoformat(),
                scene.importance, scene.confidence,
                json.dumps(scene.topics), json.dumps(scene.entities),
                json.dumps(scene.evidence_refs), parent_id, now,
                1, "active", 0.7, scene_kind,
            ),
        )
        written += 1
    for arc in pipeline_result.arcs:
        parent_id = stable_ids.get(arc.parent_ids[0], arc.parent_ids[0]) if arc.parent_ids else None
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(arc.id, arc.id), "arc", arc.title, arc.summary,
                arc.timespan_start.isoformat(), arc.timespan_end.isoformat(),
                arc.importance, arc.confidence,
                json.dumps(arc.topics), json.dumps(arc.entities),
                json.dumps(arc.evidence_refs), parent_id, now,
                2, "active", 0.4, None,
            ),
        )
        written += 1
    for epoch in pipeline_result.epochs:
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_ids.get(epoch.id, epoch.id), "epoch", epoch.title, epoch.summary,
                epoch.timespan_start.isoformat(), epoch.timespan_end.isoformat(),
                epoch.importance, epoch.confidence,
                json.dumps(epoch.topics), json.dumps(epoch.entities),
                json.dumps(epoch.evidence_refs), None, now,
                3, "active", 0.2, None,
            ),
        )
        written += 1
    return written


@dataclass
class BridgeResult:
    """Result of a Tier 1 → Tier 2 compression cycle."""

    turns_processed: int
    events_generated: int
    scenes_generated: int
    arcs_generated: int
    epochs_generated: int
    profiles_generated: int
    dry_run: bool = False
    candidate_count: int = 0
    errors: List[str] = None

    def __post_init__(self):
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
            "dry_run": self.dry_run,
            "candidate_count": self.candidate_count,
            "errors": self.errors,
        }


class Tier1ToTier2Bridge:
    """Bridge Tier 1 SQLite turns into Tier 2 Mem Pipeline structured memory.

    Usage::

        bridge = Tier1ToTier2Bridge(db_path="./memory.db")
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
    ) -> None:
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.min_relevance = min_relevance
        self.archive_keep_original = archive_keep_original
        self.max_turns = max_turns

    # ── Query candidates ──────────────────────────────────────────

    def find_candidate_turns(self) -> List[Dict[str, Any]]:
        """Find turns older than retention window, not yet compressed."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        conn = open_memory_sqlite(self.db_path)
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score "
            "FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0 "
            "AND relevance_score >= ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (cutoff, self.min_relevance, self.batch_size),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score "
                "FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0 "
                "ORDER BY timestamp ASC LIMIT ?",
                (cutoff, self.batch_size),
            ).fetchall()
        if not rows:
            total = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
            ).fetchone()[0]
            if total >= self.max_turns:
                rows = conn.execute(
                    "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score "
                    "FROM turns WHERE compressed_to_tier2 = 0 "
                    "AND relevance_score >= ? "
                    "ORDER BY timestamp ASC LIMIT ?",
                    (self.min_relevance, self.batch_size),
                ).fetchall()
                if not rows:
                    rows = conn.execute(
                        "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score "
                        "FROM turns WHERE compressed_to_tier2 = 0 "
                        "ORDER BY timestamp ASC LIMIT ?",
                        (self.batch_size,),
                    ).fetchall()
        conn.close()
        return [
            {
                "turn_id": r[0],
                "session_id": r[1],
                "speaker": r[2],
                "text": r[3],
                "timestamp": r[4],
                "relevance_score": r[5],
            }
            for r in rows
        ]

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

    def bridge_to_tier2(self, turns: List[Dict[str, Any]]) -> Dict[str, Any]:
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

        return {
            "events": [e.to_dict() for e in result.events],
            "scenes": [s.to_dict() for s in result.scenes],
            "arcs": [a.to_dict() for a in result.arcs],
            "epochs": [ep.to_dict() for ep in result.epochs],
            "profile_memories": [p.to_dict() for p in result.profile_memories],
            "_pipeline_result": result,
        }

    # ── Archive processed turns ───────────────────────────────────

    def archive_turns(
        self, turns: List[Dict[str, Any]], tier2_output: Dict[str, Any]
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
                        "timestamp, compressed_at, event_ids, scene_ids) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            turn_id, t["session_id"], t["speaker"],
                            text_summary, original_text,
                            t["timestamp"], now,
                            json.dumps(event_ids), json.dumps(scene_ids),
                        ),
                    )
                    conn.execute(
                        "UPDATE turns SET compressed_to_tier2 = 1 WHERE turn_id = ?",
                        (turn_id,),
                    )

                # ── Write compressed memories back to SQLite ─────────────
                _write_compressed_memories_to_db(conn, result, now)

                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()
        logger.info("Archived %d turns with Tier 2 back-references + compressed memories", len(turns))

    # ── Full cycle ────────────────────────────────────────────────

    def run_cycle(self, *, dry_run: bool = False) -> BridgeResult:
        """Execute one full Tier 1 → Tier 2 compression cycle."""
        candidates = self.find_candidate_turns()
        if not candidates:
            return BridgeResult(
                turns_processed=0, events_generated=0, scenes_generated=0,
                arcs_generated=0, epochs_generated=0, profiles_generated=0,
                dry_run=dry_run, candidate_count=0,
            )

        if dry_run:
            return BridgeResult(
                turns_processed=0, events_generated=0, scenes_generated=0,
                arcs_generated=0, epochs_generated=0, profiles_generated=0,
                dry_run=True, candidate_count=len(candidates),
            )

        errors: List[str] = []
        try:
            tier2_output = self.bridge_to_tier2(candidates)
        except Exception as exc:
            logger.exception("Tier 2 bridge failed")
            errors.append(str(exc))
            return BridgeResult(
                turns_processed=0, events_generated=0, scenes_generated=0,
                arcs_generated=0, epochs_generated=0, profiles_generated=0,
                errors=errors,
            )

        if not tier2_output.get("events"):
            logger.warning(
                "Tier 2 bridge cycle produced no events for %d candidate turns; "
                "leaving Tier1 turns uncompressed.",
                len(candidates),
            )
            return BridgeResult(
                turns_processed=0,
                events_generated=0,
                scenes_generated=0,
                arcs_generated=0,
                epochs_generated=0,
                profiles_generated=0,
                candidate_count=len(candidates),
            )

        try:
            self.archive_turns(candidates, tier2_output)
        except Exception as exc:
            logger.exception("Archive failed")
            errors.append(str(exc))

        events_count = len(tier2_output.get("events", []))
        scenes_count = len(tier2_output.get("scenes", []))
        arcs_count = len(tier2_output.get("arcs", []))
        epochs_count = len(tier2_output.get("epochs", []))
        profiles_count = len(tier2_output.get("profile_memories", []))

        logger.info(
            "Tier 2 bridge cycle: %d turns → %dE/%dS/%dA/%dEp/%dP (%d errors)",
            len(candidates), events_count, scenes_count, arcs_count,
            epochs_count, profiles_count, len(errors),
        )

        return BridgeResult(
            turns_processed=len(candidates),
            events_generated=events_count,
            scenes_generated=scenes_count,
            arcs_generated=arcs_count,
            epochs_generated=epochs_count,
            profiles_generated=profiles_count,
            errors=errors,
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

    parser = argparse.ArgumentParser(description="Tier 1 → Tier 2 Bridge")
    parser.add_argument("--db-path", default="./memory.db")
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
