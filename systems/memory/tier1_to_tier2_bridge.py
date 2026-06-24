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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


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
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score "
            "FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0 "
            "AND relevance_score >= ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (cutoff, self.min_relevance, self.batch_size),
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
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0",
            (cutoff,),
        ).fetchone()[0]
        conn.close()
        return count

    def needs_compression(self) -> bool:
        """Check if compression is needed (by age or by volume)."""
        if self.count_candidates() > 0:
            return True
        conn = sqlite3.connect(str(self.db_path))
        total = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        conn.close()
        return total >= self.max_turns

    # ── Bridge to Tier 2 ──────────────────────────────────────────

    def bridge_to_tier2(self, turns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert turns to TranscriptTurn and feed into ChroniclePipeline."""
        from memai.pipeline import ChroniclePipeline
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

        pipeline = ChroniclePipeline()
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

        # Build turn_id → event_ids / scene_ids mappings
        turn_to_events: Dict[str, List[str]] = {}
        for event in result.events:
            for src_turn_id in event.source_turns:
                turn_to_events.setdefault(src_turn_id, []).append(event.id)

        turn_to_scenes: Dict[str, List[str]] = {}
        for scene in result.scenes:
            for ev_id in scene.child_ids:
                for turn_id, ev_ids in turn_to_events.items():
                    if ev_id in ev_ids:
                        turn_to_scenes.setdefault(turn_id, []).append(scene.id)

        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
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
        conn.commit()
        conn.close()
        logger.info("Archived %d turns with Tier 2 back-references", len(turns))

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
        conn = sqlite3.connect(str(self.db_path))
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
