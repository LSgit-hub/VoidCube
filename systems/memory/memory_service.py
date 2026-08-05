import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from agent.redact import redact_sensitive_text
from systems.memory.backup import MemoryBackupManager, MemoryRestoreError
from systems.memory.config import MemoryServiceConfig
from systems.memory.domain import (
    DEFAULT_MEMORY_ACTOR,
    DEFAULT_MEMORY_DOMAIN,
    MemoryActor,
    MemoryDomain,
    MemoryDomainAccessError,
    authorize_read,
    authorize_write,
    domain_values,
)
from systems.memory.profile_capture import (
    ALL_PROFILE_PREDICATES,
    capture_explicit_user_profile,
)
from systems.memory.profile_store import (
    revoke_profile_predicates,
    upsert_profile_memory,
)
from systems.memory.ranking_policy import compute_dynamic_weight
from systems.memory.promotion import (
    MemoryPromotionAccessError,
    MemoryPromotionCandidateCreate,
    MemoryPromotionConflictError,
    MemoryPromotionConsent,
    MemoryPromotionNotFoundError,
    MemoryPromotionRevoke,
    MemoryPromotionValidationError,
    authorize_promotion_manager,
    consent_memory_promotion_candidate,
    create_memory_promotion_candidate,
    list_memory_promotion_candidates,
    list_memory_promotions,
    promotion_source_key,
    reject_promotion_candidates_for_source,
    revoke_memory_promotion,
    revoke_promotions_for_source,
)
from systems.memory.recall import (
    build_recall_plan,
    format_recall_context,
    merge_recall_results,
    recall_memories,
)
from systems.memory.database import MemoryDatabaseBootstrap, open_memory_sqlite
from systems.memory.http_adapter import build_memory_http_app
from systems.memory.maintenance import run_tier1_decay_cycle, run_tier2_bridge_cycle
from systems.memory.scope import (
    DEFAULT_OWNER_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_SCOPE_ID,
    MemoryScope,
)
from systems.memory.semantic_index import SemanticMemoryIndex
from systems.memory.tier1_to_tier2_bridge import (
    Tier1ToTier2Bridge,
    _identifiers,
    _source_support,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory_service")

_CMEM_COLUMNS = (
    "memory_id, memory_type, title, summary, timespan_start, timespan_end, "
    "importance, confidence, topics, entities, source_turns, parent_id, "
    "compressed_at, compression_level, status, superseded_by, weight, event_kind, "
    "access_count, last_accessed_at, citation_count, pinned, hidden, identity_layer, "
    "evidence_refs, origin_type, origin_id, verified_at, owner_id, workspace_id, "
    "memory_domain, created_at"
)


def _redact_for_memory_storage(value: Any) -> Any:
    """Recursively redact secrets before any value enters durable memory."""
    if isinstance(value, str):
        return redact_sensitive_text(value, force=True)
    if isinstance(value, dict):
        return {
            str(key): _redact_for_memory_storage(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_for_memory_storage(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_for_memory_storage(item) for item in value)
    return value


def _authorized_write_domain(
    actor: MemoryActor | str,
    domain: MemoryDomain | str,
) -> str:
    try:
        return authorize_write(actor, domain).value
    except (MemoryDomainAccessError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _authorized_read_domains(
    actor: MemoryActor | str,
    requested: List[MemoryDomain] | tuple[MemoryDomain, ...] | None,
) -> tuple[str, ...]:
    try:
        return domain_values(authorize_read(actor, requested))
    except (MemoryDomainAccessError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# ── Tier 1 Models (Short-term conversation store) ──────────────────

class SessionCreate(BaseModel):
    session_id: str = ""  # Optional: caller-provided ID; auto-generated if empty
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TurnCreate(BaseModel):
    speaker: str  # "user" | "agent" | "system"
    text: str
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TurnPairCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=300)
    user_content: str = Field(min_length=1)
    assistant_content: str = ""
    write_id: str = Field(min_length=1, max_length=300)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    session_id: str
    created_at: str
    updated_at: Optional[str] = None
    turn_count: int = 0
    metadata: Dict[str, Any] = {}


class TurnResponse(BaseModel):
    turn_id: str
    session_id: str
    speaker: str
    text: str
    timestamp: str
    relevance_score: float = 1.0
    decay_factor: float = 0.01
    compressed_to_tier2: bool = False
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


class TimelineQuery(BaseModel):
    date: str  # ISO date e.g. "2026-06-24"
    session_id: Optional[str] = None
    speaker: Optional[str] = None
    limit: int = 100
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    source_domains: List[MemoryDomain] = Field(default_factory=list)


class Tier2CompressRequest(BaseModel):
    retention_days: int = 30
    batch_size: int = 100
    min_relevance: float = 0.1
    dry_run: bool = False
    force_oldest: bool = False
    memory_actor: MemoryActor = MemoryActor.MEMORY_MAINTENANCE
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID


class RecallRequest(BaseModel):
    query: str
    memory_type: str | List[str] | None = None
    topic: Optional[str] = None
    timespan_start: Optional[str] = None
    timespan_end: Optional[str] = None
    as_of: Optional[str] = None  # bi-temporal transaction-time snapshot
    limit: Optional[int] = Field(default=None, ge=1, le=50)
    max_context_chars: Optional[int] = Field(default=None, ge=256, le=20000)
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    current_session_id: Optional[str] = None
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    source_domains: List[MemoryDomain] = Field(default_factory=list)
    include_tier1: bool = True
    include_tier2: bool = True
    include_promotions: bool = True
    request_source: str = Field(
        default="api",
        pattern=r"^(api|auto_prefetch|tool)$",
    )


class IdentityRevisionProposal(BaseModel):
    target_memory_id: str
    baseline_version: str
    reason: str = Field(min_length=8, max_length=2000)
    proposed_changes: Dict[str, Any]
    evidence: List[str] = Field(min_length=1, max_length=20)
    source_actor: str = Field(default="user", min_length=1, max_length=100)


class IdentityRevisionDecision(BaseModel):
    decision: str
    reasoning_summary: str = Field(min_length=8, max_length=2000)
    decided_by: str = Field(default="supervisor", min_length=1, max_length=100)


class IdentityExperienceVerification(BaseModel):
    """Explicit human verification of a conversation turn as identity history."""

    turn_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_refs: List[str] = Field(min_length=1, max_length=50)
    verified_by: str = Field(default="anchor", min_length=1, max_length=100)
    topics: List[str] = Field(default_factory=list, max_length=20)
    entities: List[str] = Field(default_factory=list, max_length=20)
    event_kind: str = Field(default="decision", min_length=1, max_length=50)
    importance: float = Field(default=0.9, ge=0.0, le=1.0)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN


class InteractionExperienceSettlement(BaseModel):
    user_turn_id: str = Field(min_length=1, max_length=200)
    agent_turn_id: Optional[str] = Field(default=None, max_length=200)
    verified_by: str = Field(
        default="user_explicit_signal",
        min_length=1,
        max_length=100,
    )
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN


class DurableMemoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=4000)
    topics: List[str] = Field(default_factory=list, max_length=30)
    entities: List[str] = Field(default_factory=list, max_length=30)
    evidence_refs: List[str] = Field(default_factory=list, max_length=50)
    event_kind: str = Field(default="decision", min_length=1, max_length=50)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)
    source_actor: str = Field(default="agent", min_length=1, max_length=100)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN


class RecallFeedbackCreate(BaseModel):
    trace_id: str = Field(min_length=1, max_length=200)
    memory_id: str = Field(min_length=1, max_length=300)
    verdict: str = Field(pattern=r"^(relevant|irrelevant|outdated|incorrect)$")
    reason: str = Field(default="", max_length=2000)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR


class ForgetRequest(BaseModel):
    memory_id: Optional[str] = Field(default=None, max_length=300)
    session_id: Optional[str] = Field(default=None, max_length=300)
    reason: str = Field(min_length=3, max_length=2000)
    confirmation: str = Field(pattern=r"^FORGET$")
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN


# ── Content-aware weight model (five dimensions) ─────────────────
# W_final = clamp(W_base + content_bonus + citation_bonus, 0, 1)
# Then: if pinned → W=1.0; if hidden → W=0.0

# Dimension 1: Content importance — derived from EventKind
def _cmem_row_to_dict(row) -> Dict[str, Any]:
    """Convert a compressed_memories table row to a public record."""
    def value(name: str, default: Any = None) -> Any:
        if isinstance(row, sqlite3.Row):
            return row[name] if name in row.keys() else default
        # Compatibility for callers that construct tuple rows in isolation.
        index = {
            "memory_id": 0, "memory_type": 1, "title": 2,
            "summary": 3, "timespan_start": 4, "timespan_end": 5,
            "importance": 6, "confidence": 7, "topics": 8,
            "entities": 9, "source_turns": 10, "parent_id": 11,
            "compressed_at": 12, "compression_level": 13,
            "status": 14, "superseded_by": 15, "weight": 16,
            "event_kind": 17, "access_count": 18,
            "last_accessed_at": 19, "citation_count": 20,
            "pinned": 21, "hidden": 22, "identity_layer": 23,
            "evidence_refs": 24, "origin_type": 25,
            "origin_id": 26, "verified_at": 27,
            "owner_id": 28, "workspace_id": 29,
            "memory_domain": 30, "created_at": 31,
        }
        position = index.get(name)
        return row[position] if position is not None and len(row) > position else default

    def json_value(name: str, default: Any) -> Any:
        raw = value(name)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    base = {
        "memory_id": value("memory_id"), "memory_type": value("memory_type"),
        "title": value("title"), "summary": value("summary"),
        "timespan_start": value("timespan_start"), "timespan_end": value("timespan_end"),
        "importance": value("importance"), "confidence": value("confidence"),
        "topics": json_value("topics", []), "entities": json_value("entities", []),
        "source_turns": json_value("source_turns", []),
        "parent_id": value("parent_id"), "compressed_at": value("compressed_at"),
        "compression_level": value("compression_level", 0),
        "status": value("status", "active"),
        "superseded_by": value("superseded_by"),
        "weight": value("weight", 1.0),
        # Five-dimensional content-aware fields (cols 17-22)
        "event_kind": value("event_kind"), "access_count": value("access_count", 0),
        "last_accessed_at": value("last_accessed_at"),
        "citation_count": value("citation_count", 0),
        "pinned": bool(value("pinned", 0)), "hidden": bool(value("hidden", 0)),
        "identity_layer": value("identity_layer"),
        "evidence_refs": json_value("evidence_refs", []),
        "origin_type": value("origin_type"), "origin_id": value("origin_id"),
        "verified_at": value("verified_at"),
        "owner_id": value("owner_id", DEFAULT_OWNER_ID),
        "workspace_id": value("workspace_id", DEFAULT_WORKSPACE_ID),
        "memory_domain": value("memory_domain", DEFAULT_MEMORY_DOMAIN.value),
        "created_at": value("created_at"),
    }
    # Compute dynamic weight from all signals
    base["dynamic_weight"] = compute_dynamic_weight(
        base_weight=base["weight"],
        event_kind=base["event_kind"],
        access_count=base["access_count"],
        citation_count=base["citation_count"],
        pinned=base["pinned"],
        hidden=base["hidden"],
    )
    return base


def _turn_row_to_dict(row) -> Dict[str, Any]:
    """Convert a turns table row to a dict (module-level helper)."""
    return {
        "turn_id": row[0],
        "session_id": row[1],
        "speaker": row[2],
        "text": row[3],
        "timestamp": row[4],
        "relevance_score": row[5],
        "decay_factor": row[6],
        "tags": json.loads(row[7]) if row[7] else [],
        "metadata": json.loads(row[8]) if row[8] else {},
        "compressed_to_tier2": bool(row[9]),
        "memory_domain": row[10] if len(row) > 10 else DEFAULT_MEMORY_DOMAIN.value,
    }


class MemoryApplicationService:
    def __init__(self, config: MemoryServiceConfig = None):
        self.config = config or MemoryServiceConfig()
        self._compression_task: asyncio.Task | None = None
        self._gateway_registration_task: asyncio.Task | None = None
        self._semantic_task: asyncio.Task | None = None
        self._semantic_wake = asyncio.Event()
        self._gateway_service_id: Optional[str] = None
        self._gateway_registration_healthy = False
        self._last_gateway_registration_check_at: Optional[str] = None
        self._db_path = Path(self.config.db_path)
        # Rule execution tracking
        self._last_rule_run: Dict[str, str] = {}
        self._rule_run_counts: Dict[str, int] = {}
        # P0-4 健康信号: last cycle that did real write work (not just "ran").
        self._last_effective_activity_at: Optional[str] = None
        # LLM status (re-verified each compression cycle, recovers after outage)
        self._llm_healthy: bool = False
        self._llm_model: str = ""
        self._llm_error: str = ""
        self._last_llm_health_check_at: Optional[str] = None
        self._recall_requests = 0
        self._recall_hits = 0
        self._recall_failures = 0
        self._last_recall_at: Optional[str] = None
        self._last_recall_count = 0
        self._last_recall_latency_ms = 0.0
        self._last_recall_trace_id: Optional[str] = None
        self._last_recall_status: str = "idle"
        self._backup_manager = MemoryBackupManager(
            self._db_path,
            retention_count=self.config.backup_retention_count,
        )
        self._database_bootstrap = MemoryDatabaseBootstrap(
            db_path=self._db_path,
            backup_manager=self._backup_manager,
        )
        self._database_bootstrap.initialize()
        self._semantic_index = SemanticMemoryIndex(self._db_path)

    # ── Compression Lifecycle ─────────────────────────────────────

    # Weight decay by compression level:
    #   Level 0 (Event,  <30d):   weight = 1.00
    #   Level 1 (Scene,  <180d):  weight = 0.70
    #   Level 2 (Arc,    <365d):  weight = 0.40
    #   Level 3 (Epoch,  <730d):  weight = 0.20
    #   Level 4 (Final,  >=730d): weight = 0.05 → purge candidate
    _LEVEL_WEIGHT = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.05}

    _LEVEL_MAX_AGE_DAYS = {
        # (memory_type, compression_level) → max age before escalation
        ("event", 0): 30,     # Event → escalate to Scene after 30d
        ("scene", 1): 180,    # Scene → escalate to Arc after 180d
        ("arc", 2): 365,      # Arc → escalate to Epoch after 365d
        ("epoch", 3): 730,    # Epoch → escalate to Final after 730d
        ("epoch", 4): 90,     # Final → purge candidate after 90d audit window
    }

    async def _apply_compression_lifecycle(self) -> Dict[str, Any]:
        """Cascade compressed memories through compression levels.

        For each memory, if its age exceeds the threshold for its current level,
        it gets superseded by a higher-level summary. Eventually, level-4
        (FinalSummary) entries are purged.
        """
        now = datetime.now()
        conn = open_memory_sqlite(self._db_path)
        escalated = 0
        purged = 0
        quality_rejected = 0

        # ── Escalate: find entries past their level's max age ──
        for (mem_type, level), max_age_days in self._LEVEL_MAX_AGE_DAYS.items():
            cutoff = (now - timedelta(days=max_age_days)).isoformat()
            rows = conn.execute(
                "SELECT memory_id, title, summary, topics, entities, event_kind, "
                "timespan_start, timespan_end, importance, confidence, source_turns, "
                "owner_id, workspace_id, memory_domain "
                "FROM compressed_memories "
                "WHERE memory_type = ? AND compression_level = ? "
                "AND status = 'active' AND pinned = 0 "
                "AND identity_layer IS NULL "
                "AND memory_id NOT LIKE 'identity-founding-%' AND compressed_at < ?",
                (mem_type, level, cutoff),
            ).fetchall()

            for row in rows:
                mem_id, title, summary, topics_json, entities_json, event_kind, \
                    ts_start, ts_end, importance, confidence, source_turns_json, \
                    owner_id, workspace_id, memory_domain = row

                if level >= 4:
                    # Final level: LLM reviews before permanent deletion
                    should_keep = await self._llm_purge_review(
                        mem_id=mem_id, title=title, summary=summary,
                        topics=json.loads(topics_json) if topics_json else [],
                    )
                    if should_keep:
                        conn.execute(
                            "UPDATE compressed_memories SET compression_level = 3, "
                            "status = 'active', weight = 0.15, compressed_at = ? "
                            "WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                            "AND memory_domain = ?",
                            (now.isoformat(), mem_id, owner_id, workspace_id, memory_domain),
                        )
                    else:
                        conn.execute(
                            "UPDATE compressed_memories SET status = 'purged', "
                            "weight = 0.0, compressed_at = ? WHERE memory_id = ? "
                            "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                            (now.isoformat(), mem_id, owner_id, workspace_id, memory_domain),
                        )
                        purged += 1
                    continue

                # Escalate to next level with LLM re-summarization
                next_level = level + 1
                next_type = {0: "scene", 1: "arc", 2: "epoch", 3: "epoch"}[level]
                next_weight = self._LEVEL_WEIGHT.get(next_level, 0.1)

                # ── LLM generates higher-level abstract ──
                escalated_title, escalated_summary = await self._llm_escalate_summary(
                    mem_id=mem_id,
                    title=title,
                    summary=summary,
                    from_type=mem_type,
                    from_level=level,
                    to_type=next_type,
                    to_level=next_level,
                    topics=json.loads(topics_json) if topics_json else [],
                )
                source_text = f"{title} {summary}"
                unsupported_identifiers = _identifiers(escalated_summary) - _identifiers(source_text)
                if _source_support(escalated_summary, source_text) < 0.35 or unsupported_identifiers:
                    quality_rejected += 1
                    logger.warning(
                        "Compression lifecycle rejected escalation for %s: "
                        "source support or identifier fidelity failed",
                        mem_id,
                    )
                    continue

                parent_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "voidcube-memory-lifecycle:"
                        f"{owner_id}:{workspace_id}:{memory_domain}:{mem_id}:{next_level}",
                    )
                )
                source_turns = json.loads(source_turns_json) if source_turns_json else []
                if not source_turns:
                    source_turns = [mem_id]
                conn.execute(
                    "INSERT OR REPLACE INTO compressed_memories "
                    "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                    "importance, confidence, topics, entities, source_turns, "
                    "parent_id, compressed_at, compression_level, status, weight, "
                    "owner_id, workspace_id, memory_domain, event_kind, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        parent_id, next_type, escalated_title, escalated_summary,
                        ts_start, ts_end,
                        importance * 0.85, confidence * 0.9,
                        topics_json, entities_json,
                        json.dumps(source_turns),
                        mem_id, now.isoformat(), next_level, "active", next_weight,
                        owner_id, workspace_id, memory_domain, event_kind, now.isoformat(),
                    ),
                )

                # Mark old entry as superseded
                conn.execute(
                    "UPDATE compressed_memories SET status = 'superseded', "
                    "superseded_by = ?, weight = weight * 0.3 WHERE memory_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (parent_id, mem_id, owner_id, workspace_id, memory_domain),
                )
                # Increment citation_count on the new parent (Dimension 3)
                conn.execute(
                    "UPDATE compressed_memories SET citation_count = citation_count + 1 "
                    "WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ?",
                    (parent_id, owner_id, workspace_id, memory_domain),
                )
                # Record the new parent's entities in the entity graph.
                from systems.memory.entity_graph import update_entity_graph

                parent_entities = []
                if entities_json:
                    try:
                        parent_entities = json.loads(entities_json)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        parent_entities = []
                update_entity_graph(
                    conn,
                    memory_id=parent_id,
                    memory_type=next_type,
                    entities=parent_entities,
                    owner_id=str(owner_id),
                    workspace_id=str(workspace_id),
                    memory_domain=str(memory_domain),
                    now=now.isoformat(),
                )
                escalated += 1

        # Backfill the transaction-time anchor for any row still missing one.
        conn.execute(
            "UPDATE compressed_memories SET created_at = compressed_at "
            "WHERE created_at IS NULL"
        )
        conn.commit()
        conn.close()
        if escalated or purged:
            logger.info(
                "Compression lifecycle: %d escalated, %d purged", escalated, purged
            )
        return {
            "escalated": escalated,
            "purged": purged,
            "quality_rejected": quality_rejected,
        }

    async def _llm_escalate_summary(
        self, *, mem_id: str, title: str, summary: str,
        from_type: str, from_level: int, to_type: str, to_level: int,
        topics: list,
    ) -> tuple[str, str]:
        """Use LLM to produce a higher-level abstract when escalating memory.

        Without LLM: falls back to mechanical prefix (e.g. "[L2] original title").
        With LLM: generates a genuinely more abstract summary appropriate for
        the target level (Scene→Arc: synthesize scene into arc narrative,
        Arc→Epoch: distill arc into epoch-level historical significance).

        LLM credentials are resolved from ``memory.llm.*`` via
        ``_resolve_mem_llm_client`` — the same key the CLI ``/api [3]``
        command writes — so retargeting the Mem model is a single-step
        operation in the CLI rather than scattered env-var management.
        """
        level_names = {0: "事件", 1: "场景", 2: "弧线", 3: "纪元", 4: "终章"}
        from_name = level_names.get(from_level, str(from_level))
        to_name = level_names.get(to_level, str(to_level))
        topics_text = ", ".join(topics[:5]) if topics else "通用"

        # Try LLM via the unified resolver (summarization role; cached).
        try:
            from systems.memory.llm_cache import (
                build_cache_key,
                open_cached,
                store_cached,
            )

            client, model = self._resolve_mem_llm_client(role="summarization")
            if client is not None:
                prompt = (
                    f"将以下{from_name}级别的记忆升级为{to_name}级别的摘要。\n"
                    f"原始标题: {title}\n"
                    f"原始摘要: {summary}\n"
                    f"主题: {topics_text}\n\n"
                    f"{to_name}级别的摘要应该更抽象、更关注长期意义和结构性变化，"
                    f"而不是具体细节。保留核心事实但提升抽象层次。\n"
                    f"用中文输出JSON: {{\"title\": \"...\", \"summary\": \"...\"}}"
                )
                input_text = (
                    f"{mem_id}|{from_level}|{to_level}|{title}|{summary}|{topics_text}"
                )
                cache_key = build_cache_key("escalate", model, input_text)
                cached = None
                try:
                    cached = open_cached(self._db_path, cache_key)
                except Exception:
                    cached = None
                if cached is not None and isinstance(cached, dict):
                    cached_title = str(cached.get("title", "")).strip()
                    cached_summary = str(cached.get("summary", "")).strip()
                    if cached_title and cached_summary:
                        logger.info(
                            "Cached LLM escalated %s: %s→%s (%s→%s)",
                            mem_id, from_name, to_name, title[:40], cached_title[:40],
                        )
                        return cached_title, cached_summary

                result = client.complete_json(
                    system_prompt=(
                        "你是长期记忆的编年史学者。你的任务是将低层记忆升级为更高抽象层次。"
                        "保持历史准确性，但提升视角——从具体事件到模式，从模式到意义。"
                    ),
                    user_payload={"task": prompt},
                    task="scholar.revision",
                )
                if isinstance(result, dict):
                    llm_title = str(result.get("title", "")).strip()
                    llm_summary = str(result.get("summary", "")).strip()
                    if llm_title and llm_summary:
                        try:
                            store_cached(
                                self._db_path,
                                cache_key=cache_key,
                                task="escalate",
                                model=model,
                                input_text=input_text,
                                result=result,
                            )
                        except Exception:
                            pass
                        logger.info(
                            "LLM escalated %s: %s→%s (%s→%s)",
                            mem_id, from_name, to_name, title[:40], llm_title[:40],
                        )
                        return llm_title, llm_summary
        except Exception as exc:
            logger.debug("LLM escalation unavailable for %s: %s", mem_id, exc)

        # Fallback: mechanical
        fallback_title = f"[{to_name}] {title}"
        fallback_summary = (
            f"【从{from_name}升级】{summary}\n"
            f"（自动升级，非LLM重摘要。设置memory.llm.api_key_env对应的API密钥以启用智能升级。）"
        )
        return fallback_title, fallback_summary

    async def _llm_purge_review(
        self, *, mem_id: str, title: str, summary: str, topics: list,
    ) -> bool:
        """LLM final review before permanent deletion (>730 days old).

        Uses the same ``_resolve_mem_llm_client`` helper as the rest of
        Mem, so the LLM (or its absence) is consistent with escalation
        and Tier 2 compression.
        """
        try:
            from systems.memory.llm_cache import (
                build_cache_key,
                open_cached,
                store_cached,
            )

            client, model = self._resolve_mem_llm_client()
            if client is None:
                return False  # No LLM → purge (safe: entries are >2 years old)
            topics_text = ", ".join(topics[:5]) if topics else "无"
            input_text = f"{mem_id}|{title}|{summary}|{topics_text}"
            cache_key = build_cache_key("purge_review", model, input_text)
            cached = None
            try:
                cached = open_cached(self._db_path, cache_key)
            except Exception:
                cached = None
            if cached is not None and isinstance(cached, dict) and "keep" in cached:
                return bool(cached.get("keep", False))

            prompt = (
                f"以下是一条即将被永久删除的长期记忆（超过730天）。"
                f"判断是否具有持久历史价值应保留。\n"
                f"标题: {title}\n摘要: {summary}\n主题: {topics_text}\n"
                f"重大决策/架构转折/身份定义 → 保留。过时进度细节 → 删除。"
                f"输出JSON: {{\"keep\": true/false, \"reason\": \"...\"}}"
            )
            result = client.complete_json(
                system_prompt="你是长期记忆的守护者。审慎判断历史记录的去留。",
                user_payload={"task": prompt},
                task="scholar.revision",
            )
            if isinstance(result, dict):
                try:
                    store_cached(
                        self._db_path,
                        cache_key=cache_key,
                        task="purge_review",
                        model=model,
                        input_text=input_text,
                        result=result,
                    )
                except Exception:
                    pass
                return bool(result.get("keep", False))
        except Exception:
            pass
        return False

    async def _purge_expired_memories(self) -> int:
        """Hard-delete purged memories older than the audit retention period."""
        conn = open_memory_sqlite(self._db_path)
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        # Only purge entries marked 'purged' for >90 days
        cursor = conn.execute(
            "DELETE FROM compressed_memories "
            "WHERE status = 'purged' AND pinned = 0 "
            "AND identity_layer IS NULL "
            "AND memory_id NOT LIKE 'identity-founding-%' AND compressed_at < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info("Purged %d expired compressed memories", deleted)
        return deleted

    def _http_handlers(self) -> Dict[str, Callable[..., object]]:
        """Expose only application handlers required by the HTTP adapter."""
        return {
            "health_check": self.health_check,
            "get_mem_usage": self.get_mem_usage,
            "create_session": self.create_session,
            "list_sessions": self.list_sessions,
            "get_session": self.get_session,
            "add_turn": self.add_turn,
            "get_session_turns": self.get_session_turns,
            "add_turn_pair": self.add_turn_pair,
            "query_turns": self.query_turns,
            "get_turn": self.get_turn,
            "timeline_view": self.timeline_view,
            "recall": self.recall,
            "list_recall_traces": self.list_recall_traces,
            "record_recall_feedback": self.record_recall_feedback,
            "create_promotion_candidate": self.create_promotion_candidate,
            "list_promotion_candidates": self.list_promotion_candidates,
            "consent_promotion_candidate": self.consent_promotion_candidate,
            "list_promotions": self.list_promotions,
            "revoke_promotion": self.revoke_promotion,
            "forget_memory": self.forget_memory,
            "remember": self.remember,
            "get_identity_archive": self.get_identity_archive,
            "sync_identity_archive": self.sync_identity_archive,
            "verify_identity_experience": self.verify_identity_experience,
            "settle_interaction_experience": self.settle_interaction_experience,
            "list_identity_revisions": self.list_identity_revisions,
            "propose_identity_revision": self.propose_identity_revision,
            "decide_identity_revision": self.decide_identity_revision,
            "tier2_compress": self.tier2_compress,
            "tier1_stats": self.tier1_stats,
            "search_compressed": self.search_compressed,
            "trace_compressed_by_turn": self.trace_compressed_by_turn,
            "trigger_lifecycle": self.trigger_lifecycle,
            "run_all_rules": self.run_all_rules,
            "rules_status": self.rules_status,
            "get_compressed": self.get_compressed,
            "pin_memory": self.pin_memory,
            "hide_memory": self.hide_memory,
            "unpin_memory": self.unpin_memory,
            "llm_health": self.llm_health,
            "semantic_status": self.semantic_status,
            "semantic_backfill": self.semantic_backfill,
            "create_backup": self.create_backup,
            "list_backups": self.list_backups,
            "restore_backup": self.restore_backup,
            "export_memory": self.export_memory,
            "list_graph_entities": self.list_graph_entities,
            "rebuild_entity_graph": self.rebuild_entity_graph,
            "get_graph_neighbors": self.get_graph_neighbors,
            "compression_quality": self.compression_quality,
        }

    async def create_backup(self):
        result = await asyncio.to_thread(self._backup_manager.create_backup)
        return {"status": "created", **result}

    async def list_backups(self):
        backups = await asyncio.to_thread(self._backup_manager.list_backups)
        return {"backups": backups, "count": len(backups)}

    async def restore_backup(self, backup_id: str):
        try:
            result = await asyncio.to_thread(
                self._backup_manager.restore_backup,
                backup_id,
                post_restore=self._database_bootstrap.reconcile_schema,
            )
        except MemoryRestoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    async def export_memory(self):
        return await asyncio.to_thread(self._backup_manager.export_json)

    async def semantic_status(self):
        return await asyncio.to_thread(self._semantic_index.status)

    async def semantic_backfill(self):
        indexed = await asyncio.to_thread(self._semantic_index.index_pending)
        return {"status": "indexed", "indexed": indexed, **self._semantic_index.status()}

    # ── Entity graph introspection ────────────────────────────────────────

    @staticmethod
    def _parse_graph_domains(source_domains: str | None) -> tuple[str, ...]:
        if not source_domains:
            return (DEFAULT_MEMORY_DOMAIN.value,)
        return tuple(
            dict.fromkeys(str(item).strip() for item in source_domains.split(",") if item.strip())
        )

    async def list_graph_entities(
        self,
        limit: int = 50,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        source_domains: str | None = None,
    ):
        from systems.memory.entity_graph import list_graph_entities as _list_entities

        domains = self._parse_graph_domains(source_domains)
        conn = open_memory_sqlite(self._db_path)
        try:
            entities = _list_entities(
                conn,
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_domains=domains,
                limit=limit,
            )
        finally:
            conn.close()
        return {"entities": entities, "count": len(entities)}

    async def get_graph_neighbors(
        self,
        entity_id: str,
        limit: int = 50,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        source_domains: str | None = None,
    ):
        from systems.memory.entity_graph import list_graph_neighbors as _neighbors

        domains = self._parse_graph_domains(source_domains)
        conn = open_memory_sqlite(self._db_path)
        try:
            neighbors = _neighbors(
                conn,
                entity_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_domains=domains,
                limit=limit,
            )
        finally:
            conn.close()
        return {"entity_id": entity_id, "neighbors": neighbors, "count": len(neighbors)}

    async def rebuild_entity_graph(
        self,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        memory_domain: str | None = None,
    ):
        from systems.memory.entity_graph import rebuild_entity_graph as _rebuild

        conn = open_memory_sqlite(self._db_path)
        try:
            linked = _rebuild(conn, owner_id=owner_id, workspace_id=workspace_id, memory_domain=memory_domain)
            conn.commit()
        finally:
            conn.close()
        return {"status": "rebuilt", "memory_records_linked": linked}

    # ── Compression quality dashboard ─────────────────────────────────────

    async def compression_quality(
        self,
        limit: int = 20,
        owner_id: str | None = None,
        workspace_id: str | None = None,
    ):
        bounded = max(1, min(int(limit), 200))
        conn = open_memory_sqlite(self._db_path)
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if owner_id is not None:
                clauses.append("owner_id = ?")
                params.append(str(owner_id))
            if workspace_id is not None:
                clauses.append("workspace_id = ?")
                params.append(str(workspace_id))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = conn.execute(
                "SELECT evaluated_at, status, candidate_count, event_count, "
                "covered_turn_count, event_coverage, backlink_completeness, "
                "compression_ratio, degraded_fraction, source_support, "
                "identifier_fidelity, polarity_consistency, thresholds, failed_checks, "
                "owner_id, workspace_id FROM compression_quality_audit"
                + where + " ORDER BY evaluated_at DESC LIMIT ?",
                [*params, bounded],
            ).fetchall()
        finally:
            conn.close()
        audits = [
            {
                "evaluated_at": str(row[0] or ""),
                "status": str(row[1] or ""),
                "candidate_count": int(row[2] or 0),
                "event_count": int(row[3] or 0),
                "covered_turn_count": int(row[4] or 0),
                "event_coverage": float(row[5] or 0.0),
                "backlink_completeness": float(row[6] or 0.0),
                "compression_ratio": float(row[7] or 0.0),
                "degraded_fraction": float(row[8] or 0.0),
                "source_support": float(row[9] or 0.0),
                "identifier_fidelity": float(row[10] or 0.0),
                "polarity_consistency": float(row[11] or 0.0),
                "thresholds": row[12],
                "failed_checks": row[13],
                "owner_id": str(row[14] or DEFAULT_OWNER_ID),
                "workspace_id": str(row[15] or DEFAULT_WORKSPACE_ID),
            }
            for row in rows
        ]
        passed = sum(1 for audit in audits if audit["status"] == "accepted")
        return {
            "audits": audits,
            "count": len(audits),
            "accepted": passed,
            "rejected": len(audits) - passed,
        }

    @staticmethod
    def _identity_revision_row(row) -> Dict[str, Any]:
        return {
            "proposal_id": row[0],
            "target_memory_id": row[1],
            "baseline_version": row[2],
            "reason": row[3],
            "proposed_changes": json.loads(row[4]),
            "evidence": json.loads(row[5]),
            "source_actor": row[6],
            "status": row[7],
            "decision_reason": row[8],
            "decided_by": row[9],
            "created_at": row[10],
            "decided_at": row[11],
            "release_version": row[12],
            "released_at": row[13],
        }

    async def get_identity_archive(
        self,
        history_limit: int = 20,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ):
        """Return the four-layer identity archive without mutating memory."""
        from systems.memory.identity_seed import (
            founding_manifest_version,
            load_founding_manifest,
            load_founding_story,
        )

        bounded_history = max(1, min(int(history_limit), 100))
        scope = MemoryScope.create(owner_id, workspace_id)
        manifest = load_founding_manifest()
        conn = open_memory_sqlite(self._db_path)
        try:
            anchors = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories "
                "WHERE memory_id LIKE 'identity-founding-%' ORDER BY memory_id"
            ).fetchall()
            evolving = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "AND identity_layer = 'self_narrative' "
                "AND ((owner_id = ? AND workspace_id = ?) OR "
                "(owner_id = '*' AND workspace_id = '*')) "
                "ORDER BY timespan_end DESC LIMIT 12",
                (scope.owner_id, scope.workspace_id),
            ).fetchall()
            experiences = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "AND identity_layer = 'experience' "
                "AND ((owner_id = ? AND workspace_id = ?) OR "
                "(owner_id = '*' AND workspace_id = '*')) "
                "ORDER BY importance DESC, timespan_end DESC LIMIT 12",
                (scope.owner_id, scope.workspace_id),
            ).fetchall()
            revisions = conn.execute(
                "SELECT proposal_id, target_memory_id, baseline_version, reason, "
                "proposed_changes, evidence, source_actor, status, decision_reason, "
                "decided_by, created_at, decided_at, release_version, released_at "
                "FROM identity_revision_proposals "
                "ORDER BY created_at DESC LIMIT ?",
                (bounded_history,),
            ).fetchall()
        finally:
            conn.close()

        return {
            "identity": str(manifest.get("identity") or "xingzi"),
            "manifest_version": founding_manifest_version(),
            "recorded_at": manifest.get("recorded_at"),
            "source_document": manifest.get("source_document"),
            "story_title": "星子计划：从信任开始",
            "story": load_founding_story(),
            "layers": {
                "anchors": [_cmem_row_to_dict(row) for row in anchors],
                "self_narrative": [_cmem_row_to_dict(row) for row in evolving],
                "experiences": [_cmem_row_to_dict(row) for row in experiences],
                "revision_history": [self._identity_revision_row(row) for row in revisions],
            },
            "governance": {
                "anchors_read_only": True,
                "approval_effect": "approved_pending_release",
                "required_proposal_fields": [
                    "target_memory_id", "baseline_version", "reason",
                    "proposed_changes", "evidence",
                ],
            },
        }

    async def list_identity_revisions(self, limit: int = 50):
        archive = await self.get_identity_archive(history_limit=limit)
        revisions = archive["layers"]["revision_history"]
        return {"revisions": revisions, "count": len(revisions)}

    async def sync_identity_archive(self):
        return await self._identity_experience_cycle()

    async def verify_identity_experience(self, request: IdentityExperienceVerification):
        """Mark one existing Tier 1 turn as a verified identity experience."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        authorized_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        evidence_refs = list(
            dict.fromkeys(str(item).strip() for item in request.evidence_refs)
        )
        if not evidence_refs or any(not item for item in evidence_refs):
            raise HTTPException(status_code=400, detail="evidence_refs cannot be empty")

        conn = open_memory_sqlite(self._db_path)
        try:
            row = conn.execute(
                "SELECT metadata FROM turns WHERE turn_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (
                    request.turn_id.strip(),
                    scope.owner_id,
                    scope.workspace_id,
                    authorized_domain,
                ),
            ).fetchone()
            if not row:
                archived = conn.execute(
                    "SELECT turn_id FROM turns_archive WHERE turn_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ?",
                    (
                        request.turn_id.strip(),
                        scope.owner_id,
                        scope.workspace_id,
                        authorized_domain,
                    ),
                ).fetchone()
                if archived:
                    raise HTTPException(
                        status_code=409,
                        detail="Archived turns cannot be verified as identity experiences",
                    )
                raise HTTPException(status_code=404, detail="Turn not found")

            try:
                metadata = json.loads(row[0] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            verified_fields = {
                "identity_experience": True,
                "verified": True,
                "identity_title": str(_redact_for_memory_storage(request.title)).strip(),
                "identity_summary": str(_redact_for_memory_storage(request.summary)).strip(),
                "evidence_refs": evidence_refs,
                "verified_by": request.verified_by.strip(),
                "topics": list(dict.fromkeys(_redact_for_memory_storage(request.topics))),
                "entities": list(dict.fromkeys(_redact_for_memory_storage(request.entities))),
                "event_kind": request.event_kind.strip(),
                "importance": request.importance,
            }
            verification_changed = any(
                metadata.get(key) != value for key, value in verified_fields.items()
            )
            metadata.update(verified_fields)
            if verification_changed or not metadata.get("verified_at"):
                metadata["verified_at"] = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE turns SET metadata = ? WHERE turn_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    request.turn_id.strip(),
                    scope.owner_id,
                    scope.workspace_id,
                    authorized_domain,
                ),
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()

        sync_result = await self._identity_experience_cycle()
        digest = hashlib.sha256(request.turn_id.strip().encode("utf-8")).hexdigest()[:20]
        memory_id = f"identity-experience-turn-{digest}"
        conn = open_memory_sqlite(self._db_path)
        try:
            experience_row = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE memory_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()
        finally:
            conn.close()
        experience = _cmem_row_to_dict(experience_row) if experience_row else None
        return {
            "status": "verified",
            "turn_id": request.turn_id.strip(),
            "experience": experience,
            "sync": sync_result,
        }

    async def settle_interaction_experience(
        self,
        request: InteractionExperienceSettlement,
    ):
        """Settle a dialogue only when the user supplied an explicit signal."""
        from systems.memory.identity_experience import (
            classify_explicit_conversation_experience,
        )

        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        authorized_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        conn = open_memory_sqlite(self._db_path)
        try:
            user_row = conn.execute(
                "SELECT session_id, speaker, text FROM turns WHERE turn_id = ? "
                "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                (
                    request.user_turn_id.strip(),
                    scope.owner_id,
                    scope.workspace_id,
                    authorized_domain,
                ),
            ).fetchone()
            agent_row = None
            if request.agent_turn_id:
                agent_row = conn.execute(
                    "SELECT session_id, speaker, text FROM turns WHERE turn_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (
                        request.agent_turn_id.strip(),
                        scope.owner_id,
                        scope.workspace_id,
                        authorized_domain,
                    ),
                ).fetchone()
        finally:
            conn.close()
        if not user_row:
            raise HTTPException(status_code=404, detail="User turn not found")
        if str(user_row[1]) != "user":
            raise HTTPException(status_code=409, detail="user_turn_id is not a user turn")
        if agent_row and (
            str(agent_row[0]) != str(user_row[0]) or str(agent_row[1]) != "agent"
        ):
            raise HTTPException(
                status_code=409,
                detail="agent_turn_id is not a paired agent turn",
            )

        classification = classify_explicit_conversation_experience(str(user_row[2]))
        if classification is None:
            return {
                "status": "ignored",
                "reason": "no_explicit_experience_signal",
                "user_turn_id": request.user_turn_id.strip(),
            }

        user_text = str(user_row[2]).strip()
        agent_text = str(agent_row[2]).strip() if agent_row else ""
        title_excerpt = " ".join(user_text.split())[:120]
        summary = f"用户确认：{user_text}"
        if agent_text:
            summary += f"\n处理结果：{agent_text}"
        evidence_refs = [f"turn:{request.user_turn_id.strip()}"]
        if request.agent_turn_id:
            evidence_refs.append(f"turn:{request.agent_turn_id.strip()}")
        result = await self.verify_identity_experience(
            IdentityExperienceVerification(
                turn_id=request.user_turn_id.strip(),
                title=f"{classification['title_prefix']}：{title_excerpt}"[:300],
                summary=summary[:4000],
                evidence_refs=evidence_refs,
                verified_by=request.verified_by.strip(),
                topics=list(classification["topics"]),
                entities=["锚点", "星子", "Mem"],
                event_kind=str(classification["event_kind"]),
                importance=float(classification["importance"]),
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                memory_actor=request.memory_actor,
                memory_domain=authorized_domain,
            )
        )
        return {
            **result,
            "status": "settled",
            "classification": classification["kind"],
        }

    async def propose_identity_revision(self, proposal: IdentityRevisionProposal):
        from systems.memory.identity_seed import (
            founding_manifest_version,
            is_founding_memory_id,
        )

        if not is_founding_memory_id(proposal.target_memory_id):
            raise HTTPException(status_code=404, detail="Founding identity memory not found")
        current_version = founding_manifest_version()
        if proposal.baseline_version != current_version:
            raise HTTPException(
                status_code=409,
                detail=f"Identity baseline changed: expected {current_version}",
            )
        allowed_changes = {"title", "summary", "topics", "entities", "event_kind"}
        invalid = set(proposal.proposed_changes) - allowed_changes
        if invalid or not proposal.proposed_changes:
            raise HTTPException(
                status_code=400,
                detail="proposed_changes must use canonical identity fields only",
            )
        if any(not str(item).strip() for item in proposal.evidence):
            raise HTTPException(status_code=400, detail="evidence entries cannot be empty")
        proposal_id = f"identity-revision-{uuid.uuid4()}"
        created_at = datetime.now(timezone.utc).isoformat()
        conn = open_memory_sqlite(self._db_path)
        try:
            conn.execute(
                "INSERT INTO identity_revision_proposals "
                "(proposal_id, target_memory_id, baseline_version, reason, "
                "proposed_changes, evidence, source_actor, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    proposal_id, proposal.target_memory_id, proposal.baseline_version,
                    _redact_for_memory_storage(proposal.reason),
                    json.dumps(
                        _redact_for_memory_storage(proposal.proposed_changes),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        _redact_for_memory_storage(proposal.evidence),
                        ensure_ascii=False,
                    ),
                    proposal.source_actor, created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "proposal_id": proposal_id,
            "status": "pending",
            "created_at": created_at,
            "requires_governance_decision": True,
        }

    async def decide_identity_revision(
        self, proposal_id: str, decision: IdentityRevisionDecision
    ):
        normalized = decision.decision.strip().lower()
        if normalized not in {"approve", "reject"}:
            raise HTTPException(status_code=400, detail="decision must be approve or reject")
        status = "approved_pending_release" if normalized == "approve" else "rejected"
        decided_at = datetime.now(timezone.utc).isoformat()
        conn = open_memory_sqlite(self._db_path)
        try:
            current = conn.execute(
                "SELECT status FROM identity_revision_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Identity revision not found")
            if current[0] != "pending":
                raise HTTPException(status_code=409, detail="Identity revision already decided")
            conn.execute(
                "UPDATE identity_revision_proposals SET status = ?, decision_reason = ?, "
                "decided_by = ?, decided_at = ? WHERE proposal_id = ?",
                (status, decision.reasoning_summary, decision.decided_by, decided_at, proposal_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "proposal_id": proposal_id,
            "status": status,
            "decided_at": decided_at,
            "runtime_identity_changed": False,
        }

    def _resolve_mem_llm_client(self, *, role: str = "default"):
        """Resolve a configured Mem LLM client for a Mem role.

        Thin pass-through to the canonical resolver at
        ``memai.model_config.resolve_mem_llm_client``.  All resolution
        logic (memory.llm.* priority, role overrides,
        provider credential lookup, OpenAICompatibleLLMClient construction) lives in
        one place inside the memai package; this method is just a
        convenient accessor.

        ``role`` picks the ``memory.llm.roles.*`` override (extraction,
        summarization, ...) and falls back to the default config when the role
        has no override.

        Returns ``(client, model_name)``.  ``client`` is ``None`` when
        no API key is available; callers must degrade to heuristic /
        mechanical paths in that case.
        """
        try:
            from memai.model_config import resolve_mem_llm_client
            return resolve_mem_llm_client(role=role)
        except Exception:
            return None, ""

    async def _app_lifespan(self, app: FastAPI):
        """Own Gateway registration and memory maintenance background tasks."""
        del app
        svc_id = await self.register_with_gateway()
        if svc_id:
            logger.info("Memory service registered with gateway: %s", svc_id)
        else:
            logger.warning("Memory service failed to register with gateway")
        # Startup LLM health check
        await self._check_llm_health()
        if self._llm_healthy:
            logger.info("LLM health check passed: model=%s", self._llm_model)
        else:
            logger.warning("LLM health check FAILED — memory compression will be degraded")
        try:
            identity_sync = await self._identity_experience_cycle()
            logger.info("Identity experience sync completed: %s", identity_sync)
        except Exception:
            logger.warning("Identity experience startup sync failed", exc_info=True)
        self._gateway_registration_task = asyncio.create_task(
            self._gateway_registration_loop()
        )
        self._compression_task = asyncio.create_task(self._compression_loop())
        self._semantic_task = asyncio.create_task(self._semantic_index_loop())
        try:
            yield
        finally:
            tasks = (
                self._gateway_registration_task,
                self._compression_task,
                self._semantic_task,
            )
            for task in tasks:
                if task and not task.done():
                    task.cancel()
            for task in tasks:
                if task is None:
                    continue
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _semantic_index_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._semantic_index.index_pending)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Semantic memory index update failed", exc_info=True)
            try:
                await asyncio.wait_for(self._semantic_wake.wait(), timeout=30.0)
                self._semantic_wake.clear()
            except asyncio.TimeoutError:
                pass

    async def _gateway_registration_is_current(self) -> bool:
        service_id = self._gateway_service_id
        self._last_gateway_registration_check_at = datetime.now().isoformat()
        if not service_id:
            self._gateway_registration_healthy = False
            return False

        expected_address = f"http://{self.config.host}:{self.config.port}".rstrip("/")
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.config.gateway_address}/admin/services/{service_id}",
                    timeout=5,
                ) as response:
                    if response.status != 200:
                        self._gateway_registration_healthy = False
                        return False
                    payload = await response.json()
            is_current = (
                payload.get("service_type") == "memory"
                and str(payload.get("address") or "").rstrip("/")
                == expected_address
            )
            self._gateway_registration_healthy = is_current
            return is_current
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._gateway_registration_healthy = False
            logger.debug("Memory gateway registration check failed: %s", exc)
            return False

    async def _ensure_gateway_registration(self) -> Optional[str]:
        if await self._gateway_registration_is_current():
            return self._gateway_service_id
        return await self.register_with_gateway(max_retries=1)

    async def _gateway_registration_loop(self) -> None:
        interval = max(1, int(self.config.gateway_registration_check_interval))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._ensure_gateway_registration()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._gateway_registration_healthy = False
                logger.warning(
                    "Memory gateway registration recovery failed",
                    exc_info=True,
                )

    async def _compression_loop(self) -> None:
        """Periodically trigger memory compression (runs in the memory service).

        Per architecture baseline §3.4, Mem is responsible for its own
        maintenance — the supervisor should not be running maintenance
        loops on Mem's behalf.

        Now also runs Tier 1 decay + Tier 2 bridge (two-tier architecture).
        """
        while True:
            await asyncio.sleep(self.config.compression_interval)
            try:
                # P0-4 健康信号 (4-3.1): re-probe LLM each cycle so health recovers
                # after a transient outage / late key configuration, instead of
                # being frozen at the startup result until a restart.
                await self._check_llm_health()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Periodic LLM health re-check skipped", exc_info=True)
            try:
                # ── Tier 1 decay + Tier 2 bridge + Lifecycle ─────
                await self._run_all_rules_internal()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Background compression loop failed", exc_info=True)

    async def _run_all_rules_internal(self) -> Dict[str, Any]:
        """Execute all five memory rules in correct order (internal, track execution)."""
        now = datetime.now().isoformat()
        results: Dict[str, Any] = {}
        rules = [
            ("identity_experience", self._identity_experience_cycle),
            ("tier1_decay", self._tier1_decay_cycle),
            ("tier2_bridge", self._tier2_bridge_cycle),
            ("lifecycle_escalation", self._apply_compression_lifecycle),
            ("purge_expired", self._purge_expired_memories),
        ]
        effective_work = 0
        for rule_name, rule_fn in rules:
            try:
                result = await rule_fn()
                results[rule_name] = result
                self._last_rule_run[rule_name] = now
                self._rule_run_counts[rule_name] = self._rule_run_counts.get(rule_name, 0) + 1
                effective_work += self._rule_effective_count(result)
            except Exception as exc:
                logger.warning("Memory maintenance rule %s failed: %s", rule_name, exc, exc_info=True)
                results[rule_name] = {"error": str(exc)}
        # P0-4 健康信号: only stamp the "effective activity" marker when a rule
        # actually wrote/changed rows this cycle. A no-op cycle (no candidates,
        # nothing to decay) advances last_run but NOT this marker, so the UI no
        # longer shows "记忆活跃 ✅" while the pipeline is idle or broken.
        if effective_work > 0:
            self._last_effective_activity_at = now
        results["_effective_work"] = effective_work
        return results

    async def _identity_experience_cycle(self) -> Dict[str, int]:
        from VoidCube_core.runtime_paths import get_runtime_layout
        from memai.governance_repository import GovernanceEventRepository
        from systems.memory.identity_experience import sync_identity_experiences

        events = GovernanceEventRepository(
            get_runtime_layout().supervisor_governance_log
        ).list_events()
        conn = open_memory_sqlite(self._db_path)
        try:
            return sync_identity_experiences(conn, governance_events=events)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _rule_effective_count(result: Any) -> int:
        """Number of rows a rule actually wrote/changed, across rule return shapes."""
        if isinstance(result, int):
            return max(0, result)
        if isinstance(result, dict):
            if "error" in result:
                return 0
            total = 0
            for key in ("escalated", "purged", "turns_processed", "deleted", "updated_count"):
                val = result.get(key)
                if isinstance(val, int):
                    total += max(0, val)
            return total
        return 0

    async def run_all_rules(self, request: dict = None):
        """Execute all five memory compression rules (public API for supervisor).

        Rules executed in order:
          1. identity_experience — Settle verified experiences and evidence-backed narrative
          2. tier1_decay         — Exponential decay of turn relevance_scores
          3. tier2_bridge        — Feed expired turns into ChroniclePipeline → compressed_memories
          4. lifecycle_escalation — Escalate ordinary entries through compression levels
          5. purge_expired       — Hard-delete ordinary purged entries past audit retention
        """
        results = await self._run_all_rules_internal()
        return {"status": "ok", "rules": results, "executed_at": datetime.now().isoformat()}

    async def rules_status(self):
        """Return the last execution time and count for each rule."""
        return {
            "rules": {
                name: {
                    "last_run": self._last_rule_run.get(name),
                    "run_count": self._rule_run_counts.get(name, 0),
                }
                for name in ["tier1_decay", "tier2_bridge", "lifecycle_escalation", "purge_expired"]
            },
            "compression_interval": self.config.compression_interval,
            "tier1_retention_days": self.config.tier1_retention_days,
            # P0-4 健康信号: last cycle that performed real write work, and the
            # last time LLM health was actually probed. UI computes memory_active
            # from effective_activity_at (not last_run) so no-op cycles don't
            # masquerade as "记忆活跃".
            "effective_activity_at": self._last_effective_activity_at,
            "llm_healthy": self._llm_healthy,
            "llm_model": self._llm_model,
            "llm_error": self._llm_error,
            "llm_health_checked_at": self._last_llm_health_check_at,
        }

    async def _tier1_decay_cycle(self, *, now: datetime | None = None) -> int:
        return await run_tier1_decay_cycle(
            self._db_path, self.config, now=now, logger=logger
        )

    async def _tier2_bridge_cycle(self) -> int:
        return await run_tier2_bridge_cycle(
            self._db_path, self.config, request_factory=Tier2CompressRequest,
            compress=self.tier2_compress,
            maintenance_actor=MemoryActor.MEMORY_MAINTENANCE, logger=logger,
        )

    async def health_check(self):
        return {
            "status": (
                "healthy" if self._gateway_registration_healthy else "degraded"
            ),
            "service": "memory-service",
            "gateway_registration": {
                "healthy": self._gateway_registration_healthy,
                "service_id": self._gateway_service_id,
                "last_checked_at": self._last_gateway_registration_check_at,
            },
            "recall": {
                "requests": self._recall_requests,
                "hits": self._recall_hits,
                "failures": self._recall_failures,
                "hit_rate": (
                    round(self._recall_hits / self._recall_requests, 4)
                    if self._recall_requests
                    else 0.0
                ),
                "last_recall_at": self._last_recall_at,
                "last_result_count": self._last_recall_count,
                "last_latency_ms": self._last_recall_latency_ms,
                "last_trace_id": self._last_recall_trace_id,
                "last_status": self._last_recall_status,
            },
        }

    async def get_mem_usage(self) -> Dict[str, Any]:
        """Return cumulative LLM token usage for the memory model.

        NOTE: This endpoint reports the in-process ``memai.llm_client``
        accumulator, which only counts calls routed through
        ``OpenAICompatibleLLMClient`` (the default transport).  The memory
        service itself uses ``aiohttp`` directly for summarisation and does
        NOT populate this counter — so the endpoint will return all zeros
        under normal operation.

        The canonical source for the supervisor's memory-model context
        usage is ``/ui/state.mem_usage`` on the supervisor (port 6002),
        which runs in the same process as the MemAI pipeline.

        This endpoint remains available for external monitoring tools that
        may inject LLM calls through the ``OpenAICompatibleLLMClient`` path.
        """
        try:
            from memai.llm_client import get_memory_token_usage
            usage = get_memory_token_usage()
        except Exception:
            usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "request_count": 0,
                "context_length": 65536,
            }
        context_length = usage.get("context_length", 65536)
        total_tokens = usage.get("total_tokens", 0)
        percent = round((total_tokens / context_length) * 100) if context_length > 0 else None
        return {
            "status": "ok",
            "usage": usage,
            "context_percent": percent,
            "context_length": context_length,
        }

    # ── Tier 1: Short-term Conversation Store ──────────────────────

    async def create_session(self, request: SessionCreate):
        """Create a new conversation session. Uses caller-provided ID if given."""
        session_id = request.session_id.strip() if request.session_id else str(uuid.uuid4())
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        now = datetime.now().isoformat()
        conn = open_memory_sqlite(self._db_path)
        existing = conn.execute(
            "SELECT owner_id, workspace_id, memory_domain FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing and tuple(existing) != (
            scope.owner_id,
            scope.workspace_id,
            memory_domain,
        ):
            conn.close()
            raise HTTPException(status_code=409, detail="Session belongs to another memory scope")
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                scope.owner_id,
                scope.workspace_id,
                memory_domain,
                now,
                now,
                json.dumps(_redact_for_memory_storage(request.metadata)),
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Session created: %s", session_id)
        return {
            "session_id": session_id,
            "created_at": now,
            "status": "created" if not existing else "existing",
            "memory_domain": memory_domain,
            **scope.as_dict(),
        }

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """List sessions in one private memory scope."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        rows = conn.execute(
            "SELECT s.session_id, s.created_at, s.updated_at, s.metadata, "
            "COUNT(t.turn_id) as turn_count "
            "FROM sessions s LEFT JOIN turns t ON s.session_id = t.session_id "
            "AND t.owner_id = s.owner_id AND t.workspace_id = s.workspace_id "
            "AND t.memory_domain = s.memory_domain "
            "WHERE s.owner_id = ? AND s.workspace_id = ? AND s.memory_domain = ? "
            "GROUP BY s.session_id ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
            (scope.owner_id, scope.workspace_id, authorized_domain, limit, offset),
        ).fetchall()
        conn.close()
        return {
            "sessions": [
                {
                    "session_id": r[0],
                    "created_at": r[1],
                    "updated_at": r[2],
                    "metadata": json.loads(r[3]) if r[3] else {},
                    "turn_count": r[4],
                    "memory_domain": authorized_domain,
                }
                for r in rows
            ],
            "count": len(rows),
        }

    async def get_session(
        self,
        session_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Get a session with its turn count."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        row = conn.execute(
            "SELECT session_id, created_at, updated_at, metadata FROM sessions "
            "WHERE session_id = ? AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
            (session_id, scope.owner_id, scope.workspace_id, authorized_domain),
        ).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        turn_count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ? AND owner_id = ? "
            "AND workspace_id = ? AND memory_domain = ?",
            (session_id, scope.owner_id, scope.workspace_id, authorized_domain),
        ).fetchone()[0]
        conn.close()
        return {
            "session_id": row[0],
            "created_at": row[1],
            "updated_at": row[2],
            "metadata": json.loads(row[3]) if row[3] else {},
            "turn_count": turn_count,
            "memory_domain": authorized_domain,
        }

    def _derive_turn_dedup_key(
        self,
        session_id: str,
        request: TurnCreate,
        *,
        stored_text: str,
    ) -> Optional[str]:
        metadata = dict(request.metadata or {})
        for key in ("turn_dedup_key", "dedup_key", "idempotency_key"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value

        retry_identifiers = [
            str(metadata.get(key) or "").strip()
            for key in ("task_id", "trace_id", "request_id", "decision_id")
        ]
        retry_identifiers = [value for value in retry_identifiers if value]
        if not retry_identifiers:
            return None

        payload = {
            "session_id": session_id,
            "speaker": request.speaker,
            "text": stored_text,
            "retry_identifiers": retry_identifiers,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "auto_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

    async def add_turn(self, session_id: str, request: TurnCreate):
        """Add a conversation turn to a session."""
        conn = open_memory_sqlite(self._db_path)
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        stored_text = str(_redact_for_memory_storage(request.text))
        stored_metadata = _redact_for_memory_storage(request.metadata)
        now = datetime.now().astimezone().isoformat()
        # Ensure session exists in the same DB transaction as the turn write.
        ses = conn.execute(
            "SELECT session_id, owner_id, workspace_id, memory_domain FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if ses and (str(ses[1]), str(ses[2]), str(ses[3])) != (
            scope.owner_id,
            scope.workspace_id,
            memory_domain,
        ):
            conn.close()
            raise HTTPException(status_code=409, detail="Session belongs to another memory scope")
        if not ses:
            conn.execute(
                "INSERT INTO sessions "
                "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                    now,
                    now,
                    json.dumps({"source": "turn_auto_create"}),
                ),
            )
        dedup_key = self._derive_turn_dedup_key(
            session_id,
            request,
            stored_text=stored_text,
        )
        if dedup_key:
            existing = conn.execute(
                "SELECT turn_id, timestamp FROM turns WHERE session_id = ? AND dedup_key = ?",
                (session_id, dedup_key),
            ).fetchone()
            if existing:
                conn.close()
                return {
                    "turn_id": existing[0],
                    "session_id": session_id,
                    "timestamp": existing[1],
                    "status": "deduplicated",
                    "dedup_key": dedup_key,
                }
        turn_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO turns (turn_id, session_id, speaker, text, timestamp, "
            "relevance_score, decay_factor, tags, metadata, dedup_key, "
            "compressed_to_tier2, last_decay_at, owner_id, workspace_id, memory_domain) "
            "VALUES (?, ?, ?, ?, ?, 1.0, 0.01, ?, ?, ?, 0, ?, ?, ?, ?)",
            (
                turn_id,
                session_id,
                request.speaker,
                stored_text,
                now,
                json.dumps([]),
                json.dumps(stored_metadata),
                dedup_key,
                now,
                scope.owner_id,
                scope.workspace_id,
                memory_domain,
            ),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.commit()
        conn.close()
        logger.debug("Turn %s added to session %s", turn_id, session_id)
        self._semantic_wake.set()
        response = {"turn_id": turn_id, "session_id": session_id, "timestamp": now, "status": "created", "memory_domain": memory_domain}
        if dedup_key:
            response["dedup_key"] = dedup_key
        return response

    async def add_turn_pair(self, request: TurnPairCreate):
        """Atomically persist one completed user/assistant exchange."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        session_id = request.session_id.strip()
        now = datetime.now().astimezone().isoformat()
        conn = open_memory_sqlite(self._db_path)
        turn_ids: dict[str, str] = {}
        profile_settlement: dict[str, Any] = {"action": "none"}
        try:
            existing_session = conn.execute(
                "SELECT owner_id, workspace_id, memory_domain FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing_session and tuple(existing_session) != (
                scope.owner_id,
                scope.workspace_id,
                memory_domain,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Session belongs to another memory scope",
                )
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                    now,
                    now,
                    json.dumps({"source": "agent_memory_provider"}),
                ),
            )
            stored_metadata = _redact_for_memory_storage(request.metadata)
            for speaker, content in (
                ("user", request.user_content),
                ("agent", request.assistant_content),
            ):
                text = str(_redact_for_memory_storage(content or "")).strip()
                if not text:
                    continue
                dedup_key = f"{request.write_id}:{speaker}"
                existing_turn = conn.execute(
                    "SELECT turn_id FROM turns WHERE session_id = ? AND dedup_key = ?",
                    (session_id, dedup_key),
                ).fetchone()
                if existing_turn:
                    turn_ids[speaker] = str(existing_turn[0])
                    continue
                turn_id = str(uuid.uuid4())
                metadata = {
                    **dict(stored_metadata or {}),
                    "source": "agent_memory_provider",
                    "turn_dedup_key": dedup_key,
                }
                conn.execute(
                    "INSERT INTO turns "
                    "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
                    "decay_factor, tags, metadata, dedup_key, compressed_to_tier2, "
                    "last_decay_at, owner_id, workspace_id, memory_domain) "
                    "VALUES (?, ?, ?, ?, ?, 1.0, 0.01, '[]', ?, ?, 0, ?, ?, ?, ?)",
                    (
                        turn_id,
                        session_id,
                        speaker,
                        text,
                        now,
                        json.dumps(metadata, ensure_ascii=False),
                        dedup_key,
                        now,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                )
                turn_ids[speaker] = turn_id
            if turn_ids.get("user"):
                user_turn = conn.execute(
                    "SELECT text, timestamp FROM turns WHERE turn_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (
                        turn_ids["user"],
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                ).fetchone()
                if user_turn:
                    capture = capture_explicit_user_profile(
                        str(user_turn[0]),
                        turn_id=turn_ids["user"],
                        timestamp=datetime.fromisoformat(str(user_turn[1])),
                    )
                    predicates = capture.revoke_predicates
                    if predicates == ("*",):
                        active_predicates = (
                            str(row[0])
                            for row in conn.execute(
                                "SELECT DISTINCT predicate FROM profile_memories "
                                "WHERE owner_id = ? AND workspace_id = ? "
                                "AND subject = 'user' AND status = 'active'",
                                (scope.owner_id, scope.workspace_id),
                            ).fetchall()
                        )
                        predicates = tuple(
                            dict.fromkeys(
                                (*ALL_PROFILE_PREDICATES, *active_predicates)
                            )
                        )
                    if predicates:
                        profile_settlement = revoke_profile_predicates(
                            conn,
                            predicates,
                            owner_id=scope.owner_id,
                            workspace_id=scope.workspace_id,
                            memory_domain=memory_domain,
                            turn_id=turn_ids["user"],
                            now=now,
                        )
                    elif capture.profiles:
                        inserted = sum(
                            upsert_profile_memory(
                                conn,
                                profile,
                                owner_id=scope.owner_id,
                                workspace_id=scope.workspace_id,
                                memory_domain=memory_domain,
                                now=now,
                            )
                            for profile in capture.profiles
                        )
                        profile_settlement = {
                            "action": "upserted",
                            "predicates": [
                                profile.predicate for profile in capture.profiles
                            ],
                            "inserted": inserted,
                        }
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        self._semantic_wake.set()
        settlement = None
        if turn_ids.get("user") and profile_settlement["action"] == "none":
            settlement = await self.settle_interaction_experience(
                InteractionExperienceSettlement(
                    user_turn_id=turn_ids["user"],
                    agent_turn_id=turn_ids.get("agent"),
                    owner_id=scope.owner_id,
                    workspace_id=scope.workspace_id,
                    memory_actor=request.memory_actor,
                    memory_domain=request.memory_domain,
                )
            )
        return {
            "status": "stored",
            "session_id": session_id,
            "write_id": request.write_id,
            "turn_ids": turn_ids,
            "identity_settlement": settlement,
            "profile_settlement": profile_settlement,
            "memory_domain": memory_domain,
            **scope.as_dict(),
        }

    @staticmethod
    def _promotion_http_error(exc: Exception) -> HTTPException:
        if isinstance(exc, (MemoryPromotionAccessError, MemoryDomainAccessError)):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, MemoryPromotionNotFoundError):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, MemoryPromotionConflictError):
            return HTTPException(status_code=409, detail=str(exc))
        return HTTPException(status_code=400, detail=str(exc))

    async def create_promotion_candidate(
        self,
        request: MemoryPromotionCandidateCreate,
    ):
        request = request.model_copy(
            update={
                "reason": str(_redact_for_memory_storage(request.reason)).strip(),
                "governance_ref": str(
                    _redact_for_memory_storage(request.governance_ref)
                ).strip(),
            }
        )
        conn = open_memory_sqlite(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = create_memory_promotion_candidate(conn, request)
            conn.commit()
        except (
            MemoryPromotionAccessError,
            MemoryPromotionConflictError,
            MemoryPromotionNotFoundError,
            MemoryPromotionValidationError,
        ) as exc:
            conn.rollback()
            raise self._promotion_http_error(exc) from exc
        finally:
            conn.close()
        return {"status": "awaiting_user_consent", "candidate": result}

    async def list_promotion_candidates(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        source_domain: Optional[MemoryDomain] = None,
        target_domain: Optional[MemoryDomain] = None,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = MemoryActor.GOVERNOR,
    ):
        scope = MemoryScope.create(owner_id, workspace_id)
        try:
            authorize_promotion_manager(memory_actor)
            conn = open_memory_sqlite(self._db_path)
            try:
                candidates = list_memory_promotion_candidates(
                    conn,
                    scope=scope,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    status=status,
                    limit=limit,
                )
            finally:
                conn.close()
        except (
            MemoryPromotionAccessError,
            MemoryPromotionValidationError,
            ValueError,
        ) as exc:
            raise self._promotion_http_error(exc) from exc
        return {"candidates": candidates, "count": len(candidates)}

    async def consent_promotion_candidate(
        self,
        candidate_id: str,
        request: MemoryPromotionConsent,
    ):
        request = request.model_copy(
            update={
                "reason": str(_redact_for_memory_storage(request.reason)).strip(),
            }
        )
        conn = open_memory_sqlite(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            candidate, promotion = consent_memory_promotion_candidate(
                conn,
                candidate_id,
                request,
            )
            conn.commit()
        except (
            MemoryPromotionAccessError,
            MemoryPromotionConflictError,
            MemoryPromotionNotFoundError,
            MemoryPromotionValidationError,
        ) as exc:
            conn.rollback()
            raise self._promotion_http_error(exc) from exc
        finally:
            conn.close()
        return {
            "status": candidate["status"],
            "candidate": candidate,
            "promotion": promotion,
        }

    async def list_promotions(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        target_domain: Optional[MemoryDomain] = None,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = MemoryActor.STELLAR_COMPANION,
    ):
        scope = MemoryScope.create(owner_id, workspace_id)
        try:
            actor = MemoryActor(memory_actor)
            if target_domain is not None:
                target_domains = _authorized_read_domains(actor, [target_domain])
            elif actor in {MemoryActor.MEMORY_MAINTENANCE, MemoryActor.GOVERNOR}:
                target_domains = None
            else:
                target_domains = authorize_read(actor, None)
            conn = open_memory_sqlite(self._db_path)
            try:
                promotions = list_memory_promotions(
                    conn,
                    scope=scope,
                    target_domains=target_domains,
                    status=status,
                    limit=limit,
                )
                conn.commit()
            finally:
                conn.close()
        except (
            MemoryPromotionValidationError,
            MemoryDomainAccessError,
            ValueError,
        ) as exc:
            raise self._promotion_http_error(exc) from exc
        return {"promotions": promotions, "count": len(promotions)}

    async def revoke_promotion(
        self,
        promotion_id: str,
        request: MemoryPromotionRevoke,
    ):
        request = request.model_copy(
            update={
                "reason": str(_redact_for_memory_storage(request.reason)).strip(),
            }
        )
        conn = open_memory_sqlite(self._db_path)
        try:
            result = revoke_memory_promotion(conn, promotion_id, request)
            conn.commit()
        except (
            MemoryPromotionAccessError,
            MemoryPromotionConflictError,
            MemoryPromotionNotFoundError,
            MemoryPromotionValidationError,
        ) as exc:
            conn.rollback()
            raise self._promotion_http_error(exc) from exc
        finally:
            conn.close()
        return {"status": "revoked", "promotion": result}

    async def get_session_turns(
        self,
        session_id: str,
        limit: int = 200,
        offset: int = 0,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Get all turns for a session, ordered by timestamp."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, memory_domain "
            "FROM turns WHERE session_id = ? AND owner_id = ? AND workspace_id = ? "
            "AND memory_domain = ? "
            "ORDER BY timestamp ASC LIMIT ? OFFSET ?",
            (session_id, scope.owner_id, scope.workspace_id, authorized_domain, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ? AND owner_id = ? "
            "AND workspace_id = ? AND memory_domain = ?",
            (session_id, scope.owner_id, scope.workspace_id, authorized_domain),
        ).fetchone()[0]
        conn.close()
        return {
            "session_id": session_id,
            "turns": [_turn_row_to_dict(r) for r in rows],
            "count": len(rows),
            "total": total,
        }

    async def query_turns(
        self, start: str = None, end: str = None, speaker: str = None,
        session_id: str = None, limit: int = 100, offset: int = 0,
        newest_first: bool = False,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Query turns by time range, speaker, or session."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        sql = "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, " \
              "decay_factor, tags, metadata, compressed_to_tier2, memory_domain FROM turns " \
              "WHERE owner_id = ? AND workspace_id = ? AND memory_domain = ?"
        params: list = [scope.owner_id, scope.workspace_id, authorized_domain]
        if start:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end:
            sql += " AND timestamp <= ?"
            params.append(end)
        if speaker:
            sql += " AND speaker = ?"
            params.append(speaker)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += f" ORDER BY timestamp {'DESC' if newest_first else 'ASC'} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return {
            "turns": [_turn_row_to_dict(r) for r in rows],
            "count": len(rows),
        }

    async def get_turn(
        self,
        turn_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Get a single turn by ID."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        row = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, memory_domain "
            "FROM turns WHERE turn_id = ? AND owner_id = ? AND workspace_id = ? "
            "AND memory_domain = ?",
            (turn_id, scope.owner_id, scope.workspace_id, authorized_domain),
        ).fetchone()
        if not row:
            # Check archive
            row = conn.execute(
                "SELECT turn_id, session_id, speaker, text_summary, timestamp, "
                "compressed_at, event_ids, scene_ids, original_text "
                "FROM turns_archive WHERE turn_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (turn_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Turn not found")
            conn.close()
            return {
                "turn_id": row[0], "session_id": row[1], "speaker": row[2],
                "text": row[8] or row[3], "timestamp": row[4],
                "in_archive": True, "compressed_at": row[5],
                "event_ids": json.loads(row[6]) if row[6] else [],
                "scene_ids": json.loads(row[7]) if row[7] else [],
                "memory_domain": authorized_domain,
            }
        conn.close()
        return _turn_row_to_dict(row)

    async def timeline_view(self, request: TimelineQuery):
        """Get timeline view for a specific date with turn summaries."""
        conn = open_memory_sqlite(self._db_path)
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        source_domains = _authorized_read_domains(
            request.memory_actor, request.source_domains
        )
        date_start = f"{request.date}T00:00:00"
        date_end = f"{request.date}T23:59:59"
        sql = "SELECT turn_id, session_id, speaker, text, timestamp FROM turns " \
              "WHERE timestamp >= ? AND timestamp <= ? " \
              "AND owner_id = ? AND workspace_id = ?"
        placeholders = ",".join("?" for _ in source_domains)
        sql += f" AND memory_domain IN ({placeholders})"
        params: list = [
            date_start, date_end, scope.owner_id, scope.workspace_id, *source_domains
        ]
        if request.session_id:
            sql += " AND session_id = ?"
            params.append(request.session_id)
        if request.speaker:
            sql += " AND speaker = ?"
            params.append(request.speaker)
        sql += " ORDER BY timestamp ASC LIMIT ?"
        params.append(request.limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return {
            "date": request.date,
            "turns": [
                {
                    "turn_id": r[0], "session_id": r[1], "speaker": r[2],
                    "text_preview": r[3][:200] + ("..." if len(r[3]) > 200 else ""),
                    "timestamp": r[4],
                }
                for r in rows
            ],
            "count": len(rows),
        }

    async def tier2_compress(self, request: Tier2CompressRequest = None):
        """Run the canonical Tier 1 → Tier 2 bridge with request-scoped policy."""
        req = request or Tier2CompressRequest()
        memory_domain = _authorized_write_domain(req.memory_actor, req.memory_domain)
        bridge = Tier1ToTier2Bridge(
            self._db_path,
            retention_days=req.retention_days,
            batch_size=req.batch_size,
            min_relevance=req.min_relevance,
            archive_keep_original=self.config.tier1_archive_keep_original,
            max_turns=self.config.tier1_max_turns,
            pipeline_factory=self._build_compression_pipeline,
            compression_degraded=not self._llm_healthy,
            min_event_coverage=self.config.tier2_min_event_coverage,
            min_backlink_completeness=self.config.tier2_min_backlink_completeness,
            max_compression_ratio=self.config.tier2_max_compression_ratio,
            max_degraded_fraction=self.config.tier2_max_degraded_fraction,
            min_source_support=self.config.tier2_min_source_support,
            min_identifier_fidelity=self.config.tier2_min_identifier_fidelity,
            min_polarity_consistency=self.config.tier2_min_polarity_consistency,
            memory_domain=memory_domain,
            owner_id=req.owner_id,
            workspace_id=req.workspace_id,
        )
        result = await asyncio.to_thread(
            bridge.run_cycle,
            dry_run=req.dry_run,
            force_oldest=req.force_oldest,
        )
        payload = result.to_dict()
        if result.turns_processed:
            self._semantic_wake.set()
        payload["compression_degraded"] = not self._llm_healthy
        payload["compression_method"] = (
            "llm" if self._llm_healthy else "heuristic"
        )
        if not self._llm_healthy:
            payload["degradation_reason"] = self._llm_error or "llm_unavailable"
        return payload

    async def _check_llm_health(self) -> bool:
        """Verify LLM connectivity. Probed at startup and re-probed each
        compression cycle so health can recover after a transient outage or a
        late-configured key (P0-4 健康信号, 4-3.1).

        Reads from the same ``memory.llm.*`` config as the rest of Mem via
        ``_resolve_mem_llm_client``.  When no key is configured the
        service runs in fully-degraded mode (no LLM features).
        """
        self._last_llm_health_check_at = datetime.now().isoformat()
        client, model = self._resolve_mem_llm_client()
        self._llm_model = model or "none"
        self._llm_error = ""
        if client is None:
            self._llm_healthy = False
            self._llm_error = "llm_client_unavailable"
            return False
        try:
            import asyncio as _asyncio
            def _ping():
                result = client.complete_json(
                    system_prompt="Reply with exactly: {\"ok\": true}",
                    user_payload={"ping": True},
                    task="health_check",
                )
                return isinstance(result, dict) and result.get("ok") is True
            ok = await _asyncio.to_thread(_ping)
            self._llm_healthy = ok
            if not ok:
                self._llm_error = "health_check_returned_false"
            return ok
        except Exception as exc:
            self._llm_healthy = False
            self._llm_error = f"{type(exc).__name__}: {str(exc)}"[:240]
            logger.warning(
                "LLM health check failed for model=%s: %s",
                self._llm_model,
                exc,
            )
            return False

    async def llm_health(self):
        """Return LLM status (verified at startup, not continuously monitored)."""
        return {
            "healthy": self._llm_healthy,
            "model": self._llm_model,
            "error": self._llm_error,
        }

    def _build_compression_pipeline(self):
        """Build ChroniclePipeline — LLM-first with explicit degraded fallback.

        When LLM is healthy: uses cached LLM extraction + LLM scholar.
        When LLM is degraded: falls back to heuristic (keyword-based).
        Caller should check self._llm_healthy to decide whether to proceed.

        LLM credentials come from ``_resolve_mem_llm_client`` (i.e.
        ``memory.llm.*`` in voidcube config) — the same source the rest
        of Mem uses, so the model cannot drift between Tier 2
        compression, escalation, and purge review.
        """
        from memai.pipeline import ChroniclePipeline

        if not self._llm_healthy:
            logger.warning("LLM unhealthy — using heuristic compression (degraded mode)")
            return ChroniclePipeline()

        from systems.memory.llm_extraction import build_llm_first_pipeline

        return build_llm_first_pipeline(self._db_path, role="extraction")

    async def tier1_stats(
        self,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ):
        """Return storage statistics visible to one memory scope."""
        scope = MemoryScope.create(owner_id, workspace_id)
        private = (scope.owner_id, scope.workspace_id)
        conn = open_memory_sqlite(self._db_path)
        total_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE owner_id = ? AND workspace_id = ?",
            private,
        ).fetchone()[0]
        active_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0 "
            "AND owner_id = ? AND workspace_id = ?",
            private,
        ).fetchone()[0]
        compressed_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 1 "
            "AND owner_id = ? AND workspace_id = ?",
            private,
        ).fetchone()[0]
        archived_turns = conn.execute(
            "SELECT COUNT(*) FROM turns_archive WHERE owner_id = ? AND workspace_id = ?",
            private,
        ).fetchone()[0]
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE owner_id = ? AND workspace_id = ?",
            private,
        ).fetchone()[0]
        oldest = conn.execute(
            "SELECT MIN(timestamp) FROM turns WHERE compressed_to_tier2 = 0 "
            "AND owner_id = ? AND workspace_id = ?",
            private,
        ).fetchone()[0]
        visible = (
            scope.owner_id,
            scope.workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
        )
        visible_clause = (
            "((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?))"
        )
        compressed_total = conn.execute(
            f"SELECT COUNT(*) FROM compressed_memories WHERE {visible_clause}",
            visible,
        ).fetchone()[0]
        compressed_events = conn.execute(
            f"SELECT COUNT(*) FROM compressed_memories WHERE memory_type='event' "
            f"AND {visible_clause}",
            visible,
        ).fetchone()[0]
        compressed_scenes = conn.execute(
            f"SELECT COUNT(*) FROM compressed_memories WHERE memory_type='scene' "
            f"AND {visible_clause}",
            visible,
        ).fetchone()[0]
        compressed_arcs = conn.execute(
            f"SELECT COUNT(*) FROM compressed_memories WHERE memory_type='arc' "
            f"AND {visible_clause}",
            visible,
        ).fetchone()[0]
        conn.close()
        return {
            "tier1": {
                "total_turns": total_turns,
                "active_turns": active_turns,
                "compressed_turns": compressed_turns,
                "archived_turns": archived_turns,
                "total_sessions": total_sessions,
                "oldest_active_turn": oldest,
                "retention_days": self.config.tier1_retention_days,
                "max_turns": self.config.tier1_max_turns,
            },
            "tier2": {
                "total_compressed": compressed_total,
                "events": compressed_events,
                "scenes": compressed_scenes,
                "arcs": compressed_arcs,
            },
        }

    # ── Compressed Memories Query ─────────────────────────────────

    async def search_compressed(self, request: dict):
        """Search compressed memories by type, topic, time range, or text.

        Default: excludes superseded and purged entries, sorts by weight DESC.
        Pass include_superseded=true to see historical versions.
        """
        conn = open_memory_sqlite(self._db_path)
        memory_type = request.get("memory_type")  # "event"|"scene"|"arc"|"epoch"
        topic = request.get("topic")
        query_text = request.get("query", "")
        start = request.get("timespan_start")
        end = request.get("timespan_end")
        limit = request.get("limit", 20)
        min_weight = request.get("min_weight", 0.0)
        include_superseded = request.get("include_superseded", False)
        scope = MemoryScope.create(
            request.get("owner_id"),
            request.get("workspace_id"),
        )
        source_domains = _authorized_read_domains(
            request.get("memory_actor") or DEFAULT_MEMORY_ACTOR,
            request.get("source_domains") or None,
        )

        sql = (
            f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE "
            "((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?))"
        )
        params: list = [
            scope.owner_id,
            scope.workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
        ]
        domain_placeholders = ",".join("?" for _ in source_domains)
        sql += f" AND memory_domain IN ({domain_placeholders})"
        params.extend(source_domains)
        # Default: only active entries
        if not include_superseded:
            sql += " AND status = 'active'"
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)
        if topic:
            sql += " AND topics LIKE ?"
            params.append(f"%{topic}%")
        if start:
            sql += " AND timespan_end >= ?"
            params.append(start)
        if end:
            sql += " AND timespan_start <= ?"
            params.append(end)
        if query_text:
            sql += " AND (title LIKE ? OR summary LIKE ?)"
            params.extend([f"%{query_text}%", f"%{query_text}%"])
        if min_weight > 0:
            sql += " AND weight >= ?"
            params.append(min_weight)
        sql += " ORDER BY timespan_start DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        # Track access and compute dynamic weights
        now_iso = datetime.now().isoformat()
        results = []
        for r in rows:
            d = _cmem_row_to_dict(r)
            results.append(d)
        # Sort by dynamic_weight DESC after computing
        results.sort(key=lambda x: x.get("dynamic_weight", 0), reverse=True)
        # Update access_count in background (best-effort)
        try:
            for d in results[:limit]:
                conn.execute(
                    "UPDATE compressed_memories SET access_count = access_count + 1, "
                    "last_accessed_at = ? WHERE memory_id = ? AND "
                    "((owner_id = ? AND workspace_id = ?) OR "
                    "(owner_id = ? AND workspace_id = ?)) "
                    f"AND memory_domain IN ({domain_placeholders})",
                    (
                        now_iso,
                        d["memory_id"],
                        scope.owner_id,
                        scope.workspace_id,
                        GLOBAL_SCOPE_ID,
                        GLOBAL_SCOPE_ID,
                        *source_domains,
                    ),
                )
            conn.commit()
        except Exception:
            pass
        conn.close()
        return {
            "results": results,
            "count": len(results),
        }

    async def _recall_promoted_results(
        self,
        *,
        request: RecallRequest,
        scope: MemoryScope,
        target_domains: tuple[str, ...],
        plan,
    ) -> tuple[list[Dict[str, Any]], set[tuple[str, str, str]], int]:
        conn = open_memory_sqlite(self._db_path)
        try:
            promotions = list_memory_promotions(
                conn,
                scope=scope,
                target_domains=target_domains,
                status="active",
                limit=500,
            )
            conn.commit()
        finally:
            conn.close()
        if not promotions:
            return [], set(), 0

        source_domains = tuple(
            dict.fromkeys(item["source_domain"] for item in promotions)
        )
        record_filter: dict[str, list[str]] = {}
        promotions_by_source: dict[
            tuple[str, str, str], list[Dict[str, Any]]
        ] = {}
        for promotion in promotions:
            source_type = str(promotion["source_type"])
            source_id = str(promotion["source_memory_id"])
            source_domain = str(promotion["source_domain"])
            record_filter.setdefault(source_type, []).append(source_id)
            promotions_by_source.setdefault(
                (source_type, source_id, source_domain), []
            ).append(promotion)

        semantic_matches = await asyncio.to_thread(
            self._semantic_index.search,
            request.query,
            owner_id=scope.owner_id,
            workspace_id=scope.workspace_id,
            source_domains=source_domains,
            limit=max(self.config.recall_candidate_limit, len(promotions)),
        )
        conn = open_memory_sqlite(self._db_path)
        try:
            source_payload = recall_memories(
                conn,
                plan,
                limit=min(50, max(request.limit or self.config.recall_default_limit, len(promotions))),
                candidate_limit=max(self.config.recall_candidate_limit, len(promotions)),
                max_context_chars=(
                    request.max_context_chars or self.config.recall_max_context_chars
                ),
                min_score=(
                    request.min_score
                    if request.min_score is not None
                    else self.config.recall_min_score
                ),
                current_session_id=request.current_session_id,
                include_tier1=request.include_tier1,
                include_tier2=request.include_tier2,
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                source_domains=source_domains,
                semantic_matches=semantic_matches,
                record_filter=record_filter,
            )
        finally:
            conn.close()

        projected: list[Dict[str, Any]] = []
        projected_source_keys: set[tuple[str, str, str]] = set()
        for source in source_payload["results"]:
            source_key = promotion_source_key(source)
            for promotion in promotions_by_source.get(source_key, []):
                projected_source_keys.add(source_key)
                item = dict(source)
                source_id = str(item.get("id") or "")
                source_domain = str(item.get("memory_domain") or "")
                promotion_id = str(promotion["promotion_id"])
                item.update(
                    {
                        "id": promotion_id,
                        "memory_domain": promotion["target_domain"],
                        "promotion_ref_id": promotion_id,
                        "source_memory_id": source_id,
                        "source_memory_type": promotion["source_type"],
                        "source_memory_domain": source_domain,
                        "promotion_reason": promotion["reason"],
                        "promotion_approved_by": promotion["approved_by"],
                        "promotion_approval_ref": promotion["approval_ref"],
                        "promotion_expires_at": promotion["expires_at"],
                        "score": round(float(item.get("score") or 0.0) * 0.98, 6),
                    }
                )
                item["evidence_refs"] = list(
                    dict.fromkeys(
                        [
                            *(item.get("evidence_refs") or []),
                            f"promotion:{promotion_id}",
                        ]
                    )
                )
                item["signals"] = {
                    **dict(item.get("signals") or {}),
                    "promotion_reference": True,
                }
                projected.append(item)
        return projected, projected_source_keys, int(
            source_payload.get("candidate_count") or 0
        )

    async def recall(self, request: RecallRequest):
        """Recall a bounded mix of recent turns and durable Tier 2 memory."""
        if not request.query.strip():
            raise HTTPException(status_code=400, detail="query is required")
        if not request.include_tier1 and not request.include_tier2:
            raise HTTPException(
                status_code=400,
                detail="at least one memory tier must be enabled",
            )

        trace_id = str(uuid.uuid4())
        created_at = datetime.now().astimezone().isoformat()
        started = time.perf_counter()
        self._recall_requests += 1
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        source_domains = _authorized_read_domains(
            request.memory_actor, request.source_domains
        )
        plan = None
        payload: Dict[str, Any] | None = None
        failure: Exception | None = None
        try:
            plan = build_recall_plan(
                request.query,
                memory_type=request.memory_type,
                topic=request.topic,
                timespan_start=request.timespan_start,
                timespan_end=request.timespan_end,
                as_of=request.as_of,
            )
            semantic_matches = await asyncio.to_thread(
                self._semantic_index.search,
                request.query,
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                source_domains=source_domains,
                limit=self.config.recall_candidate_limit,
            )
            conn = open_memory_sqlite(self._db_path)
            try:
                payload = recall_memories(
                    conn,
                    plan,
                    limit=request.limit or self.config.recall_default_limit,
                    candidate_limit=self.config.recall_candidate_limit,
                    max_context_chars=(
                        request.max_context_chars
                        or self.config.recall_max_context_chars
                    ),
                    min_score=(
                        request.min_score
                        if request.min_score is not None
                        else self.config.recall_min_score
                    ),
                    current_session_id=request.current_session_id,
                    include_tier1=request.include_tier1,
                    include_tier2=request.include_tier2,
                    owner_id=scope.owner_id,
                    workspace_id=scope.workspace_id,
                    source_domains=source_domains,
                    semantic_matches=semantic_matches,
                )
            finally:
                conn.close()
            payload["promotion_count"] = 0
            if request.include_promotions:
                promoted, promoted_source_keys, promoted_candidate_count = (
                    await self._recall_promoted_results(
                        request=request,
                        scope=scope,
                        target_domains=source_domains,
                        plan=plan,
                    )
                )
                payload["candidate_count"] = int(
                    payload.get("candidate_count") or 0
                ) + promoted_candidate_count
                if promoted:
                    native_results = [
                        item
                        for item in payload["results"]
                        if promotion_source_key(item) not in promoted_source_keys
                    ]
                    merged = merge_recall_results(
                        [native_results, promoted],
                        limit=request.limit or self.config.recall_default_limit,
                        max_context_chars=(
                            request.max_context_chars
                            or self.config.recall_max_context_chars
                        ),
                        per_session_limit=(
                            request.limit or self.config.recall_default_limit
                            if plan.intent == "recent_conversation"
                            else 2
                        ),
                    )
                    merged["truncated"] = bool(
                        payload.get("truncated") or merged.get("truncated")
                    )
                    payload.update(merged)
                payload["promotion_count"] = sum(
                    1
                    for item in payload["results"]
                    if item.get("promotion_ref_id")
                )
            payload["context"] = format_recall_context(payload["results"])
            payload["trace_id"] = trace_id
            payload["recall_status"] = "hit" if payload["count"] else "empty"
            payload["request_source"] = request.request_source
            payload["source_domains"] = list(source_domains)
            self._last_recall_count = int(payload["count"])
            if self._last_recall_count:
                self._recall_hits += 1
            return payload
        except Exception as exc:
            failure = exc
            self._recall_failures += 1
            raise
        finally:
            self._last_recall_at = datetime.now().astimezone().isoformat()
            self._last_recall_latency_ms = round(
                (time.perf_counter() - started) * 1000,
                3,
            )
            self._last_recall_trace_id = trace_id
            self._last_recall_status = (
                "failure"
                if failure is not None
                else ("hit" if payload and payload.get("count") else "empty")
            )
            self._persist_recall_trace(
                trace_id=trace_id,
                created_at=created_at,
                request=request,
                plan=plan.as_dict() if plan is not None else None,
                payload=payload,
                latency_ms=self._last_recall_latency_ms,
                failure=failure,
            )

    def _persist_recall_trace(
        self,
        *,
        trace_id: str,
        created_at: str,
        request: RecallRequest,
        plan: Dict[str, Any] | None,
        payload: Dict[str, Any] | None,
        latency_ms: float,
        failure: Exception | None,
    ) -> None:
        selected = []
        for item in list((payload or {}).get("results") or []):
            selected.append(
                {
                    "id": item.get("id"),
                    "tier": item.get("tier"),
                    "score": item.get("score"),
                    "matched_terms": item.get("matched_terms") or [],
                    "source_turns": item.get("source_turns") or [],
                    "evidence_refs": item.get("evidence_refs") or [],
                    "memory_domain": item.get("memory_domain"),
                    "promotion_ref_id": item.get("promotion_ref_id"),
                    "source_memory_id": item.get("source_memory_id"),
                    "source_memory_type": item.get("source_memory_type"),
                    "source_memory_domain": item.get("source_memory_domain"),
                    "promotion_approved_by": item.get("promotion_approved_by"),
                    "promotion_approval_ref": item.get("promotion_approval_ref"),
                }
            )
        status = (
            "failure"
            if failure is not None
            else ("hit" if payload and payload.get("count") else "empty")
        )
        conn = open_memory_sqlite(self._db_path)
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        source_domains = _authorized_read_domains(
            request.memory_actor, request.source_domains
        )
        try:
            conn.execute(
                "INSERT INTO recall_traces "
                "(trace_id, created_at, completed_at, request_source, session_id, "
                "query, status, intent, query_plan, candidate_count, result_count, "
                "selected_results, context_chars, latency_ms, error_type, error_detail, "
                "memory_actor, owner_id, workspace_id, source_domains) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    created_at,
                    datetime.now().astimezone().isoformat(),
                    request.request_source,
                    request.current_session_id,
                    _redact_for_memory_storage(request.query),
                    status,
                    (plan or {}).get("intent"),
                    json.dumps(
                        _redact_for_memory_storage(plan or {}),
                        ensure_ascii=False,
                    ),
                    int((payload or {}).get("candidate_count") or 0),
                    int((payload or {}).get("count") or 0),
                    json.dumps(selected, ensure_ascii=False),
                    int((payload or {}).get("context_chars") or 0),
                    latency_ms,
                    type(failure).__name__ if failure is not None else None,
                    (
                        str(_redact_for_memory_storage(str(failure)))[:500]
                        if failure is not None
                        else None
                    ),
                    request.memory_actor.value,
                    scope.owner_id,
                    scope.workspace_id,
                    json.dumps(source_domains),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.warning("Failed to persist recall trace %s", trace_id, exc_info=True)
        finally:
            conn.close()

    async def list_recall_traces(
        self,
        limit: int = 50,
        session_id: Optional[str] = None,
        status: Optional[str] = None,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
    ):
        bounded_limit = max(1, min(int(limit), 500))
        memory_actor = MemoryActor(memory_actor)
        scope = MemoryScope.create(owner_id, workspace_id)
        clauses = ["owner_id = ?", "workspace_id = ?", "memory_actor = ?"]
        params: list[Any] = [scope.owner_id, scope.workspace_id, memory_actor.value]
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        params.append(bounded_limit)
        conn = open_memory_sqlite(self._db_path)
        try:
            rows = conn.execute(
                "SELECT trace_id, created_at, completed_at, request_source, "
                "session_id, query, status, intent, query_plan, candidate_count, "
                "result_count, selected_results, context_chars, latency_ms, "
                "error_type, error_detail FROM recall_traces WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        traces = []
        for row in rows:
            traces.append(
                {
                    "trace_id": row[0],
                    "created_at": row[1],
                    "completed_at": row[2],
                    "request_source": row[3],
                    "session_id": row[4],
                    "query": row[5],
                    "status": row[6],
                    "intent": row[7],
                    "query_plan": json.loads(row[8] or "{}"),
                    "candidate_count": row[9],
                    "result_count": row[10],
                    "selected_results": json.loads(row[11] or "[]"),
                    "context_chars": row[12],
                    "latency_ms": row[13],
                    "error_type": row[14],
                    "error_detail": row[15],
                }
            )
        return {"traces": traces, "count": len(traces)}

    async def record_recall_feedback(self, request: RecallFeedbackCreate):
        """Record scoped user feedback for one actually selected recall item."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        conn = open_memory_sqlite(self._db_path)
        try:
            row = conn.execute(
                "SELECT selected_results, source_domains FROM recall_traces "
                "WHERE trace_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_actor = ?",
                (
                    request.trace_id,
                    scope.owner_id,
                    scope.workspace_id,
                    request.memory_actor.value,
                ),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recall trace not found")
            selected_ids = {
                str(item.get("id") or "")
                for item in json.loads(row[0] or "[]")
                if isinstance(item, dict)
            }
            if request.memory_id not in selected_ids:
                raise HTTPException(
                    status_code=409,
                    detail="Feedback can only reference a selected recall result",
                )
            selected_item = next(
                item
                for item in json.loads(row[0] or "[]")
                if isinstance(item, dict) and str(item.get("id") or "") == request.memory_id
            )
            memory_domain = str(
                selected_item.get("memory_domain") or DEFAULT_MEMORY_DOMAIN.value
            )
            _authorized_read_domains(request.memory_actor, [MemoryDomain(memory_domain)])
            feedback_id = "feedback-" + hashlib.sha256(
                (
                    f"{request.trace_id}\0{request.memory_id}\0"
                    f"{scope.owner_id}\0{scope.workspace_id}"
                ).encode("utf-8")
            ).hexdigest()[:24]
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO recall_feedback "
                "(feedback_id, trace_id, memory_id, verdict, reason, owner_id, "
                "workspace_id, memory_domain, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trace_id, memory_id, owner_id, workspace_id) DO UPDATE SET "
                "verdict = excluded.verdict, reason = excluded.reason, "
                "memory_domain = excluded.memory_domain, "
                "created_at = excluded.created_at",
                (
                    feedback_id,
                    request.trace_id,
                    request.memory_id,
                    request.verdict,
                    str(_redact_for_memory_storage(request.reason)).strip(),
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                    now,
                ),
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "status": "recorded",
            "feedback_id": feedback_id,
            "verdict": request.verdict,
            "memory_id": request.memory_id,
            "memory_domain": memory_domain,
            **scope.as_dict(),
        }

    async def forget_memory(self, request: ForgetRequest):
        """Hard-delete one scoped memory or all memory derived from a session."""
        memory_id = str(request.memory_id or "").strip()
        session_id = str(request.session_id or "").strip()
        if bool(memory_id) == bool(session_id):
            raise HTTPException(
                status_code=400,
                detail="Specify exactly one of memory_id or session_id",
            )
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        conn = open_memory_sqlite(self._db_path)
        counts = {
            "compressed_memories": 0,
            "profile_memories": 0,
            "turns": 0,
            "turns_archive": 0,
            "sessions": 0,
            "recall_feedback": 0,
            "recall_traces": 0,
            "recall_trace_references": 0,
            "memory_embeddings": 0,
            "memory_promotions_revoked": 0,
            "memory_promotion_candidates_rejected": 0,
        }
        target_kind = "memory" if memory_id else "session"
        target = memory_id or session_id
        try:
            if memory_id:
                for table in ("compressed_memories", "profile_memories"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE memory_id = ? "
                        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                        (memory_id, scope.owner_id, scope.workspace_id, memory_domain),
                    )
                    counts[table] += max(0, int(cursor.rowcount or 0))
                for table in ("turns", "turns_archive"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE turn_id = ? "
                        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                        (memory_id, scope.owner_id, scope.workspace_id, memory_domain),
                    )
                    counts[table] += max(0, int(cursor.rowcount or 0))
                counts["memory_promotions_revoked"] += revoke_promotions_for_source(
                    conn,
                    source_memory_ids=[memory_id],
                    source_domain=memory_domain,
                    scope=scope,
                    revoked_by=request.memory_actor.value,
                )
                counts["memory_promotion_candidates_rejected"] += (
                    reject_promotion_candidates_for_source(
                        conn,
                        source_memory_ids=[memory_id],
                        source_domain=memory_domain,
                        scope=scope,
                    )
                )
                cursor = conn.execute(
                    "DELETE FROM recall_feedback WHERE memory_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (memory_id, scope.owner_id, scope.workspace_id, memory_domain),
                )
                counts["recall_feedback"] += max(0, int(cursor.rowcount or 0))
                trace_rows = conn.execute(
                    "SELECT trace_id, selected_results FROM recall_traces WHERE "
                    "owner_id = ? AND workspace_id = ? AND ((EXISTS (SELECT 1 FROM "
                    "json_each(recall_traces.source_domains) domains WHERE domains.value = ?) "
                    "AND EXISTS (SELECT 1 FROM "
                    "json_each(recall_traces.selected_results) "
                    "WHERE json_extract(value, '$.id') = ?)) OR EXISTS (SELECT 1 FROM "
                    "json_each(recall_traces.selected_results) "
                    "WHERE json_extract(value, '$.source_memory_id') = ?))",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        memory_id,
                        memory_id,
                    ),
                ).fetchall()
                for trace_id, selected_json in trace_rows:
                    selected = [
                        item
                        for item in json.loads(selected_json or "[]")
                        if str(item.get("id") or "") != memory_id
                        and str(item.get("source_memory_id") or "") != memory_id
                    ]
                    conn.execute(
                        "UPDATE recall_traces SET selected_results = ?, result_count = ? "
                        "WHERE trace_id = ? AND owner_id = ? AND workspace_id = ?",
                        (
                            json.dumps(selected, ensure_ascii=False),
                            len(selected),
                            trace_id,
                            scope.owner_id,
                            scope.workspace_id,
                        ),
                    )
                counts["recall_trace_references"] += len(trace_rows)
                cursor = conn.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (memory_id, scope.owner_id, scope.workspace_id, memory_domain),
                )
                counts["memory_embeddings"] += max(0, int(cursor.rowcount or 0))
            else:
                turn_rows = conn.execute(
                    "SELECT turn_id FROM turns WHERE session_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ? UNION SELECT turn_id FROM turns_archive "
                    "WHERE session_id = ? AND owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ?",
                    (
                        session_id,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        session_id,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                ).fetchall()
                derived_memory_ids: set[str] = set()
                for (turn_id,) in turn_rows:
                    for table in ("compressed_memories", "profile_memories"):
                        derived_memory_ids.update(
                            str(row[0])
                            for row in conn.execute(
                                f"SELECT memory_id FROM {table} WHERE owner_id = ? "
                                "AND workspace_id = ? AND memory_domain = ? AND EXISTS (SELECT 1 FROM "
                                f"json_each({table}.source_turns) WHERE value = ?)",
                                (scope.owner_id, scope.workspace_id, memory_domain, turn_id),
                            ).fetchall()
                        )
                        cursor = conn.execute(
                            f"DELETE FROM {table} WHERE owner_id = ? AND workspace_id = ? "
                            "AND memory_domain = ? AND EXISTS (SELECT 1 FROM "
                            f"json_each({table}.source_turns) WHERE value = ?)",
                            (scope.owner_id, scope.workspace_id, memory_domain, turn_id),
                        )
                        counts[table] += max(0, int(cursor.rowcount or 0))
                for source_type, identifiers in (
                    ("turn", {str(row[0]) for row in turn_rows}),
                    ("archive", {str(row[0]) for row in turn_rows}),
                    ("compressed", derived_memory_ids),
                    ("profile", derived_memory_ids),
                ):
                    if not identifiers:
                        continue
                    placeholders = ",".join("?" for _ in identifiers)
                    cursor = conn.execute(
                        "DELETE FROM memory_embeddings WHERE source_type = ? AND "
                        f"memory_id IN ({placeholders}) AND owner_id = ? AND workspace_id = ? "
                        "AND memory_domain = ?",
                        (
                            source_type,
                            *sorted(identifiers),
                            scope.owner_id,
                            scope.workspace_id,
                            memory_domain,
                        ),
                    )
                    counts["memory_embeddings"] += max(0, int(cursor.rowcount or 0))
                promotion_source_ids = {
                    *(str(row[0]) for row in turn_rows),
                    *derived_memory_ids,
                }
                counts["memory_promotions_revoked"] += revoke_promotions_for_source(
                    conn,
                    source_memory_ids=sorted(promotion_source_ids),
                    source_domain=memory_domain,
                    scope=scope,
                    revoked_by=request.memory_actor.value,
                )
                counts["memory_promotion_candidates_rejected"] += (
                    reject_promotion_candidates_for_source(
                        conn,
                        source_memory_ids=sorted(promotion_source_ids),
                        source_domain=memory_domain,
                        scope=scope,
                    )
                )
                cursor = conn.execute(
                    "DELETE FROM recall_feedback WHERE memory_domain = ? AND trace_id IN ("
                    "SELECT trace_id FROM recall_traces WHERE session_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_actor = ? "
                    "AND EXISTS (SELECT 1 FROM "
                    "json_each(recall_traces.source_domains) WHERE value = ?))",
                    (
                        memory_domain,
                        session_id,
                        scope.owner_id,
                        scope.workspace_id,
                        request.memory_actor.value,
                        memory_domain,
                    ),
                )
                counts["recall_feedback"] += max(0, int(cursor.rowcount or 0))
                cursor = conn.execute(
                    "DELETE FROM recall_traces WHERE session_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND EXISTS (SELECT 1 FROM "
                    "json_each(recall_traces.source_domains) WHERE value = ?)",
                    (session_id, scope.owner_id, scope.workspace_id, memory_domain),
                )
                counts["recall_traces"] += max(0, int(cursor.rowcount or 0))
                for table in ("turns", "turns_archive"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE session_id = ? "
                        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                        (session_id, scope.owner_id, scope.workspace_id, memory_domain),
                    )
                    counts[table] += max(0, int(cursor.rowcount or 0))
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE session_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ?",
                    (session_id, scope.owner_id, scope.workspace_id, memory_domain),
                )
                counts["sessions"] += max(0, int(cursor.rowcount or 0))
            deleted_total = sum(counts.values())
            if deleted_total == 0:
                raise HTTPException(status_code=404, detail="Scoped memory target not found")
            audit_id = f"forget-{uuid.uuid4()}"
            conn.execute(
                "INSERT INTO memory_deletion_audit "
                "(audit_id, memory_domain, target_kind, target_hash, reason, deleted_counts, owner_id, "
                "workspace_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    audit_id,
                    memory_domain,
                    target_kind,
                    hashlib.sha256(target.encode("utf-8")).hexdigest(),
                    str(_redact_for_memory_storage(request.reason)).strip(),
                    json.dumps(counts, sort_keys=True),
                    scope.owner_id,
                    scope.workspace_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "status": "forgotten",
            "audit_id": audit_id,
            "target_kind": target_kind,
            "memory_domain": memory_domain,
            "deleted_counts": counts,
            **scope.as_dict(),
        }

    async def remember(self, request: DurableMemoryCreate):
        """Persist an explicit durable memory in the canonical Mem store."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        title = str(_redact_for_memory_storage(request.title)).strip()
        summary = str(_redact_for_memory_storage(request.summary)).strip()
        evidence_refs = list(
            dict.fromkeys(str(item).strip() for item in request.evidence_refs)
        )
        evidence_refs = [item for item in evidence_refs if item]
        topics = list(dict.fromkeys(_redact_for_memory_storage(request.topics)))
        entities = list(dict.fromkeys(_redact_for_memory_storage(request.entities)))
        identity = json.dumps(
            {
                "title": title,
                "summary": summary,
                "evidence_refs": evidence_refs,
                "source_actor": request.source_actor.strip(),
                "owner_id": scope.owner_id,
                "workspace_id": scope.workspace_id,
                "memory_domain": memory_domain,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        memory_id = "durable-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        now = datetime.now(timezone.utc).isoformat()
        source_turns = [
            ref.removeprefix("turn:")
            for ref in evidence_refs
            if ref.startswith("turn:") and ref.removeprefix("turn:")
        ]
        conn = open_memory_sqlite(self._db_path)
        try:
            conn.execute(
                "INSERT INTO compressed_memories "
                "(memory_id, memory_domain, memory_type, title, summary, timespan_start, timespan_end, "
                "importance, confidence, topics, entities, source_turns, compressed_at, "
                "compression_level, status, weight, event_kind, pinned, hidden, "
                "evidence_refs, origin_type, origin_id, verified_at, owner_id, workspace_id, created_at) "
                "VALUES (?, ?, 'event', ?, ?, ?, ?, ?, 0.9, ?, ?, ?, ?, 0, 'active', "
                "0.8, ?, 0, 0, ?, 'agent_explicit_memory', ?, ?, ?, ?, ?) "
                "ON CONFLICT(memory_id) DO UPDATE SET "
                "title = excluded.title, summary = excluded.summary, "
                "importance = excluded.importance, topics = excluded.topics, "
                "entities = excluded.entities, source_turns = excluded.source_turns, "
                "event_kind = excluded.event_kind, evidence_refs = excluded.evidence_refs, "
                "status = 'active', hidden = 0",
                (
                    memory_id,
                    memory_domain,
                    title,
                    summary,
                    now,
                    now,
                    request.importance,
                    json.dumps(topics, ensure_ascii=False),
                    json.dumps(entities, ensure_ascii=False),
                    json.dumps(source_turns, ensure_ascii=False),
                    now,
                    request.event_kind.strip(),
                    json.dumps(evidence_refs, ensure_ascii=False),
                    f"{request.source_actor.strip()}:{memory_id}",
                    now,
                    scope.owner_id,
                    scope.workspace_id,
                    now,
                ),
            )
            from systems.memory.entity_graph import update_entity_graph

            update_entity_graph(
                conn,
                memory_id=memory_id,
                memory_type="event",
                entities=entities,
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                memory_domain=memory_domain,
                now=now,
            )
            conn.commit()
            row = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE memory_id = ? "
                "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, memory_domain),
            ).fetchone()
        finally:
            conn.close()
        self._semantic_wake.set()
        return {
            "status": "remembered",
            "memory": _cmem_row_to_dict(row),
        }

    async def get_compressed(
        self,
        memory_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Get a single compressed memory by ID."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        row = conn.execute(
            f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE memory_id = ? AND "
            "((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?)) AND memory_domain = ?",
            (
                memory_id,
                scope.owner_id,
                scope.workspace_id,
                GLOBAL_SCOPE_ID,
                GLOBAL_SCOPE_ID,
                authorized_domain,
            ),
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Compressed memory not found")
        return _cmem_row_to_dict(row)

    async def trace_compressed_by_turn(
        self,
        turn_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Find all compressed memories that reference a given turn_id."""
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_read_domains(memory_actor, [memory_domain])[0]
        conn = open_memory_sqlite(self._db_path)
        rows = conn.execute(
            "SELECT c.* FROM compressed_memories c WHERE "
            "((c.owner_id = ? AND c.workspace_id = ?) OR "
            "(c.owner_id = ? AND c.workspace_id = ?)) "
            "AND c.memory_domain = ? "
            "AND EXISTS (SELECT 1 FROM json_each(c.source_turns) WHERE value = ?)",
            (
                scope.owner_id,
                scope.workspace_id,
                GLOBAL_SCOPE_ID,
                GLOBAL_SCOPE_ID,
                authorized_domain,
                turn_id,
            ),
        ).fetchall()
        conn.close()
        return {
            "turn_id": turn_id,
            "compressed_memories": [_cmem_row_to_dict(r) for r in rows],
            "count": len(rows),
        }

    async def trigger_lifecycle(self, request: dict = None):
        """Manually trigger compression lifecycle (escalation + purge)."""
        req = request or {}
        result = {}
        if req.get("escalate", True):
            result["escalation"] = await self._apply_compression_lifecycle()
        if req.get("purge", True):
            result["purge"] = {"deleted": await self._purge_expired_memories()}
        return {"status": "ok", **result}

    # ── User Feedback: Pin / Hide ──────────────────────────────────

    @staticmethod
    def _reject_founding_identity_mutation(memory_id: str) -> None:
        from systems.memory.identity_seed import is_founding_memory_id

        if is_founding_memory_id(memory_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Founding identity is canonical and read-only; submit an "
                    "identity revision proposal with evidence"
                ),
            )

    async def pin_memory(
        self,
        memory_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Pin a memory: lock weight at 1.0, immune to decay/escalation."""
        self._reject_founding_identity_mutation(memory_id)
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_write_domain(memory_actor, memory_domain)
        conn = open_memory_sqlite(self._db_path)
        cur = conn.execute(
            "UPDATE compressed_memories SET pinned = 1, hidden = 0, "
            "weight = 1.0 WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
            "AND memory_domain = ?",
            (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
        )
        if cur.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        conn.commit()
        conn.close()
        return {"memory_id": memory_id, "pinned": True, "status": "ok"}

    async def hide_memory(
        self,
        memory_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Hide a memory: weight = 0.0, excluded from default queries."""
        self._reject_founding_identity_mutation(memory_id)
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_write_domain(memory_actor, memory_domain)
        conn = open_memory_sqlite(self._db_path)
        cur = conn.execute(
            "UPDATE compressed_memories SET hidden = 1, pinned = 0, "
            "weight = 0.0 WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
            "AND memory_domain = ?",
            (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
        )
        if cur.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        conn.commit()
        conn.close()
        return {"memory_id": memory_id, "hidden": True, "status": "ok"}

    async def unpin_memory(
        self,
        memory_id: str,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
        memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN,
    ):
        """Remove pin/hide: restore to normal dynamic weight."""
        self._reject_founding_identity_mutation(memory_id)
        scope = MemoryScope.create(owner_id, workspace_id)
        authorized_domain = _authorized_write_domain(memory_actor, memory_domain)
        conn = open_memory_sqlite(self._db_path)
        row = conn.execute(
            "SELECT memory_type, compression_level FROM compressed_memories "
            "WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
            "AND memory_domain = ?",
            (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
        ).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        mem_type, level = row[0], row[1] or 0
        base_w = self._LEVEL_WEIGHT.get(level, 0.2)
        conn.execute(
            "UPDATE compressed_memories SET pinned = 0, hidden = 0, "
            "weight = ? WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
            "AND memory_domain = ?",
            (base_w, memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
        )
        conn.commit()
        conn.close()
        return {"memory_id": memory_id, "pinned": False, "hidden": False, "base_weight": base_w, "status": "ok"}

    async def register_with_gateway(self, *, max_retries: int = 5):
        url = f"{self.config.gateway_address}/register"
        payload = {
            "service_name": "memory-service",
            "service_type": "memory",
            "address": f"http://{self.config.host}:{self.config.port}",
            "health_endpoint": "/",
            "metadata": {"version": "1.0"},
        }

        base_delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    request_kwargs: Dict[str, Any] = {
                        "json": payload,
                        "timeout": 10,
                    }
                    gateway_token = str(
                        os.getenv("GATEWAY_AUTH_TOKEN") or ""
                    ).strip()
                    if gateway_token:
                        request_kwargs["headers"] = {
                            "Authorization": f"Bearer {gateway_token}"
                        }
                    async with session.post(url, **request_kwargs) as response:
                        if response.status == 201:
                            result = await response.json()
                            logger.info(
                                "Registered with gateway (attempt %d): %s",
                                attempt,
                                {
                                    "service_id": result.get("service_id"),
                                    "status": result.get("status"),
                                },
                            )
                            self._gateway_service_id = result["service_id"]
                            self._gateway_registration_healthy = True
                            self._last_gateway_registration_check_at = (
                                datetime.now().isoformat()
                            )
                            return self._gateway_service_id
                        else:
                            logger.debug(
                                "Gateway registration attempt %d returned status %d",
                                attempt,
                                response.status,
                            )
            except Exception as e:
                logger.debug("Gateway registration attempt %d failed: %s", attempt, e)

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.info(
                    "Waiting %.1fs before retrying gateway registration (attempt %d/%d)...",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)

        self._gateway_registration_healthy = False
        self._last_gateway_registration_check_at = datetime.now().isoformat()
        logger.warning("Failed to register with gateway after %d attempts", max_retries)
        return None

class MemoryService(MemoryApplicationService):
    """HTTP composition root for the independently testable Memory use cases."""

    def __init__(self, config: MemoryServiceConfig = None):
        super().__init__(config)
        self.app = build_memory_http_app(
            self._http_handlers(),
            lifespan=asynccontextmanager(self._app_lifespan),
        )

    async def start(self) -> None:
        import uvicorn

        logger.info(
            "Starting memory service on %s:%s",
            self.config.host,
            self.config.port,
        )
        await uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=self.config.host,
                port=self.config.port,
                log_level="info",
            )
        ).serve()


if __name__ == "__main__":
    import argparse
    from VoidCube_core.runtime_paths import get_runtime_layout
    
    parser = argparse.ArgumentParser(description="VoidCube Memory Service")
    parser.add_argument("--host", default="127.0.0.1", help="Service host")
    parser.add_argument("--port", type=int, default=6001, help="Service port")
    parser.add_argument(
        "--db-path",
        default=str(get_runtime_layout().memory_db),
        help="SQLite database path",
    )
    parser.add_argument("--gateway", default="http://127.0.0.1:6000", help="Gateway address")
    args = parser.parse_args()
    
    config = MemoryServiceConfig(
        host=args.host,
        port=args.port,
        db_path=args.db_path,
        gateway_address=args.gateway
    )
    service = MemoryService(config)
    
    import asyncio
    asyncio.run(service.start())
