import asyncio
from contextlib import asynccontextmanager
from functools import partial
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from memai.redaction import redact_sensitive_text
from memai.repository.backup import MemoryRestoreError
from memai.application.config import MemoryServiceConfig
from memai.domain.domain import (
    DEFAULT_MEMORY_ACTOR,
    DEFAULT_MEMORY_DOMAIN,
    MemoryActor,
    MemoryDomain,
    MemoryDomainAccessError,
    authorize_identity_experience_verification,
    authorize_read,
    authorize_write,
    domain_values,
)
from memai.application.profile_capture import (
    ALL_PROFILE_PREDICATES,
    capture_explicit_user_profile,
)
from memai.repository.profile_store import (
    revoke_profile_predicates,
    upsert_profile_memory,
)
from memai.domain.ranking_policy import compute_dynamic_weight
from memai.domain.resource_contract import TurnCompressionStatus
from memai.application.promotion import (
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
from memai.application.recall import (
    build_recall_plan,
    format_recall_context,
    merge_recall_results,
    recall_memories,
)
from memai.repository.contracts import MemoryRepository
from memai.repository.sqlite_repository import (
    IdempotentWriteResult,
    MemoryWriteBackpressure,
    MemoryWriteReceiptConflict,
    SQLiteMemoryRepository,
)
from memai.transport.http_adapter import build_memory_http_app
from memai.application.maintenance import run_tier1_decay_cycle, run_tier2_bridge_cycle
from memai.domain.lifecycle_policy import (
    evaluate_lifecycle_quality,
    lifecycle_age_thresholds,
    record_lifecycle_rejection,
)
from memai.application.maintenance_schedule import (
    claim_rule_execution,
    get_rule_state,
    record_rule_result,
)
from memai.domain.scope import (
    DEFAULT_OWNER_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_SCOPE_ID,
    MemoryScope,
)
from memai.indexes.semantic_index import SemanticMemoryIndex
from memai.application.tier1_to_tier2_bridge import (
    Tier1ToTier2Bridge,
    _parse_utc_timestamp,
)
from memai.domain.time_summary import (
    DaySnapshotChanged,
    SessionSnapshotChanged,
    day_bucket_for_timestamp,
    day_source_hash,
    normalize_day_summary,
    normalize_session_summary,
    session_source_hash,
)
from memai.indexes.timeline import (
    get_active_day_summary,
    get_active_session_summary,
    load_day_session_summaries,
    load_session_turns,
    persist_day_summary,
    persist_session_summary,
    supersede_empty_day_summary,
)

logger = logging.getLogger("memory_service")

_CMEM_COLUMNS = (
    "memory_id, memory_type, title, summary, timespan_start, timespan_end, "
    "importance, confidence, topics, entities, source_turns, timeline_parent_id, "
    "derived_from_id, "
    "compressed_at, compression_level, status, superseded_by, weight, event_kind, "
    "access_count, last_accessed_at, citation_count, pinned, hidden, identity_layer, "
    "evidence_refs, origin_type, origin_id, verified_at, owner_id, workspace_id, "
    "memory_domain, created_at, lifecycle_retry_count, lifecycle_retry_after, "
    "lifecycle_last_error, identity_metadata"
)

_IDENTITY_VERIFICATION_METADATA_KEYS = frozenset(
    {
        "identity_experience",
        "verified",
        "self_authored_identity",
        "verified_by",
        "verified_at",
        "self_claim",
        "what_changed",
        "continuity_impact",
        "agency",
        "identity_title",
        "identity_summary",
        "evidence_refs",
    }
)


def _strip_identity_verification_metadata(value: Any) -> dict[str, Any]:
    """Keep reserved identity-verification fields service-owned."""
    metadata = dict(value) if isinstance(value, dict) else {}
    return {
        key: item
        for key, item in metadata.items()
        if key not in _IDENTITY_VERIFICATION_METADATA_KEYS
    }


def _prepare_memory_storage_value(value: Any, *, redact: bool) -> Any:
    """Prepare a nested value for Memory persistence under the active policy."""
    if not redact:
        return value
    if isinstance(value, str):
        return redact_sensitive_text(value, force=True)
    if isinstance(value, dict):
        return {
            str(key): _prepare_memory_storage_value(item, redact=True)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_prepare_memory_storage_value(item, redact=True) for item in value]
    if isinstance(value, tuple):
        return tuple(_prepare_memory_storage_value(item, redact=True) for item in value)
    return value


def _authorized_write_domain(
    actor: MemoryActor | str,
    domain: MemoryDomain | str,
) -> str:
    try:
        return authorize_write(actor, domain).value
    except (MemoryDomainAccessError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _authorized_identity_experience_domain(
    actor: MemoryActor | str,
    domain: MemoryDomain | str,
) -> str:
    try:
        return authorize_identity_experience_verification(actor, domain).value
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


class SessionCloseRequest(BaseModel):
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN


class DayAggregateRequest(SessionCloseRequest):
    """Scope and authorization for rebuilding one natural-day index."""


class TurnCreate(BaseModel):
    speaker: str  # "user" | "agent" | "system"
    text: str
    tags: List[str] = Field(default_factory=list)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TurnPairCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=300)
    user_content: str = Field(min_length=1)
    assistant_content: str = ""
    tags: List[str] = Field(default_factory=list)
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
    compression_status: TurnCompressionStatus = TurnCompressionStatus.PENDING
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


class TimelineQuery(BaseModel):
    date: date
    session_id: Optional[str] = None
    speaker: Optional[str] = None
    limit: int = 100
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    source_domains: List[MemoryDomain] = Field(default_factory=list)


class Tier2CompressRequest(BaseModel):
    retention_days: int = 30
    batch_size: int = 25
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
    min_revision: Optional[int] = Field(default=None, ge=0)


class AgentOutboxHealthReport(BaseModel):
    session_id: str = Field(min_length=1, max_length=300)
    outbox_id: str = Field(min_length=1, max_length=128)
    queue_name: str = Field(default="api_a", min_length=1, max_length=64)
    memory_domain: str = Field(default="agent_interaction", min_length=1, max_length=64)
    pending_count: int = Field(ge=0)
    inflight_count: int = Field(default=0, ge=0)
    dead_letter_count: int = Field(ge=0)
    oldest_pending_at: Optional[str] = Field(default=None, max_length=100)
    oldest_failure_at: Optional[str] = Field(default=None, max_length=100)
    last_success_at: Optional[str] = Field(default=None, max_length=100)
    last_error: Optional[str] = Field(default=None, max_length=500)
    max_attempts: int = Field(ge=1, le=1000)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR


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


class SelfIdentityExperienceCreate(BaseModel):
    """A first-person identity experience authored by 星子."""

    turn_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_refs: List[str] = Field(min_length=1, max_length=50)
    self_claim: str = Field(min_length=1, max_length=4000)
    what_changed: str = Field(min_length=1, max_length=2000)
    continuity_impact: str = Field(min_length=1, max_length=2000)
    agency: str = Field(pattern=r"^(chosen|accepted|observed|imposed)$")
    topics: List[str] = Field(default_factory=list, max_length=20)
    entities: List[str] = Field(default_factory=list, max_length=20)
    event_kind: str = Field(default="decision", min_length=1, max_length=50)
    importance: float = Field(default=0.9, ge=0.0, le=1.0)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = MemoryActor.STELLAR_COMPANION
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN


class DurableMemoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=4000)
    topics: List[str] = Field(default_factory=list, max_length=30)
    entities: List[str] = Field(default_factory=list, max_length=30)
    evidence_refs: List[str] = Field(default_factory=list, max_length=50)
    supersedes_memory_ids: List[str] = Field(default_factory=list, max_length=50)
    event_kind: str = Field(default="decision", min_length=1, max_length=50)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)
    source_actor: str = Field(default="agent", min_length=1, max_length=100)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR
    memory_domain: MemoryDomain = DEFAULT_MEMORY_DOMAIN
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=300)


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
            "entities": 9, "source_turns": 10, "timeline_parent_id": 11,
            "derived_from_id": 12, "compressed_at": 13, "compression_level": 14,
            "status": 15, "superseded_by": 16, "weight": 17,
            "event_kind": 18, "access_count": 19,
            "last_accessed_at": 20, "citation_count": 21,
            "pinned": 22, "hidden": 23, "identity_layer": 24,
            "evidence_refs": 25, "origin_type": 26,
            "origin_id": 27, "verified_at": 28,
            "owner_id": 29, "workspace_id": 30,
            "memory_domain": 31, "created_at": 32,
            "lifecycle_retry_count": 33, "lifecycle_retry_after": 34,
            "lifecycle_last_error": 35,
            "identity_metadata": 36,
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
        "timeline_parent_id": value("timeline_parent_id"),
        "derived_from_id": value("derived_from_id"),
        "compressed_at": value("compressed_at"),
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
        "lifecycle_retry_count": value("lifecycle_retry_count", 0),
        "lifecycle_retry_after": value("lifecycle_retry_after"),
        "lifecycle_last_error": value("lifecycle_last_error"),
        "identity_metadata": json_value("identity_metadata", {}),
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
        "compression_status": row[9],
        "memory_domain": row[10] if len(row) > 10 else DEFAULT_MEMORY_DOMAIN.value,
    }


def _settle_explicit_profile_capture(
    conn: sqlite3.Connection,
    *,
    text: str,
    turn_id: str,
    timestamp: str,
    scope: MemoryScope,
    memory_domain: str,
    now: str,
) -> dict[str, Any]:
    capture = capture_explicit_user_profile(
        text,
        turn_id=turn_id,
        timestamp=datetime.fromisoformat(timestamp),
    )
    predicates = capture.revoke_predicates
    if predicates == ("*",):
        active_predicates = (
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT predicate FROM profile_memories "
                "WHERE owner_id = ? AND workspace_id = ? AND memory_domain = ? "
                "AND subject = 'user' AND status = 'active'",
                (scope.owner_id, scope.workspace_id, memory_domain),
            ).fetchall()
        )
        predicates = tuple(
            dict.fromkeys((*ALL_PROFILE_PREDICATES, *active_predicates))
        )
    if predicates:
        return revoke_profile_predicates(
            conn,
            predicates,
            owner_id=scope.owner_id,
            workspace_id=scope.workspace_id,
            memory_domain=memory_domain,
            turn_id=turn_id,
            now=now,
        )
    if not capture.profiles:
        return {"action": "none"}
    inserted = sum(
        upsert_profile_memory(
            conn,
            profile,
            owner_id=scope.owner_id,
            workspace_id=scope.workspace_id,
            memory_domain=memory_domain,
            now=now,
            capture_source="explicit_user",
        )
        for profile in capture.profiles
    )
    return {
        "action": "upserted",
        "predicates": [profile.predicate for profile in capture.profiles],
        "inserted": inserted,
    }


def _json_string_set(raw: Any) -> set[str]:
    if not raw:
        return set()
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def _json_string_list(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _collect_dependent_memory_ids(
    conn: sqlite3.Connection,
    *,
    scope: MemoryScope,
    memory_domain: str,
    seed_references: set[str],
    direct_memory_ids: set[str],
) -> tuple[set[str], set[str]]:
    """Resolve all scoped durable memories that transitively cite the seeds."""
    compressed_rows = conn.execute(
        "SELECT memory_id, source_turns, evidence_refs, timeline_parent_id, "
        "derived_from_id, origin_id "
        "FROM compressed_memories WHERE owner_id = ? AND workspace_id = ? "
        "AND memory_domain = ?",
        (scope.owner_id, scope.workspace_id, memory_domain),
    ).fetchall()
    profile_rows = conn.execute(
        "SELECT memory_id, source_turns, evidence_refs FROM profile_memories "
        "WHERE owner_id = ? AND workspace_id = ? AND memory_domain = ?",
        (scope.owner_id, scope.workspace_id, memory_domain),
    ).fetchall()

    references = set(seed_references)
    compressed_ids: set[str] = set()
    profile_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for (
            memory_id,
            source_turns,
            evidence_refs,
            timeline_parent_id,
            derived_from_id,
            origin_id,
        ) in compressed_rows:
            resolved_id = str(memory_id)
            if resolved_id in compressed_ids:
                continue
            row_references = {
                *_json_string_set(source_turns),
                *_json_string_set(evidence_refs),
                str(timeline_parent_id or "").strip(),
                str(derived_from_id or "").strip(),
                str(origin_id or "").strip(),
            }
            row_references.discard("")
            if resolved_id in direct_memory_ids or row_references & references:
                compressed_ids.add(resolved_id)
                references.add(resolved_id)
                changed = True
        for memory_id, source_turns, evidence_refs in profile_rows:
            resolved_id = str(memory_id)
            if resolved_id in profile_ids:
                continue
            row_references = {
                *_json_string_set(source_turns),
                *_json_string_set(evidence_refs),
            }
            if resolved_id in direct_memory_ids or row_references & references:
                profile_ids.add(resolved_id)
                references.add(resolved_id)
                changed = True
    return compressed_ids, profile_ids


class MemoryApplicationService:
    def __init__(
        self,
        config: MemoryServiceConfig = None,
        *,
        repository: MemoryRepository | None = None,
    ):
        self.config = config or MemoryServiceConfig()
        self._compression_task: asyncio.Task | None = None
        self._gateway_registration_task: asyncio.Task | None = None
        self._semantic_task: asyncio.Task | None = None
        self._maintenance_request_task: asyncio.Task | None = None
        self._semantic_wake = asyncio.Event()
        self._tier2_wake = asyncio.Event()
        self._maintenance_lock = asyncio.Lock()
        self._gateway_service_id: Optional[str] = None
        self._gateway_registration_healthy = False
        self._last_gateway_registration_check_at: Optional[str] = None
        self._repository = repository or SQLiteMemoryRepository(
            self.config.db_path,
            backup_retention_count=self.config.backup_retention_count,
            write_queue_max_size=self.config.memory_write_queue_max_size,
            write_batch_size=self.config.memory_write_batch_size,
            write_batch_wait_ms=self.config.memory_write_batch_wait_ms,
            write_enqueue_timeout_ms=self.config.memory_write_enqueue_timeout_ms,
        )
        self._db_path = self._repository.db_path
        # Rule execution tracking
        self._last_rule_run: Dict[str, str] = {}
        self._last_rule_run_monotonic: Dict[str, float] = {}
        self._rule_run_counts: Dict[str, int] = {}
        self._last_tier2_bridge_result: Dict[str, Any] | None = None
        self._tier2_bridge_state = "idle"
        self._tier2_bridge_consecutive_failures = 0
        self._tier2_bridge_last_failure_reason: str | None = None
        self._tier2_bridge_last_succeeded_at: str | None = None
        self._tier2_bridge_last_trigger_reason: str | None = None
        self._tier2_candidate_health_snapshot_cache_revision = -1
        self._tier2_candidate_health_snapshot_cache: Dict[str, Any] | None = None
        self._maintenance_run_status: Dict[str, Any] = {
            "run_id": None,
            "status": "idle",
            "accepted_at": None,
            "started_at": None,
            "completed_at": None,
            "rules": None,
            "error": None,
        }
        # P0-4 健康信号: last cycle that did real write work (not just "ran").
        self._last_effective_activity_at: Optional[str] = None
        # LLM status (re-verified each compression cycle, recovers after outage)
        self._llm_healthy: bool = False
        self._llm_model: str = ""
        self._llm_error: str = ""
        self._llm_resolution_status: str = ""
        self._llm_resolution_detail: str = ""
        self._last_llm_health_check_at: Optional[str] = None
        self._recall_requests = 0
        self._recall_hits = 0
        self._recall_failures = 0
        self._last_recall_at: Optional[str] = None
        self._last_recall_count = 0
        self._last_recall_latency_ms = 0.0
        self._last_recall_trace_id: Optional[str] = None
        self._last_recall_status: str = "idle"
        self._agent_outbox_reports: Dict[str, Dict[str, Any]] = {}
        self._read_cache: Dict[tuple[Any, ...], tuple[int, Any]] = {}
        self._backup_manager = self._repository.backup_manager
        self._repository.initialize()
        self._semantic_index = SemanticMemoryIndex(
            self._db_path,
            repository=self._repository,
        )

    def _memory_storage_value(self, value: Any) -> Any:
        return _prepare_memory_storage_value(
            value,
            redact=self.config.redact_before_store,
        )

    def _repository_read(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        return self._repository.execute_read(operation)

    def _repository_write(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        return self._repository.execute_write(operation)

    async def _repository_read_async(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        return await self._repository.execute_read_async(operation)

    async def _repository_write_async(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        try:
            result = await self._repository.execute_write_async(operation)
            self._read_cache.clear()
            return result
        except MemoryWriteBackpressure as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "busy",
                    "code": "memory_write_backpressure",
                    "retryable": True,
                },
            ) from exc

    async def _repository_idempotent_write_async(
        self,
        *,
        receipt_key: str,
        operation: str,
        fingerprint: str,
        owner_id: str,
        workspace_id: str,
        memory_domain: str,
        callback: Callable[[sqlite3.Connection], Any],
    ) -> IdempotentWriteResult:
        execute = getattr(self._repository, "execute_idempotent_write_async", None)
        if execute is None:
            value = await self._repository_write_async(callback)
            return IdempotentWriteResult(
                value=value,
                commit_revision=getattr(self._repository, "commit_revision", 0),
            )
        try:
            result = await execute(
                receipt_key=receipt_key,
                operation=operation,
                fingerprint=fingerprint,
                owner_id=owner_id,
                workspace_id=workspace_id,
                memory_domain=memory_domain,
                callback=callback,
            )
            self._read_cache.clear()
            return result
        except MemoryWriteBackpressure as exc:
            raise HTTPException(
                status_code=429,
                detail={"error": "busy", "code": "memory_write_backpressure", "retryable": True},
            ) from exc
        except MemoryWriteReceiptConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "idempotency_conflict", "code": "memory_write_receipt_conflict", "retryable": False},
            ) from exc

    def _cached_read(self, key: tuple[Any, ...]) -> Any | None:
        cached = self._read_cache.get(key)
        if cached is None:
            return None
        revision, value = cached
        if revision != getattr(self._repository, "commit_revision", 0):
            self._read_cache.pop(key, None)
            return None
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    def _store_cached_read(self, key: tuple[Any, ...], value: Any) -> Any:
        revision = getattr(self._repository, "commit_revision", 0)
        if isinstance(value, dict):
            stored = dict(value)
        elif isinstance(value, list):
            stored = list(value)
        else:
            stored = value
        self._read_cache[key] = (revision, stored)
        return value

    def _database_revision_snapshot(self) -> dict[str, Any]:
        stats = getattr(self._repository, "execution_stats", lambda: {})()
        return {
            "commit_revision": getattr(self._repository, "commit_revision", 0),
            **stats,
        }

    # ── Compression Lifecycle ─────────────────────────────────────

    # Weight decay by compression level:
    #   Level 0 (Event,  <30d):   weight = 1.00
    #   Level 1 (Scene,  <180d):  weight = 0.70
    #   Level 2 (Arc,    <365d):  weight = 0.40
    #   Level 3 (Epoch,  <730d):  weight = 0.20
    #   Level 4 (Final,  >=730d): weight = 0.05 → purge candidate
    _LEVEL_WEIGHT = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.05}

    async def _apply_compression_lifecycle(self) -> Dict[str, Any]:
        """Run lifecycle maintenance with model work outside the write lock."""
        now = datetime.now(timezone.utc)

        def read_candidates(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
            rows: list[tuple[Any, ...]] = []
            for (mem_type, level), max_age_days in lifecycle_age_thresholds(self.config):
                cutoff = (now - timedelta(days=max_age_days)).isoformat()
                rows.extend(
                    conn.execute(
                        "SELECT memory_id, title, summary, topics, entities, event_kind, "
                        "timespan_start, timespan_end, importance, confidence, source_turns, "
                        "evidence_refs, origin_type, origin_id, verified_at, "
                        "owner_id, workspace_id, memory_domain, memory_type, compression_level "
                        "FROM compressed_memories "
                        "WHERE memory_type = ? AND compression_level = ? "
                        "AND status = 'active' AND hidden = 0 AND pinned = 0 "
                        "AND identity_layer IS NULL "
                        "AND memory_id NOT LIKE 'identity-founding-%' AND compressed_at < ? "
                        "AND lifecycle_retry_count < ? "
                        "AND (lifecycle_retry_after IS NULL OR lifecycle_retry_after <= ?)"
                        ,
                        (
                            mem_type,
                            level,
                            cutoff,
                            self.config.lifecycle_max_quality_retries,
                            now.isoformat(),
                        ),
                    ).fetchall()
                )
            return rows

        candidates = self._repository_read(read_candidates)
        plans: list[dict[str, Any]] = []
        quality_rejected = 0

        for row in candidates:
            (
                mem_id,
                title,
                summary,
                topics_json,
                entities_json,
                event_kind,
                ts_start,
                ts_end,
                importance,
                confidence,
                source_turns_json,
                evidence_refs_json,
                origin_type,
                origin_id,
                verified_at,
                owner_id,
                workspace_id,
                memory_domain,
                mem_type,
                level,
            ) = row

            if int(level) >= 4:
                should_keep = await self._llm_purge_review(
                    mem_id=str(mem_id),
                    title=str(title),
                    summary=str(summary),
                    topics=_json_string_list(topics_json),
                )
                plans.append(
                    {
                        "kind": "final_keep" if should_keep else "final_purge",
                        "memory_id": str(mem_id),
                        "owner_id": str(owner_id),
                        "workspace_id": str(workspace_id),
                        "memory_domain": str(memory_domain),
                    }
                )
                continue

            next_level = int(level) + 1
            next_type = {0: "scene", 1: "arc", 2: "epoch", 3: "epoch"}[int(level)]
            next_weight = self._LEVEL_WEIGHT.get(next_level, 0.1)
            escalated_title, escalated_summary = await self._llm_escalate_summary(
                mem_id=str(mem_id),
                title=str(title),
                summary=str(summary),
                from_type=str(mem_type),
                from_level=int(level),
                to_type=next_type,
                to_level=next_level,
                topics=_json_string_list(topics_json),
            )
            quality = evaluate_lifecycle_quality(
                source_title=str(title or ""),
                source_summary=str(summary or ""),
                proposed_title=str(escalated_title or ""),
                proposed_summary=str(escalated_summary or ""),
                min_source_support=self.config.lifecycle_min_source_support,
                min_identifier_fidelity=self.config.lifecycle_min_identifier_fidelity,
            )
            if not quality.passed:
                quality_rejected += 1
                plans.append(
                    {
                        "kind": "rejection",
                        "memory_id": str(mem_id),
                        "owner_id": str(owner_id),
                        "workspace_id": str(workspace_id),
                        "memory_domain": str(memory_domain),
                        "reason": ",".join(quality.failed_checks),
                    }
                )
                continue

            plans.append(
                {
                    "kind": "escalate",
                    "memory_id": str(mem_id),
                    "owner_id": str(owner_id),
                    "workspace_id": str(workspace_id),
                    "memory_domain": str(memory_domain),
                    "mem_type": str(mem_type),
                    "next_type": next_type,
                    "next_level": next_level,
                    "next_weight": next_weight,
                    "title": str(escalated_title),
                    "summary": str(escalated_summary),
                    "timespan_start": ts_start,
                    "timespan_end": ts_end,
                    "importance": float(importance),
                    "confidence": float(confidence),
                    "source_turns_json": source_turns_json,
                    "evidence_refs_json": evidence_refs_json,
                    "origin_type": origin_type,
                    "origin_id": origin_id,
                    "verified_at": verified_at,
                    "event_kind": event_kind,
                    "topics_json": topics_json,
                    "entities_json": entities_json,
                }
            )

        def write(conn: sqlite3.Connection) -> Dict[str, Any]:
            escalated = 0
            purged = 0
            graph_scopes: set[tuple[str, str, str]] = set()
            for plan in plans:
                kind = plan["kind"]
                memory_id = plan["memory_id"]
                owner_id = plan["owner_id"]
                workspace_id = plan["workspace_id"]
                memory_domain = plan["memory_domain"]
                if kind == "final_keep":
                    conn.execute(
                        "UPDATE compressed_memories SET compression_level = 3, "
                        "status = 'active', weight = 0.15, compressed_at = ? "
                        "WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                        "AND memory_domain = ?",
                        (now.isoformat(), memory_id, owner_id, workspace_id, memory_domain),
                    )
                    continue
                if kind == "final_purge":
                    conn.execute(
                        "UPDATE compressed_memories SET status = 'purged', "
                        "weight = 0.0, compressed_at = ? WHERE memory_id = ? "
                        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                        (now.isoformat(), memory_id, owner_id, workspace_id, memory_domain),
                    )
                    purged += 1
                    graph_scopes.add((owner_id, workspace_id, memory_domain))
                    continue
                if kind == "rejection":
                    retry_count = record_lifecycle_rejection(
                        conn,
                        memory_id=memory_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        memory_domain=memory_domain,
                        reason=plan["reason"],
                        now=now,
                        max_retries=self.config.lifecycle_max_quality_retries,
                        retry_base_hours=self.config.lifecycle_retry_base_hours,
                    )
                    logger.warning(
                        "Compression lifecycle rejected escalation for %s (attempt ?/%d): %s",
                        memory_id,
                        self.config.lifecycle_max_quality_retries,
                        plan["reason"],
                    )
                    continue
                successor_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "voidcube-memory-lifecycle:"
                        f"{owner_id}:{workspace_id}:{memory_domain}:{memory_id}:{plan['next_level']}",
                    )
                )
                conn.execute(
                    "INSERT OR REPLACE INTO compressed_memories "
                    "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                    "importance, confidence, topics, entities, source_turns, "
                    "evidence_refs, origin_type, origin_id, verified_at, "
                    "derived_from_id, compressed_at, compression_level, status, weight, "
                    "owner_id, workspace_id, memory_domain, event_kind, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        successor_id,
                        plan["next_type"],
                        plan["title"],
                        plan["summary"],
                        plan["timespan_start"],
                        plan["timespan_end"],
                        plan["importance"] * 0.85,
                        plan["confidence"] * 0.9,
                        plan["topics_json"],
                        plan["entities_json"],
                        json.dumps(_json_string_list(plan["source_turns_json"])),
                        plan["evidence_refs_json"],
                        plan["origin_type"],
                        plan["origin_id"],
                        plan["verified_at"],
                        memory_id,
                        now.isoformat(),
                        plan["next_level"],
                        "active",
                        plan["next_weight"],
                        owner_id,
                        workspace_id,
                        memory_domain,
                        plan["event_kind"],
                        now.isoformat(),
                    ),
                )
                conn.execute(
                    "UPDATE compressed_memories SET status = 'superseded', "
                    "superseded_by = ?, weight = weight * 0.3 WHERE memory_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (successor_id, memory_id, owner_id, workspace_id, memory_domain),
                )
                conn.execute(
                    "UPDATE compressed_memories SET citation_count = citation_count + 1 "
                    "WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ?",
                    (successor_id, owner_id, workspace_id, memory_domain),
                )
                graph_scopes.add((owner_id, workspace_id, memory_domain))
                escalated += 1

            if graph_scopes:
                from memai.indexes.entity_graph import rebuild_entity_graph

                for owner_id, workspace_id, memory_domain in sorted(graph_scopes):
                    rebuild_entity_graph(
                        conn,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        memory_domain=memory_domain,
                    )

            conn.execute(
                "UPDATE compressed_memories SET created_at = compressed_at "
                "WHERE created_at IS NULL"
            )
            if escalated or purged:
                logger.info(
                    "Compression lifecycle: %d escalated, %d purged", escalated, purged
                )
            result = {"escalated": escalated, "purged": purged}
            if quality_rejected:
                result["quality_rejected"] = quality_rejected
            return result

        return await self._repository_write_async(write)

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

        The selected model is resolved from ``memory.llm.*`` and its endpoint
        and credentials come from the shared Provider pool. The CLI
        ``/api -> 4`` command updates that reference without duplicating keys.
        """
        level_names = {0: "事件", 1: "场景", 2: "弧线", 3: "纪元", 4: "终章"}
        from_name = level_names.get(from_level, str(from_level))
        to_name = level_names.get(to_level, str(to_level))
        topics_text = ", ".join(topics[:5]) if topics else "通用"

        # Try LLM via the unified resolver (summarization role; cached).
        try:
            from memai.repository.llm_cache import (
                build_cache_key,
                open_cached_with_repository,
                store_cached_with_repository,
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
                    cached = open_cached_with_repository(self._repository, cache_key)
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
                            store_cached_with_repository(
                                self._repository,
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
            from memai.repository.llm_cache import (
                build_cache_key,
                open_cached_with_repository,
                store_cached_with_repository,
            )

            client, model = self._resolve_mem_llm_client()
            if client is None:
                return False  # No LLM → purge (safe: entries are >2 years old)
            topics_text = ", ".join(topics[:5]) if topics else "无"
            input_text = f"{mem_id}|{title}|{summary}|{topics_text}"
            cache_key = build_cache_key("purge_review", model, input_text)
            cached = None
            try:
                cached = open_cached_with_repository(self._repository, cache_key)
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
                    store_cached_with_repository(
                        self._repository,
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
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        def write(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                "DELETE FROM compressed_memories "
                "WHERE status = 'purged' AND pinned = 0 "
                "AND identity_layer IS NULL "
                "AND memory_id NOT LIKE 'identity-founding-%' AND compressed_at < ?",
                (cutoff,),
            )
            return max(0, int(cursor.rowcount or 0))

        deleted = await self._repository_write_async(write)
        if deleted:
            logger.info("Purged %d expired compressed memories", deleted)
        return deleted

    def _http_handlers(self) -> Dict[str, Callable[..., object]]:
        """Expose only application handlers required by the HTTP adapter."""
        return {
            "health_check": self.health_check,
            "report_agent_outbox_health": self.report_agent_outbox_health,
            "get_mem_usage": self.get_mem_usage,
            "create_session": self.create_session,
            "list_sessions": self.list_sessions,
            "get_session": self.get_session,
            "close_session": self.close_session,
            "aggregate_day": self.aggregate_day,
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
            "author_identity_experience": self.author_identity_experience,
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
                post_restore=self._repository.reconcile_schema,
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

    def _authorized_graph_domains(
        self,
        source_domains: str | None,
        memory_actor: MemoryActor | str,
    ) -> tuple[str, ...]:
        if source_domains:
            requested = tuple(
                MemoryDomain(item)
                for item in self._parse_graph_domains(source_domains)
            )
            return _authorized_read_domains(memory_actor, requested)
        return _authorized_read_domains(memory_actor, None)

    async def list_graph_entities(
        self,
        limit: int = 50,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        source_domains: str | None = None,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
    ):
        from memai.indexes.entity_graph import list_graph_entities as _list_entities

        domains = self._authorized_graph_domains(source_domains, memory_actor)
        entities = self._repository_read(
            lambda conn: _list_entities(
                conn,
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_domains=domains,
                limit=limit,
            )
        )
        return {"entities": entities, "count": len(entities)}

    async def get_graph_neighbors(
        self,
        entity_id: str,
        limit: int = 50,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        source_domains: str | None = None,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
    ):
        from memai.indexes.entity_graph import list_graph_neighbors as _neighbors

        domains = self._authorized_graph_domains(source_domains, memory_actor)
        neighbors = self._repository_read(
            lambda conn: _neighbors(
                conn,
                entity_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_domains=domains,
                limit=limit,
            )
        )
        return {"entity_id": entity_id, "neighbors": neighbors, "count": len(neighbors)}

    async def rebuild_entity_graph(
        self,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        memory_domain: str | None = None,
        memory_actor: MemoryActor = DEFAULT_MEMORY_ACTOR,
    ):
        from memai.indexes.entity_graph import rebuild_entity_graph as _rebuild

        if (
            owner_id is None
            and workspace_id is None
            and memory_actor != MemoryActor.MEMORY_MAINTENANCE
        ):
            owner_id = DEFAULT_OWNER_ID
            workspace_id = DEFAULT_WORKSPACE_ID
        if memory_domain is None:
            authorized_domain = None
            if memory_actor != MemoryActor.MEMORY_MAINTENANCE:
                authorized_domain = _authorized_write_domain(
                    memory_actor, DEFAULT_MEMORY_DOMAIN
                )
        else:
            authorized_domain = _authorized_write_domain(memory_actor, memory_domain)
        def write(conn: sqlite3.Connection) -> int:
            linked = _rebuild(
                conn,
                owner_id=owner_id,
                workspace_id=workspace_id,
                memory_domain=authorized_domain,
            )
            return linked

        linked = await self._repository_write_async(write)
        return {"status": "rebuilt", "memory_records_linked": linked}

    # ── Compression quality dashboard ─────────────────────────────────────

    async def compression_quality(
        self,
        limit: int = 20,
        owner_id: str | None = None,
        workspace_id: str | None = None,
    ):
        bounded = max(1, min(int(limit), 200))
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(str(owner_id))
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(str(workspace_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._repository_read(
            lambda conn: conn.execute(
                "SELECT evaluated_at, status, candidate_count, event_count, "
                "covered_turn_count, event_coverage, backlink_completeness, "
                "compression_ratio, degraded_fraction, source_support, "
                "identifier_fidelity, polarity_consistency, thresholds, failed_checks, "
                "owner_id, workspace_id FROM compression_quality_audit"
                + where + " ORDER BY evaluated_at DESC LIMIT ?",
                [*params, bounded],
            ).fetchall()
        )
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
        from memai.application.identity_seed import (
            founding_manifest_version,
            load_founding_manifest,
            load_founding_story,
        )

        bounded_history = max(1, min(int(history_limit), 100))
        scope = MemoryScope.create(owner_id, workspace_id)
        manifest = load_founding_manifest()
        def read(conn: sqlite3.Connection) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
            anchors = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories "
                "WHERE memory_id LIKE 'identity-founding-%' ORDER BY memory_id"
            ).fetchall()
            self_experiences = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "AND identity_layer = 'self_experience' "
                "AND ((owner_id = ? AND workspace_id = ?) OR "
                "(owner_id = '*' AND workspace_id = '*')) "
                "ORDER BY timespan_end DESC LIMIT 12",
                (scope.owner_id, scope.workspace_id),
            ).fetchall()
            governance_history = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "AND identity_layer = 'governance_history' "
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
            return anchors, self_experiences, governance_history, revisions

        anchors, self_experiences, governance_history, revisions = self._repository_read(read)

        return {
            "identity": str(manifest.get("identity") or "xingzi"),
            "manifest_version": founding_manifest_version(),
            "recorded_at": manifest.get("recorded_at"),
            "source_document": manifest.get("source_document"),
            "story_title": "星子计划：从信任开始",
            "story": load_founding_story(),
            "layers": {
                "anchors": [_cmem_row_to_dict(row) for row in anchors],
                "self_experiences": [
                    _cmem_row_to_dict(row) for row in self_experiences
                ],
                "governance_history": [
                    _cmem_row_to_dict(row) for row in governance_history
                ],
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

    async def author_identity_experience(self, request: SelfIdentityExperienceCreate):
        """Persist 星子's evidence-backed, first-person identity experience."""
        if request.memory_actor is not MemoryActor.STELLAR_COMPANION:
            raise HTTPException(
                status_code=403,
                detail="Only stellar_companion may author first-person identity history",
            )
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        authorized_domain = _authorized_identity_experience_domain(
            request.memory_actor, request.memory_domain
        )
        evidence_refs = list(
            dict.fromkeys(
                str(item).strip()
                for item in self._memory_storage_value(request.evidence_refs)
            )
        )
        if not evidence_refs or any(not item for item in evidence_refs):
            raise HTTPException(status_code=400, detail="evidence_refs cannot be empty")

        def write(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT speaker, metadata FROM turns WHERE turn_id = ? AND owner_id = ? "
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
                metadata = json.loads(row[1] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if str(row[0]) != "agent":
                raise HTTPException(
                    status_code=409,
                    detail="First-person identity history must be authored from an agent turn",
                )
            verified_fields = {
                "identity_experience": True,
                "verified": True,
                "self_authored_identity": True,
                "self_claim": str(self._memory_storage_value(request.self_claim)).strip(),
                "what_changed": str(self._memory_storage_value(request.what_changed)).strip(),
                "continuity_impact": str(
                    self._memory_storage_value(request.continuity_impact)
                ).strip(),
                "agency": str(self._memory_storage_value(request.agency)).strip(),
                "identity_title": str(self._memory_storage_value(request.title)).strip(),
                "identity_summary": str(self._memory_storage_value(request.summary)).strip(),
                "evidence_refs": evidence_refs,
                "verified_by": "stellar_companion",
                "topics": list(dict.fromkeys(self._memory_storage_value(request.topics))),
                "entities": list(dict.fromkeys(self._memory_storage_value(request.entities))),
                "event_kind": str(
                    self._memory_storage_value(request.event_kind)
                ).strip(),
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
        await self._repository_write_async(write)

        sync_result = await self._identity_experience_cycle()
        digest = hashlib.sha256(request.turn_id.strip().encode("utf-8")).hexdigest()[:20]
        memory_id = f"identity-experience-turn-{digest}"
        experience_row = self._repository_read(
            lambda conn: conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE memory_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()
        )
        experience = _cmem_row_to_dict(experience_row) if experience_row else None
        return {
            "status": "authored",
            "turn_id": request.turn_id.strip(),
            "experience": experience,
            "sync": sync_result,
        }

    async def propose_identity_revision(self, proposal: IdentityRevisionProposal):
        from memai.application.identity_seed import (
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
        await self._repository_write_async(
            lambda conn: conn.execute(
                "INSERT INTO identity_revision_proposals "
                "(proposal_id, target_memory_id, baseline_version, reason, "
                "proposed_changes, evidence, source_actor, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    proposal_id, proposal.target_memory_id, proposal.baseline_version,
                    self._memory_storage_value(proposal.reason),
                    json.dumps(
                        self._memory_storage_value(proposal.proposed_changes),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        self._memory_storage_value(proposal.evidence),
                        ensure_ascii=False,
                    ),
                    proposal.source_actor, created_at,
                ),
            )
        )
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
        def write(conn: sqlite3.Connection) -> None:
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
        await self._repository_write_async(write)
        return {
            "proposal_id": proposal_id,
            "status": status,
            "decided_at": decided_at,
            "runtime_identity_changed": False,
        }

    def _resolve_mem_llm_client(self, *, role: str = "default"):
        """Resolve one Mem role and retain its non-secret failure diagnostics."""
        try:
            from memai.model_config import resolve_mem_llm

            resolution = resolve_mem_llm(role=role)
            self._llm_resolution_status = resolution.status
            self._llm_resolution_detail = resolution.detail
            return resolution.client, resolution.model
        except Exception as exc:
            self._llm_resolution_status = "resolution_failed"
            self._llm_resolution_detail = type(exc).__name__
            return None, ""

    async def _generate_session_summary(
        self,
        *,
        session_id: str,
        source_hash: str,
        turns,
    ):
        from memai.repository.llm_cache import (
            TASK_SESSION_SUMMARY,
            build_cache_key,
            open_cached_with_repository,
            store_cached_with_repository,
        )

        client, model = self._resolve_mem_llm_client(role="summarization")
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="Mem summarization model is unavailable",
            )
        cache_key = build_cache_key(TASK_SESSION_SUMMARY, model, source_hash)
        cached = open_cached_with_repository(self._repository, cache_key)
        if isinstance(cached, dict):
            try:
                return normalize_session_summary(cached)
            except ValueError:
                pass

        request_call = partial(
            client.complete_json,
            system_prompt=(
                "你是长期记忆的会话编目员。请只依据给定的有序对话，记录本次会话"
                "主要做了什么、产生了哪些结果、还有哪些明确未决问题。不要推断人格，"
                "不要补写未发生的结果，不要把讨论中的假设写成事实。输出必须是 JSON。"
            ),
            user_payload={
                "session_id": session_id,
                "turns": [turn.as_prompt_item() for turn in turns],
                "required_output": {
                    "title": "简短会话标题",
                    "summary": "按发生顺序概括本次会话主要做了什么",
                    "outcomes": ["已经确认或完成的结果"],
                    "open_questions": ["会话结束时仍未解决的问题"],
                },
            },
            task="scholar.session_summary",
            response_schema=(
                '{"title":"string","summary":"string",'
                '"outcomes":["string"],"open_questions":["string"]}'
            ),
        )
        try:
            payload = await asyncio.to_thread(request_call)
            draft = normalize_session_summary(payload)
        except Exception as exc:
            logger.warning("Session summary generation failed for %s: %s", session_id, exc)
            raise HTTPException(
                status_code=503,
                detail="Mem session summarization failed",
            ) from exc
        try:
            store_cached_with_repository(
                self._repository,
                cache_key=cache_key,
                task=TASK_SESSION_SUMMARY,
                model=model,
                input_text=source_hash,
                result=draft.as_dict(),
            )
        except Exception:
            logger.debug("Session summary cache write failed", exc_info=True)
        return draft

    async def _generate_day_summary(
        self,
        *,
        day_key: str,
        source_hash: str,
        summaries,
    ):
        from memai.repository.llm_cache import (
            TASK_DAY_SUMMARY,
            build_cache_key,
            open_cached_with_repository,
            store_cached_with_repository,
        )

        client, model = self._resolve_mem_llm_client(role="summarization")
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="Mem summarization model is unavailable",
            )
        cache_key = build_cache_key(TASK_DAY_SUMMARY, model, source_hash)
        cached = open_cached_with_repository(self._repository, cache_key)
        if isinstance(cached, dict):
            try:
                return normalize_day_summary(cached)
            except ValueError:
                pass

        request_call = partial(
            client.complete_json,
            system_prompt=(
                "你是长期记忆的日目录编目员。请只依据给定的有序会话摘要，记录这个"
                "自然日主要做了什么、形成了哪些结果、还留下哪些明确未决问题。保持"
                "不同会话的先后顺序和边界，不要按主题虚构合并，不要添加输入中没有的"
                "事实。输出必须是 JSON。"
            ),
            user_payload={
                "day": day_key,
                "session_summaries": [
                    summary.as_prompt_item() for summary in summaries
                ],
                "required_output": {
                    "title": "简短日期标题",
                    "summary": "按会话发生顺序概括当日主要工作",
                    "outcomes": ["当日已经确认或完成的结果"],
                    "open_questions": ["当日结束时仍未解决的问题"],
                },
            },
            task="scholar.day_summary",
            response_schema=(
                '{"title":"string","summary":"string",'
                '"outcomes":["string"],"open_questions":["string"]}'
            ),
        )
        try:
            payload = await asyncio.to_thread(request_call)
            draft = normalize_day_summary(payload)
        except Exception as exc:
            logger.warning("Day summary generation failed for %s: %s", day_key, exc)
            raise HTTPException(
                status_code=503,
                detail="Mem day summarization failed",
            ) from exc
        try:
            store_cached_with_repository(
                self._repository,
                cache_key=cache_key,
                task=TASK_DAY_SUMMARY,
                model=model,
                input_text=source_hash,
                result=draft.as_dict(),
            )
        except Exception:
            logger.debug("Day summary cache write failed", exc_info=True)
        return draft

    async def _app_lifespan(self, app: FastAPI):
        """Own Gateway registration and memory maintenance background tasks."""
        del app
        # Gateway registration is control-plane bookkeeping.  It must not hold
        # the local Memory data plane behind a remote service's retry window.
        self._gateway_registration_task = asyncio.create_task(
            self._gateway_registration_loop()
        )
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
        self._compression_task = asyncio.create_task(self._compression_loop())
        self._tier2_wake.set()
        self._semantic_task = asyncio.create_task(self._semantic_index_loop())
        try:
            yield
        finally:
            tasks = (
                self._gateway_registration_task,
                self._compression_task,
                self._semantic_task,
                self._maintenance_request_task,
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
            if self._maintenance_run_status["status"] in {"accepted", "running"}:
                self._maintenance_run_status.update(
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            close = getattr(self._repository, "close", None)
            if close is not None:
                await asyncio.to_thread(
                    close,
                    timeout=self.config.memory_write_shutdown_timeout_seconds,
                )

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
            await asyncio.sleep(interval)

    async def _compression_loop(self) -> None:
        """Periodically trigger memory compression (runs in the memory service).

        Per architecture baseline §3.4, Mem is responsible for its own
        maintenance — the supervisor should not be running maintenance
        loops on Mem's behalf.

        Now also runs Tier 1 decay + Tier 2 bridge (two-tier architecture).
        """
        while True:
            triggered = False
            try:
                await asyncio.wait_for(
                    self._tier2_wake.wait(), timeout=self.config.compression_interval
                )
                self._tier2_wake.clear()
                triggered = True
            except asyncio.TimeoutError:
                pass
            if triggered:
                snapshot = await asyncio.to_thread(self._tier2_candidate_health_snapshot)
                reason = self._tier2_pressure_trigger_reason(snapshot)
                if reason is None:
                    continue
                self._tier2_bridge_last_trigger_reason = reason
                if self._maintenance_lock.locked():
                    continue
                async with self._maintenance_lock:
                    await self._tier2_bridge_cycle()
                continue
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
                await self._run_all_rules_internal(respect_cadence=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Background compression loop failed", exc_info=True)

    async def _run_all_rules_internal(
        self,
        *,
        respect_cadence: bool = False,
        skip_if_busy: bool = True,
    ) -> Dict[str, Any]:
        """Execute all five memory rules in correct order (internal, track execution)."""
        rules = [
            ("identity_experience", self._identity_experience_cycle),
            ("tier1_decay", self._tier1_decay_cycle),
            ("tier2_bridge", self._tier2_bridge_cycle),
            ("lifecycle_escalation", self._apply_compression_lifecycle),
            ("purge_expired", self._purge_expired_memories),
        ]
        if skip_if_busy and self._maintenance_lock.locked():
            return {
                **{
                    rule_name: {"skipped": "in_progress"}
                    for rule_name, _ in rules
                },
                "_effective_work": 0,
            }

        async with self._maintenance_lock:
            now = datetime.now().isoformat()
            results: Dict[str, Any] = {}
            effective_work = 0
            for rule_name, rule_fn in rules:
                if respect_cadence and rule_name != "lifecycle_escalation":
                    last_run = self._last_rule_run_monotonic.get(rule_name)
                    if (
                        last_run is not None
                        and time.monotonic() - last_run
                        < self.config.compression_interval
                    ):
                        results[rule_name] = {"skipped": "cadence"}
                        continue
                if respect_cadence and rule_name == "lifecycle_escalation":
                    cadence = claim_rule_execution(
                        self._db_path,
                        rule_name=rule_name,
                        cadence_days=self.config.lifecycle_cadence_days,
                        repository=self._repository,
                    )
                    if not cadence.due:
                        results[rule_name] = {
                            "skipped": cadence.skip_reason or "cadence",
                            "last_succeeded_at": cadence.last_succeeded_at,
                            "next_due_at": cadence.next_due_at,
                        }
                        continue
                try:
                    result = await rule_fn()
                    results[rule_name] = result
                    self._last_rule_run[rule_name] = now
                    self._last_rule_run_monotonic[rule_name] = time.monotonic()
                    self._rule_run_counts[rule_name] = (
                        self._rule_run_counts.get(rule_name, 0) + 1
                    )
                    effective_work += self._rule_effective_count(result)
                    if rule_name == "lifecycle_escalation":
                        record_rule_result(
                            self._db_path,
                            rule_name=rule_name,
                            succeeded=True,
                            repository=self._repository,
                        )
                except Exception as exc:
                    logger.warning(
                        "Memory maintenance rule %s failed: %s",
                        rule_name,
                        exc,
                        exc_info=True,
                    )
                    results[rule_name] = {"error": str(exc)}
                    if rule_name == "lifecycle_escalation":
                        record_rule_result(
                            self._db_path,
                            rule_name=rule_name,
                            succeeded=False,
                            error=str(exc),
                            repository=self._repository,
                        )
            # Only real writes count as effective activity. Cadence and lock
            # skips must not make an idle memory pipeline look active.
            if effective_work > 0:
                self._last_effective_activity_at = now
            results["_effective_work"] = effective_work
            return results

    async def _identity_experience_cycle(self) -> Dict[str, int]:
        from memai.application.identity_experience import sync_identity_experiences
        return await self._repository_write_async(
            lambda conn: sync_identity_experiences(conn, commit=False)
        )

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

    def _maintenance_run_snapshot(self) -> Dict[str, Any]:
        snapshot = dict(self._maintenance_run_status)
        rules = snapshot.get("rules")
        if isinstance(rules, dict):
            snapshot["rules"] = dict(rules)
        return snapshot

    def _maintenance_due(self) -> bool:
        if self._maintenance_lock.locked():
            return False
        last_runs = list(self._last_rule_run_monotonic.values())
        if not last_runs:
            return True
        return time.monotonic() - min(last_runs) >= self.config.compression_interval

    @staticmethod
    def _maintenance_rule_errors(results: Dict[str, Any]) -> list[str]:
        return [
            f"{rule_name}: {rule_result['error']}"
            for rule_name, rule_result in results.items()
            if isinstance(rule_result, dict) and rule_result.get("error")
        ]

    async def _run_requested_maintenance(self, run_id: str) -> None:
        self._maintenance_run_status.update(
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            results = await self._run_all_rules_internal(
                respect_cadence=True,
                skip_if_busy=False,
            )
            errors = self._maintenance_rule_errors(results)
            self._maintenance_run_status.update(
                status="failed" if errors else "completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                rules=results,
                error="; ".join(errors) if errors else None,
            )
        except asyncio.CancelledError:
            self._maintenance_run_status.update(
                status="cancelled",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            raise
        except Exception as exc:
            logger.warning(
                "Requested memory maintenance %s failed: %s",
                run_id,
                exc,
                exc_info=True,
            )
            self._maintenance_run_status.update(
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )

    async def run_all_rules(self, request: dict = None):
        """Accept an asynchronous request to run all memory maintenance rules.

        Rules executed in order:
          1. identity_experience — Settle verified experiences and evidence-backed narrative
          2. tier1_decay         — Exponential decay of turn relevance_scores
          3. tier2_bridge        — Feed expired turns into ChroniclePipeline → compressed_memories
          4. lifecycle_escalation — Escalate ordinary entries through compression levels
          5. purge_expired       — Hard-delete ordinary purged entries past audit retention
        """
        del request
        active_task = self._maintenance_request_task
        if active_task is not None and not active_task.done():
            snapshot = self._maintenance_run_snapshot()
            snapshot["status"] = "in_progress"
            return snapshot
        if self._maintenance_lock.locked():
            return {
                "run_id": None,
                "status": "in_progress",
                "accepted_at": None,
                "started_at": None,
                "completed_at": None,
                "rules": None,
                "error": None,
            }

        accepted_at = datetime.now(timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        self._maintenance_run_status = {
            "run_id": run_id,
            "status": "accepted",
            "accepted_at": accepted_at,
            "started_at": None,
            "completed_at": None,
            "rules": None,
            "error": None,
        }
        self._maintenance_request_task = asyncio.create_task(
            self._run_requested_maintenance(run_id),
            name=f"memory-maintenance-{run_id}",
        )
        return self._maintenance_run_snapshot()

    async def rules_status(self):
        """Return the last execution time and count for each rule."""
        lifecycle_state = get_rule_state(
            self._db_path,
            "lifecycle_escalation",
            repository=self._repository,
        )
        maintenance_run = self._maintenance_run_snapshot()
        return {
            **{
                key: value
                for key, value in maintenance_run.items()
                if key != "rules"
            },
            "maintenance_run": maintenance_run,
            "maintenance_due": self._maintenance_due(),
            "rules": {
                name: {
                    "last_run": self._last_rule_run.get(name),
                    "run_count": self._rule_run_counts.get(name, 0),
                }
                for name in ["tier1_decay", "tier2_bridge", "lifecycle_escalation", "purge_expired"]
            },
            "compression_interval": self.config.compression_interval,
            "tier1_retention_days": self.config.tier1_retention_days,
            "lifecycle_cadence_days": self.config.lifecycle_cadence_days,
            "lifecycle_state": lifecycle_state,
            "tier2_bridge_last_result": self._last_tier2_bridge_result,
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
            self._db_path, self.config, now=now, logger=logger, repository=self._repository
        )

    async def _tier2_bridge_cycle(self) -> Dict[str, Any]:
        self._tier2_bridge_state = "running"
        try:
            result = await run_tier2_bridge_cycle(
                self._db_path, self.config, request_factory=Tier2CompressRequest,
                compress=self.tier2_compress,
                maintenance_actor=MemoryActor.MEMORY_MAINTENANCE, logger=logger,
                repository=self._repository,
            )
        except Exception as exc:
            self._record_tier2_bridge_failure(str(exc))
            raise
        self._last_tier2_bridge_result = result
        failed_scopes = [
            scope for scope in result.get("scopes", [])
            if scope.get("status") in {"failed", "quality_rejected", "no_events_generated"}
        ]
        if failed_scopes:
            errors = [
                str(error)
                for scope in failed_scopes
                for error in scope.get("errors", [])
                if str(error)
            ]
            self._record_tier2_bridge_failure(
                errors[0] if errors else str(failed_scopes[0].get("status"))
            )
        else:
            self._tier2_bridge_consecutive_failures = 0
            self._tier2_bridge_last_failure_reason = None
            self._tier2_bridge_last_succeeded_at = datetime.now(timezone.utc).isoformat()
            self._tier2_bridge_state = "idle"
        return result

    def _record_tier2_bridge_failure(self, reason: str) -> None:
        self._tier2_bridge_consecutive_failures += 1
        self._tier2_bridge_last_failure_reason = reason
        self._tier2_bridge_state = (
            "degraded"
            if self._tier2_bridge_consecutive_failures
            >= self.config.tier2_bridge_failure_degraded_after
            else "failed"
        )

    def _tier2_candidate_health_snapshot(self) -> Dict[str, Any]:
        current_revision = getattr(self._repository, "commit_revision", 0)
        cached = self._tier2_candidate_health_snapshot_cache
        if (
            cached is not None
            and self._tier2_candidate_health_snapshot_cache_revision == current_revision
        ):
            return dict(cached)

        def read(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
            scopes = conn.execute(
                "SELECT DISTINCT memory_domain, owner_id, workspace_id FROM turns "
                "WHERE compression_status IN ('pending', 'retry_wait')"
            ).fetchall()
            return [(str(domain), str(owner_id), str(workspace_id)) for domain, owner_id, workspace_id in scopes]

        scopes = self._repository_read(read)
        eligible_count = 0
        oldest_at: str | None = None
        oldest_time: datetime | None = None
        for domain, owner_id, workspace_id in scopes:
            if not domain or not owner_id or not workspace_id:
                continue
            snapshot = Tier1ToTier2Bridge(
                self._db_path,
                retention_days=self.config.tier1_retention_days,
                max_turns=self.config.tier1_max_turns,
                memory_domain=str(domain), owner_id=str(owner_id),
                workspace_id=str(workspace_id),
            ).candidate_health_snapshot()
            eligible_count += int(snapshot["eligible_count"])
            candidate_at = snapshot["oldest_candidate_at"]
            candidate_time = _parse_utc_timestamp(candidate_at)
            if candidate_time is not None and (
                oldest_time is None or candidate_time < oldest_time
            ):
                oldest_at = str(candidate_at)
                oldest_time = candidate_time
        oldest_age_seconds = 0.0
        if oldest_time is not None:
            oldest_age_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - oldest_time).total_seconds(),
            )
        snapshot = {
            "eligible_candidate_count": eligible_count,
            "oldest_candidate_at": oldest_at,
            "oldest_candidate_age_seconds": round(oldest_age_seconds, 3),
        }
        self._tier2_candidate_health_snapshot_cache_revision = current_revision
        self._tier2_candidate_health_snapshot_cache = dict(snapshot)
        return snapshot

    def _tier2_pressure_trigger_reason(self, snapshot: Dict[str, Any]) -> str | None:
        if snapshot["eligible_candidate_count"] >= self.config.tier2_trigger_candidate_count:
            return "candidate_count"
        if (
            snapshot["oldest_candidate_age_seconds"]
            >= self.config.tier2_trigger_oldest_age_seconds
        ):
            return "oldest_candidate_age"
        return None

    def _memory_reference_health_snapshot(self) -> Dict[str, Any]:
        """统计 Tier2 来源引用类型，避免把外部证据误判为孤儿 turn。"""
        def read(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], set[str], set[str]]:
            rows = conn.execute(
                """
                SELECT source_turns
                FROM compressed_memories
                WHERE source_turns IS NOT NULL
                  AND json_valid(source_turns)
                """
            ).fetchall()
            active_turn_ids = {
                row[0]
                for row in conn.execute("SELECT turn_id FROM turns")
            }
            archived_turn_ids = {
                row[0]
                for row in conn.execute("SELECT turn_id FROM turns_archive")
            }
            return rows, active_turn_ids, archived_turn_ids

        rows, active_turn_ids, archived_turn_ids = self._repository_read(read)

        total = active = archived = external = malformed = 0
        for row in rows:
            try:
                references = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                malformed += 1
                continue
            if not isinstance(references, list):
                malformed += 1
                continue
            for reference in references:
                total += 1
                if reference in active_turn_ids:
                    active += 1
                elif reference in archived_turn_ids:
                    archived += 1
                else:
                    external += 1

        return {
            "source_references": total,
            "active_turn_references": active,
            "archived_turn_references": archived,
            "external_references": external,
            "malformed_source_lists": malformed,
        }

    async def health_check(self):
        database = await asyncio.to_thread(self._database_health_snapshot)
        reference_health = await asyncio.to_thread(
            self._memory_reference_health_snapshot
        )
        semantic = await asyncio.to_thread(self._semantic_health_snapshot)
        agent_outbox = self._agent_outbox_health_snapshot()
        transport_outboxes = self._transport_outboxes_health_snapshot(agent_outbox)
        tier2_candidates = await asyncio.to_thread(
            self._tier2_candidate_health_snapshot
        )
        maintenance = {
            "last_effective_activity_at": self._last_effective_activity_at,
            "last_rule_runs": dict(self._last_rule_run),
            "last_tier2_bridge_result": self._last_tier2_bridge_result,
            "requested_run": self._maintenance_run_snapshot(),
            "tier2_bridge": {
                **tier2_candidates,
                "state": self._tier2_bridge_state,
                "consecutive_failures": self._tier2_bridge_consecutive_failures,
                "last_failure_reason": self._tier2_bridge_last_failure_reason,
                "last_succeeded_at": self._tier2_bridge_last_succeeded_at,
                "last_trigger_reason": self._tier2_bridge_last_trigger_reason,
            },
        }
        service_healthy = bool(
            transport_outboxes["healthy"]
            and self._tier2_bridge_state != "degraded"
        )
        database_healthy = database["readable"] and database["integrity"] == "ok"
        return {
            "status": (
                "healthy" if service_healthy and database_healthy else "degraded"
            ),
            "service": "memory-service",
            "service_reachable": True,
            "commit_revision": database["commit_revision"],
            "redaction": {
                "enabled": self.config.redact_before_store,
                "scope": "memory_persistence_and_recall",
            },
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
            "database": database,
            "memory_references": reference_health,
            "semantic_index": semantic,
            "agent_outbox": agent_outbox,
            "transport_outboxes": transport_outboxes,
            "maintenance": maintenance,
        }

    async def report_agent_outbox_health(
        self,
        report: AgentOutboxHealthReport,
    ) -> Dict[str, Any]:
        reported_at = datetime.now(timezone.utc).isoformat()
        self._agent_outbox_reports[report.outbox_id] = {
            **report.model_dump(mode="json"),
            "reported_at": reported_at,
        }
        return {
            "status": "recorded",
            "outbox_id": report.outbox_id,
            "reported_at": reported_at,
        }

    def _agent_outbox_health_snapshot(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        reports: list[Dict[str, Any]] = []
        issues: list[str] = []
        for stored in self._agent_outbox_reports.values():
            report = dict(stored)
            reported_at = self._parse_health_timestamp(report.get("reported_at"))
            report_age = (
                max(0.0, (now - reported_at).total_seconds())
                if reported_at is not None
                else float("inf")
            )
            report["report_age_seconds"] = round(report_age, 3)
            report["stale"] = (
                report_age > self.config.agent_outbox_report_stale_seconds
            )
            oldest_pending = self._parse_health_timestamp(
                report.get("oldest_pending_at")
            )
            pending_age = (
                max(0.0, (now - oldest_pending).total_seconds())
                if oldest_pending is not None
                else 0.0
            )
            report["oldest_pending_age_seconds"] = round(pending_age, 3)
            oldest_failure = self._parse_health_timestamp(
                report.get("oldest_failure_at")
            )
            failure_age = (
                max(0.0, (now - oldest_failure).total_seconds())
                if oldest_failure is not None
                else 0.0
            )
            report["oldest_failure_age_seconds"] = round(failure_age, 3)
            outbox_id = str(report.get("outbox_id") or "unknown")
            if int(report.get("dead_letter_count") or 0) > 0:
                issues.append(f"{outbox_id}:dead_letter")
            if (
                int(report.get("pending_count") or 0) > 0
                and pending_age > self.config.agent_outbox_pending_stale_seconds
            ):
                issues.append(f"{outbox_id}:stuck_pending")
            if report["stale"] and (
                int(report.get("pending_count") or 0) > 0
                or int(report.get("dead_letter_count") or 0) > 0
            ):
                issues.append(f"{outbox_id}:stale_report")
            reports.append(report)

        reports.sort(key=lambda item: str(item.get("outbox_id") or ""))
        active_count = sum(not bool(item["stale"]) for item in reports)
        unique_issues = sorted(set(issues))
        return {
            "healthy": not unique_issues,
            "status": (
                "degraded"
                if unique_issues
                else ("healthy" if active_count else "unreported")
            ),
            "reporter_count": len(reports),
            "active_reporter_count": active_count,
            "stale_reporter_count": len(reports) - active_count,
            "pending_count": sum(
                int(item.get("pending_count") or 0) for item in reports
            ),
            "inflight_count": sum(
                int(item.get("inflight_count") or 0) for item in reports
            ),
            "dead_letter_count": sum(
                int(item.get("dead_letter_count") or 0) for item in reports
            ),
            "oldest_pending_age_seconds": max(
                (
                    float(item.get("oldest_pending_age_seconds") or 0.0)
                    for item in reports
                ),
                default=0.0,
            ),
            "oldest_failure_age_seconds": max(
                (
                    float(item.get("oldest_failure_age_seconds") or 0.0)
                    for item in reports
                ),
                default=0.0,
            ),
            "issues": unique_issues,
            "report_stale_after_seconds": (
                self.config.agent_outbox_report_stale_seconds
            ),
            "pending_stale_after_seconds": (
                self.config.agent_outbox_pending_stale_seconds
            ),
            "reporters": reports,
        }

    def _transport_outboxes_health_snapshot(
        self, agent_outbox: Dict[str, Any]
    ) -> Dict[str, Any]:
        queues: Dict[str, list[Dict[str, Any]]] = {}
        for report in agent_outbox.get("reporters", []):
            queue_name = str(report.get("queue_name") or "api_a")
            queues.setdefault(queue_name, []).append(report)

        outboxes: Dict[str, Dict[str, Any]] = {}
        issues: list[str] = []
        for queue_name, reports in sorted(queues.items()):
            queue_issues: list[str] = []
            for report in reports:
                if int(report.get("dead_letter_count") or 0) > 0:
                    queue_issues.append(f"{queue_name}:dead_letter")
                if (
                    int(report.get("pending_count") or 0) > 0
                    and float(report.get("oldest_pending_age_seconds") or 0.0)
                    > self.config.agent_outbox_pending_stale_seconds
                ):
                    queue_issues.append(f"{queue_name}:stuck_pending")
                if report.get("stale") and (
                    int(report.get("pending_count") or 0) > 0
                    or int(report.get("dead_letter_count") or 0) > 0
                ):
                    queue_issues.append(f"{queue_name}:stale_report")
            unique_queue_issues = sorted(set(queue_issues))
            outboxes[queue_name] = {
                "healthy": not unique_queue_issues,
                "status": "degraded" if unique_queue_issues else "healthy",
                "reporter_count": len(reports),
                "pending_count": sum(int(r.get("pending_count") or 0) for r in reports),
                "inflight_count": sum(int(r.get("inflight_count") or 0) for r in reports),
                "dead_letter_count": sum(int(r.get("dead_letter_count") or 0) for r in reports),
                "issues": unique_queue_issues,
                "reporters": reports,
            }
            issues.extend(unique_queue_issues)
        return {
            "healthy": not issues,
            "status": "degraded" if issues else ("healthy" if outboxes else "unreported"),
            "reporter_count": agent_outbox.get("reporter_count", 0),
            "pending_count": agent_outbox.get("pending_count", 0),
            "inflight_count": agent_outbox.get("inflight_count", 0),
            "dead_letter_count": agent_outbox.get("dead_letter_count", 0),
            "issues": sorted(set(issues)),
            "outboxes": outboxes,
        }

    @staticmethod
    def _parse_health_timestamp(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _database_health_snapshot(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        readable = False
        integrity = "unavailable"
        commit_revision = getattr(self._repository, "commit_revision", 0)
        try:
            def read(conn: sqlite3.Connection) -> tuple[str, dict[str, int], int]:
                readable = True
                integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
                integrity_value = str(integrity_row[0] if integrity_row else "missing")
                table_counts: dict[str, int] = {}
                for table in (
                    "sessions",
                    "turns",
                    "turns_archive",
                    "compressed_memories",
                    "profile_memories",
                ):
                    table_counts[table] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
                revision_row = conn.execute(
                    "SELECT state_value FROM memory_runtime_state WHERE state_key = ?",
                    ("memory_commit_revision",),
                ).fetchone()
                revision_value = int(revision_row[0]) if revision_row and revision_row[0] is not None else 0
                return integrity_value, table_counts, revision_value

            integrity, counts, commit_revision = self._repository_read(read)
            readable = True
        except Exception as exc:
            return {
                "readable": False,
                "integrity": "error",
                "path": str(self._db_path),
                "counts": counts,
                "commit_revision": commit_revision,
                "repository": self._database_revision_snapshot(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "readable": readable,
            "integrity": integrity,
            "path": str(self._db_path),
            "counts": counts,
            "commit_revision": commit_revision,
            "repository": self._database_revision_snapshot(),
            "error": None,
        }

    def _semantic_health_snapshot(self) -> dict[str, Any]:
        try:
            status = self._semantic_index.status()
            status["pending_count"] = self._semantic_index.pending_count()
            return status
        except Exception as exc:
            return {
                "enabled": False,
                "pending_count": None,
                "error": f"{type(exc).__name__}: {exc}",
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
                "last_prompt_tokens": 0,
            }
        context_length = usage.get("context_length", 65536)
        last_prompt = usage.get("last_prompt_tokens", 0)
        # Per-request utilisation: last call's prompt_tokens vs the model's
        # context window.  The cumulative total_tokens is an odometer, not a
        # tank-level gauge — dividing it by context_length yields a
        # meaningless number that always grows past 100%.
        last_request_usage_percent = (
            round((last_prompt / context_length) * 100)
            if context_length > 0 and last_prompt > 0
            else None
        )
        return {
            "status": "ok",
            "usage": usage,
            "context_length": context_length,
            "last_request_usage_percent": last_request_usage_percent,
            "commit_revision": getattr(self._repository, "commit_revision", 0),
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
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            existing = conn.execute(
                "SELECT owner_id, workspace_id, memory_domain FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing and tuple(existing) != (
                scope.owner_id,
                scope.workspace_id,
                memory_domain,
            ):
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
                    json.dumps(self._memory_storage_value(request.metadata)),
                ),
            )
            return {
                "session_id": session_id,
                "created_at": now,
                "status": "created" if not existing else "existing",
                "memory_domain": memory_domain,
                **scope.as_dict(),
            }

        result = await self._repository_write_async(write)
        logger.info("Session created: %s", session_id)
        result["write_status"] = "committed"
        result["commit_revision"] = getattr(self._repository, "commit_revision", 0)
        return result

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
        def read(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            return conn.execute(
                "SELECT s.session_id, s.created_at, s.updated_at, s.metadata, "
                "COUNT(t.turn_id) as turn_count "
                "FROM sessions s LEFT JOIN turns t ON s.session_id = t.session_id "
                "AND t.owner_id = s.owner_id AND t.workspace_id = s.workspace_id "
                "AND t.memory_domain = s.memory_domain "
                "WHERE s.owner_id = ? AND s.workspace_id = ? AND s.memory_domain = ? "
                "GROUP BY s.session_id ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
                (scope.owner_id, scope.workspace_id, authorized_domain, limit, offset),
            ).fetchall()

        rows = self._repository_read(read)
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
        def read(conn: sqlite3.Connection) -> tuple[Any, Any]:
            row = conn.execute(
                "SELECT session_id, created_at, updated_at, metadata FROM sessions "
                "WHERE session_id = ? AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                (session_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")
            turn_count = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (session_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()[0]
            return row, turn_count

        row, turn_count = self._repository_read(read)
        return {
            "session_id": row[0],
            "created_at": row[1],
            "updated_at": row[2],
            "metadata": json.loads(row[3]) if row[3] else {},
            "turn_count": turn_count,
            "memory_domain": authorized_domain,
        }

    async def close_session(
        self,
        session_id: str,
        request: SessionCloseRequest,
    ):
        """Publish a SessionSummary and bring its natural-day index current."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor,
            request.memory_domain,
        )
        session_summary = None
        for _attempt in range(2):
            def read_snapshot(conn: sqlite3.Connection):
                session = conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ?",
                    (
                        session_id,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                ).fetchone()
                if not session:
                    raise HTTPException(status_code=404, detail="Session not found")
                turns = load_session_turns(
                    conn,
                    session_id=session_id,
                    owner_id=scope.owner_id,
                    workspace_id=scope.workspace_id,
                    memory_domain=memory_domain,
                )
                if not turns:
                    raise HTTPException(
                        status_code=409,
                        detail="Cannot close a session without stored turns",
                    )
                source_hash = session_source_hash(turns)
                current = get_active_session_summary(
                    conn,
                    session_id=session_id,
                    owner_id=scope.owner_id,
                    workspace_id=scope.workspace_id,
                    memory_domain=memory_domain,
                )
                if current and current["source_hash"] == source_hash:
                    return turns, source_hash, {**current, "write_status": "current"}
                return turns, source_hash, None

            turns, source_hash, current_summary = self._repository_read(read_snapshot)
            if current_summary is not None:
                session_summary = current_summary

            if session_summary is not None:
                break
            draft = await self._generate_session_summary(
                session_id=session_id,
                source_hash=source_hash,
                turns=turns,
            )
            try:
                session_summary = await self._repository_write_async(
                    lambda conn: persist_session_summary(
                        None,
                        session_id=session_id,
                        owner_id=scope.owner_id,
                        workspace_id=scope.workspace_id,
                        memory_domain=memory_domain,
                        timezone_name=self.config.time_summary_timezone,
                        expected_source_hash=source_hash,
                        draft=draft,
                        connection=conn,
                    )
                )
                break
            except SessionSnapshotChanged:
                continue
        if session_summary is None:
            raise HTTPException(
                status_code=409,
                detail="Session kept changing while its summary was generated; retry close",
            )

        affected_day_keys: set[str] = set()
        historical_periods = self._repository_read(lambda conn: conn.execute(
                "SELECT period_start FROM time_summaries WHERE summary_type = 'session' "
                "AND bucket_key = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?",
                (
                    session_id,
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                ),
            ).fetchall())
        for row in historical_periods:
            affected_day_keys.add(
                day_bucket_for_timestamp(
                    str(row[0]),
                    timezone_name=self.config.time_summary_timezone,
                )
            )
        day_key = day_bucket_for_timestamp(
            str(session_summary["period_start"]),
            timezone_name=self.config.time_summary_timezone,
        )
        day_summary = None
        for affected_day_key in sorted(affected_day_keys):
            refreshed = await self.aggregate_day(
                affected_day_key,
                DayAggregateRequest(
                    owner_id=scope.owner_id,
                    workspace_id=scope.workspace_id,
                    memory_actor=request.memory_actor,
                    memory_domain=request.memory_domain,
                ),
            )
            if affected_day_key == day_key:
                day_summary = refreshed
        if day_summary is None:
            raise HTTPException(
                status_code=409,
                detail="Session summary did not resolve to a day bucket",
            )
        return {**session_summary, "day_summary": day_summary}

    async def aggregate_day(
        self,
        day_key: str,
        request: DayAggregateRequest,
    ):
        """Publish an immutable DaySummary for one stable child-summary snapshot."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor,
            request.memory_domain,
        )
        for _attempt in range(2):
            def read_snapshot(conn: sqlite3.Connection):
                try:
                    summaries = load_day_session_summaries(
                        conn,
                        day_key=day_key,
                        owner_id=scope.owner_id,
                        workspace_id=scope.workspace_id,
                        memory_domain=memory_domain,
                        timezone_name=self.config.time_summary_timezone,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                current = get_active_day_summary(
                    conn,
                    day_key=day_key,
                    owner_id=scope.owner_id,
                    workspace_id=scope.workspace_id,
                    memory_domain=memory_domain,
                )
                expected_summary_id = None
                if not summaries:
                    if current is None:
                        return (), None, None, {
                            "summary_type": "day",
                            "bucket_key": day_key,
                            "write_status": "absent",
                        }
                    expected_summary_id = str(current["summary_id"])
                else:
                    source_hash = day_source_hash(summaries)
                if summaries and current and current["source_hash"] == source_hash:
                    return summaries, source_hash, None, {**current, "write_status": "current"}
                return summaries, source_hash if summaries else None, (
                    expected_summary_id if not summaries else None
                ), None

            summaries, source_hash, expected_summary_id, current_summary = self._repository_read(
                read_snapshot
            )
            if current_summary is not None:
                return current_summary

            if not summaries:
                try:
                    return await self._repository_write_async(
                        lambda conn: supersede_empty_day_summary(
                            None,
                            day_key=day_key,
                            owner_id=scope.owner_id,
                            workspace_id=scope.workspace_id,
                            memory_domain=memory_domain,
                            timezone_name=self.config.time_summary_timezone,
                            expected_summary_id=expected_summary_id,
                            connection=conn,
                        )
                    )
                except DaySnapshotChanged:
                    continue

            draft = await self._generate_day_summary(
                day_key=day_key,
                source_hash=source_hash,
                summaries=summaries,
            )
            try:
                return await self._repository_write_async(
                    lambda conn: persist_day_summary(
                        None,
                        day_key=day_key,
                        owner_id=scope.owner_id,
                        workspace_id=scope.workspace_id,
                        memory_domain=memory_domain,
                        timezone_name=self.config.time_summary_timezone,
                        expected_source_hash=source_hash,
                        draft=draft,
                        connection=conn,
                    )
                )
            except DaySnapshotChanged:
                continue
        raise HTTPException(
            status_code=409,
            detail="Day kept changing while its summary was generated; retry aggregation",
        )

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
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        stored_text = str(self._memory_storage_value(request.text))
        stored_tags = list(
            dict.fromkeys(
                str(self._memory_storage_value(tag)).strip()
                for tag in request.tags
                if str(tag).strip()
            )
        )
        stored_metadata = _strip_identity_verification_metadata(
            self._memory_storage_value(request.metadata)
        )
        now = datetime.now().astimezone().isoformat()
        dedup_key = self._derive_turn_dedup_key(
            session_id,
            request,
            stored_text=stored_text,
        )

        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            ses = conn.execute(
                "SELECT session_id, owner_id, workspace_id, memory_domain FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if ses and (str(ses[1]), str(ses[2]), str(ses[3])) != (
                scope.owner_id,
                scope.workspace_id,
                memory_domain,
            ):
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
            if dedup_key:
                existing = conn.execute(
                    "SELECT turn_id, timestamp FROM turns WHERE session_id = ? AND dedup_key = ?",
                    (session_id, dedup_key),
                ).fetchone()
                if existing:
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
                "compression_status, last_decay_at, owner_id, workspace_id, memory_domain) "
                "VALUES (?, ?, ?, ?, ?, 1.0, 0.01, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    turn_id,
                    session_id,
                    request.speaker,
                    stored_text,
                    now,
                    json.dumps(stored_tags, ensure_ascii=False),
                    json.dumps(stored_metadata),
                    dedup_key,
                    now,
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                ),
            )
            profile_settlement = {"action": "none"}
            if request.speaker == "user":
                profile_settlement = _settle_explicit_profile_capture(
                    conn,
                    text=stored_text,
                    turn_id=turn_id,
                    timestamp=now,
                    scope=scope,
                    memory_domain=memory_domain,
                    now=now,
                )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            response = {
                "turn_id": turn_id,
                "session_id": session_id,
                "timestamp": now,
                "status": "created",
                "memory_domain": memory_domain,
                "profile_settlement": profile_settlement,
            }
            if dedup_key:
                response["dedup_key"] = dedup_key
            return response

        response = await self._repository_write_async(write)
        if response.get("status") != "deduplicated":
            logger.debug("Turn %s added to session %s", response["turn_id"], session_id)
            self._semantic_wake.set()
            self._tier2_wake.set()
        response["write_status"] = "committed"
        response["commit_revision"] = getattr(self._repository, "commit_revision", 0)
        return response

    async def add_turn_pair(self, request: TurnPairCreate):
        """Atomically persist one completed user/assistant exchange."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        session_id = request.session_id.strip()
        now = datetime.now().astimezone().isoformat()
        stored_metadata = _strip_identity_verification_metadata(
            self._memory_storage_value(request.metadata)
        )
        stored_tags = list(
            dict.fromkeys(
                str(self._memory_storage_value(tag)).strip()
                for tag in request.tags
                if str(tag).strip()
            )
        )
        stored_tags_json = json.dumps(stored_tags, ensure_ascii=False)

        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            turn_ids: dict[str, str] = {}
            profile_settlement: dict[str, Any] = {"action": "none"}
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
            for speaker, content in (
                ("user", request.user_content),
                ("agent", request.assistant_content),
            ):
                text = str(self._memory_storage_value(content or "")).strip()
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
                    "decay_factor, tags, metadata, dedup_key, compression_status, "
                    "last_decay_at, owner_id, workspace_id, memory_domain) "
                    "VALUES (?, ?, ?, ?, ?, 1.0, 0.01, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                    (
                        turn_id,
                        session_id,
                        speaker,
                        text,
                        now,
                        stored_tags_json,
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
                    profile_settlement = _settle_explicit_profile_capture(
                        conn,
                        text=str(user_turn[0]),
                        turn_id=turn_ids["user"],
                        timestamp=str(user_turn[1]),
                        scope=scope,
                        memory_domain=memory_domain,
                        now=now,
                    )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            return {"turn_ids": turn_ids, "profile_settlement": profile_settlement}

        fingerprint_payload = {
            "session_id": session_id,
            "user_content": str(self._memory_storage_value(request.user_content or "")).strip(),
            "assistant_content": str(self._memory_storage_value(request.assistant_content or "")).strip(),
            "tags": stored_tags,
            "metadata": stored_metadata,
            "owner_id": scope.owner_id,
            "workspace_id": scope.workspace_id,
            "memory_domain": memory_domain,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        receipt = await self._repository_idempotent_write_async(
            receipt_key=f"turn-pair:{request.write_id}",
            operation="add_turn_pair",
            fingerprint=fingerprint,
            owner_id=scope.owner_id,
            workspace_id=scope.workspace_id,
            memory_domain=memory_domain,
            callback=write,
        )
        result = receipt.value
        turn_ids = dict(result["turn_ids"])
        profile_settlement = dict(result["profile_settlement"])
        if receipt.replay and profile_settlement.get("inserted"):
            # Keep the established API meaning for a replay: no new profile rows
            # were inserted by this request, even though the durable receipt
            # returns the original committed response payload.
            profile_settlement["inserted"] = 0
        if receipt.replay and profile_settlement.get("action") == "revoked":
            profile_settlement["action"] = "already_revoked"

        self._semantic_wake.set()
        return {
            "status": "stored",
            "session_id": session_id,
            "write_id": request.write_id,
            "write_status": "committed",
            "turn_ids": turn_ids,
            "identity_settlement": None,
            "profile_settlement": profile_settlement,
            "memory_domain": memory_domain,
            "replayed": receipt.replay,
            "commit_revision": receipt.commit_revision,
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
                "reason": str(self._memory_storage_value(request.reason)).strip(),
                "governance_ref": str(
                    self._memory_storage_value(request.governance_ref)
                ).strip(),
            }
        )
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            result = create_memory_promotion_candidate(conn, request)
            return result

        try:
            result = await self._repository_write_async(write)
        except (
            MemoryPromotionAccessError,
            MemoryPromotionConflictError,
            MemoryPromotionNotFoundError,
            MemoryPromotionValidationError,
        ) as exc:
            raise self._promotion_http_error(exc) from exc
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
            candidates = self._repository_read(
                lambda conn: list_memory_promotion_candidates(
                    conn,
                    scope=scope,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    status=status,
                    limit=limit,
                )
            )
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
                "reason": str(self._memory_storage_value(request.reason)).strip(),
            }
        )
        def write(conn: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any]]:
            candidate, promotion = consent_memory_promotion_candidate(
                conn,
                candidate_id,
                request,
            )
            return candidate, promotion

        try:
            candidate, promotion = await self._repository_write_async(write)
        except (
            MemoryPromotionAccessError,
            MemoryPromotionConflictError,
            MemoryPromotionNotFoundError,
            MemoryPromotionValidationError,
        ) as exc:
            raise self._promotion_http_error(exc) from exc
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
            promotions = self._repository_read(
                lambda conn: list_memory_promotions(
                    conn,
                    scope=scope,
                    target_domains=target_domains,
                    status=status,
                    limit=limit,
                )
            )
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
                "reason": str(self._memory_storage_value(request.reason)).strip(),
            }
        )
        try:
            result = await self._repository_write_async(
                lambda conn: revoke_memory_promotion(conn, promotion_id, request)
            )
        except (
            MemoryPromotionAccessError,
            MemoryPromotionConflictError,
            MemoryPromotionNotFoundError,
            MemoryPromotionValidationError,
        ) as exc:
            raise self._promotion_http_error(exc) from exc
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
        def read(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], int]:
            rows = conn.execute(
                "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
                "decay_factor, tags, metadata, compression_status, memory_domain "
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
            return rows, total

        rows, total = self._repository_read(read)
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
        sql = "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, " \
              "decay_factor, tags, metadata, compression_status, memory_domain FROM turns " \
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
        rows = self._repository_read(lambda conn: conn.execute(sql, params).fetchall())
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
        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
                "decay_factor, tags, metadata, compression_status, memory_domain "
                "FROM turns WHERE turn_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?",
                (turn_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT turn_id, session_id, speaker, text_summary, timestamp, "
                    "compressed_at, event_ids, scene_ids, original_text "
                    "FROM turns_archive WHERE turn_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ?",
                    (turn_id, scope.owner_id, scope.workspace_id, authorized_domain),
                ).fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Turn not found")
                return {
                    "turn_id": row[0], "session_id": row[1], "speaker": row[2],
                    "text": row[8] or row[3], "timestamp": row[4],
                    "in_archive": True, "compressed_at": row[5],
                    "event_ids": json.loads(row[6]) if row[6] else [],
                    "scene_ids": json.loads(row[7]) if row[7] else [],
                    "memory_domain": authorized_domain,
                }
            return _turn_row_to_dict(row)

        return self._repository_read(read)

    async def timeline_view(self, request: TimelineQuery):
        """Get timeline view for a specific date with turn summaries."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        source_domains = _authorized_read_domains(
            request.memory_actor, request.source_domains
        )
        requested_date = request.date.isoformat()
        date_start = f"{requested_date}T00:00:00"
        date_end = f"{(request.date + timedelta(days=1)).isoformat()}T00:00:00"
        sql = "SELECT turn_id, session_id, speaker, text, timestamp FROM turns " \
              "WHERE timestamp >= ? AND timestamp < ? " \
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
        rows = self._repository_read(lambda conn: conn.execute(sql, params).fetchall())
        return {
            "date": requested_date,
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
            if self._llm_resolution_status:
                self._llm_error = self._llm_resolution_status
                if self._llm_resolution_detail:
                    self._llm_error += f": {self._llm_resolution_detail}"
            else:
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

        from memai.application.llm_extraction import build_llm_first_pipeline

        return build_llm_first_pipeline(
            self._db_path,
            role="extraction",
            repository=self._repository,
        )

    async def tier1_stats(
        self,
        owner_id: str = DEFAULT_OWNER_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ):
        """Return storage statistics visible to one memory scope."""
        scope = MemoryScope.create(owner_id, workspace_id)
        private = (scope.owner_id, scope.workspace_id)
        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            total_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE owner_id = ? AND workspace_id = ?",
                private,
            ).fetchone()[0]
            active_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE compression_status != 'compressed' "
                "AND owner_id = ? AND workspace_id = ?",
                private,
            ).fetchone()[0]
            compressed_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE compression_status = 'compressed' "
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
                "SELECT MIN(timestamp) FROM turns WHERE compression_status != 'compressed' "
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
            identity_anchors = conn.execute(
                "SELECT COUNT(*) FROM compressed_memories "
                "WHERE memory_id LIKE 'identity-founding-%'"
            ).fetchone()[0]
            identity_self_experiences = conn.execute(
                f"SELECT COUNT(*) FROM compressed_memories WHERE status = 'active' "
                f"AND hidden = 0 AND identity_layer = 'self_experience' "
                f"AND {visible_clause}",
                visible,
            ).fetchone()[0]
            identity_governance_history = conn.execute(
                f"SELECT COUNT(*) FROM compressed_memories WHERE status = 'active' "
                f"AND hidden = 0 AND identity_layer = 'governance_history' "
                f"AND {visible_clause}",
                visible,
            ).fetchone()[0]
            return {
                "total_turns": total_turns,
                "active_turns": active_turns,
                "compressed_turns": compressed_turns,
                "archived_turns": archived_turns,
                "total_sessions": total_sessions,
                "oldest": oldest,
                "compressed_total": compressed_total,
                "compressed_events": compressed_events,
                "compressed_scenes": compressed_scenes,
                "compressed_arcs": compressed_arcs,
                "identity_anchors": identity_anchors,
                "identity_self_experiences": identity_self_experiences,
                "identity_governance_history": identity_governance_history,
            }

        stats = self._repository_read(read)
        structure = {
            "ordinary_memory": stats["total_turns"],
            "active_session_memory": stats["active_turns"],
            "compressed_memory": stats["compressed_turns"],
            "event_memory": stats["compressed_events"],
            "scene_memory": stats["compressed_scenes"],
            "arc_memory": stats["compressed_arcs"],
            "identity_archive": stats["identity_anchors"],
            "self_experiences": stats["identity_self_experiences"],
            "governance_history": stats["identity_governance_history"],
        }
        return {
            "tier1": {
                "total_turns": stats["total_turns"],
                "active_turns": stats["active_turns"],
                "compressed_turns": stats["compressed_turns"],
                "archived_turns": stats["archived_turns"],
                "total_sessions": stats["total_sessions"],
                "oldest_active_turn": stats["oldest"],
                "retention_days": self.config.tier1_retention_days,
                "max_turns": self.config.tier1_max_turns,
            },
            "tier2": {
                "total_compressed": stats["compressed_total"],
                "events": stats["compressed_events"],
                "scenes": stats["compressed_scenes"],
                "arcs": stats["compressed_arcs"],
            },
            "identity_archive": {
                "anchors": stats["identity_anchors"],
                "self_experiences": stats["identity_self_experiences"],
                "governance_history": stats["identity_governance_history"],
            },
            "structure": structure,
        }

    # ── Compressed Memories Query ─────────────────────────────────

    async def search_compressed(self, request: dict):
        """Search compressed memories by type, topic, time range, or text.

        Default: excludes superseded, purged, and hidden entries, then sorts by
        weight DESC. Pass ``include_superseded`` or ``include_hidden`` when an
        explicit administrative view is required.
        """
        memory_type = request.get("memory_type")  # "event"|"scene"|"arc"|"epoch"
        topic = request.get("topic")
        query_text = request.get("query", "")
        start = request.get("timespan_start")
        end = request.get("timespan_end")
        try:
            limit = max(1, min(int(request.get("limit", 20)), 200))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="limit must be an integer",
            ) from exc
        min_weight = request.get("min_weight", 0.0)
        include_superseded = request.get("include_superseded", False)
        include_hidden = bool(request.get("include_hidden", False))
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
        if not include_hidden:
            sql += " AND hidden = 0"
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
        sql += (
            " ORDER BY pinned DESC, weight DESC, importance DESC, "
            "confidence DESC, timespan_start DESC LIMIT ?"
        )
        params.append(limit)

        def read_and_touch(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(sql, params).fetchall()
            results = [_cmem_row_to_dict(row) for row in rows]
            results.sort(key=lambda item: item.get("dynamic_weight", 0), reverse=True)
            now_iso = datetime.now().isoformat()
            for item in results[:limit]:
                conn.execute(
                    "UPDATE compressed_memories SET access_count = access_count + 1, "
                    "last_accessed_at = ? WHERE memory_id = ? AND "
                    "((owner_id = ? AND workspace_id = ?) OR "
                    "(owner_id = ? AND workspace_id = ?)) "
                    f"AND memory_domain IN ({domain_placeholders})",
                    (now_iso, item["memory_id"], scope.owner_id, scope.workspace_id,
                     GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID, *source_domains),
                )
            return results

        results = await self._repository_write_async(read_and_touch)
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
        promotions = self._repository_read(
            lambda conn: list_memory_promotions(
                conn,
                scope=scope,
                target_domains=target_domains,
                status="active",
                limit=500,
            )
        )
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
        source_payload = self._repository_read(
            lambda conn: recall_memories(
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
                graph_min_relevance=self.config.recall_graph_min_relevance,
            )
        )

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
        current_revision = getattr(self._repository, "commit_revision", 0)
        if request.min_revision is not None and current_revision < request.min_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "revision_not_available",
                    "current_revision": current_revision,
                    "min_revision": request.min_revision,
                    "retryable": True,
                },
            )
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
            payload = await self._repository_write_async(
                lambda conn: recall_memories(
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
                    graph_min_relevance=self.config.recall_graph_min_relevance,
                )
            )
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
            payload["context"] = format_recall_context(
                payload["results"],
                redact_sensitive=self.config.redact_before_store,
            )
            payload["trace_id"] = trace_id
            payload["recall_status"] = (
                "hit"
                if payload["count"]
                else (
                    "weak_match"
                    if int(payload.get("candidate_count") or 0) > 0
                    else "miss"
                )
            )
            payload["request_source"] = request.request_source
            payload["source_domains"] = list(source_domains)
            payload["commit_revision"] = getattr(
                self._repository, "commit_revision", 0
            )
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
                else (
                    str(payload.get("recall_status"))
                    if payload
                    else "miss"
                )
            )
            await self._persist_recall_trace(
                trace_id=trace_id,
                created_at=created_at,
                request=request,
                plan=plan.as_dict() if plan is not None else None,
                payload=payload,
                latency_ms=self._last_recall_latency_ms,
                failure=failure,
            )

    async def _persist_recall_trace(
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
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        source_domains = _authorized_read_domains(
            request.memory_actor, request.source_domains
        )
        try:
            await self._repository_write_async(
                lambda conn: conn.execute(
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
                        self._memory_storage_value(request.query),
                        status,
                        (plan or {}).get("intent"),
                        json.dumps(
                            self._memory_storage_value(plan or {}),
                            ensure_ascii=False,
                        ),
                        int((payload or {}).get("candidate_count") or 0),
                        int((payload or {}).get("count") or 0),
                        json.dumps(selected, ensure_ascii=False),
                        int((payload or {}).get("context_chars") or 0),
                        latency_ms,
                        type(failure).__name__ if failure is not None else None,
                        (
                            str(self._memory_storage_value(str(failure)))[:500]
                            if failure is not None
                            else None
                        ),
                        request.memory_actor.value,
                        scope.owner_id,
                        scope.workspace_id,
                        json.dumps(source_domains),
                    ),
                ),
            )
        except Exception:
            logger.warning("Failed to persist recall trace %s", trace_id, exc_info=True)

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
        rows = self._repository_read(
            lambda conn: conn.execute(
                "SELECT trace_id, created_at, completed_at, request_source, "
                "session_id, query, status, intent, query_plan, candidate_count, "
                "result_count, selected_results, context_chars, latency_ms, "
                "error_type, error_detail FROM recall_traces WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        )
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
        def write(conn: sqlite3.Connection) -> tuple[str, str]:
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
                    str(self._memory_storage_value(request.reason)).strip(),
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                    now,
                ),
            )
            return feedback_id, memory_domain

        feedback_id, memory_domain = await self._repository_write_async(write)
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
        target_kind = "memory" if memory_id else "session"
        target = memory_id or session_id
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            counts = {
                "compressed_memories": 0,
                "profile_memories": 0,
                "profile_memory_tombstones": 0,
                "compression_quality_audit": 0,
                "turns": 0,
                "turns_archive": 0,
                "sessions": 0,
                "recall_feedback": 0,
                "recall_traces": 0,
                "recall_trace_references": 0,
                "memory_embeddings": 0,
                "memory_promotions_revoked": 0,
                "memory_promotion_candidates_rejected": 0,
                "time_summaries": 0,
                "time_summary_links": 0,
                "session_summary_sources": 0,
            }
            turn_query = (
                "SELECT turn_id FROM turns WHERE {predicate} AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ? UNION SELECT turn_id "
                "FROM turns_archive WHERE {predicate} AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?"
            )
            predicate = "session_id = ?" if session_id else "turn_id = ?"
            turn_rows = conn.execute(
                turn_query.format(predicate=predicate),
                (
                    target,
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                    target,
                    scope.owner_id,
                    scope.workspace_id,
                    memory_domain,
                ),
            ).fetchall()
            turn_ids = {str(row[0]) for row in turn_rows}
            seed_references = {
                *turn_ids,
                *(f"turn:{turn_id}" for turn_id in turn_ids),
            }
            if session_id:
                seed_references.add(f"session:{session_id}")
            if memory_id:
                seed_references.add(memory_id)
            compressed_ids, profile_ids = _collect_dependent_memory_ids(
                conn,
                scope=scope,
                memory_domain=memory_domain,
                seed_references=seed_references,
                direct_memory_ids={memory_id} if memory_id else set(),
            )
            summary_seed_rows = []
            if session_id:
                summary_seed_rows = conn.execute(
                    "SELECT summary_id FROM time_summaries WHERE summary_type = 'session' "
                    "AND bucket_key = ? AND owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ?",
                    (
                        session_id,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                ).fetchall()
            elif memory_id:
                summary_seed_rows = conn.execute(
                    "SELECT summary_id FROM time_summaries WHERE summary_id = ? "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ? "
                    "UNION SELECT source.summary_id FROM session_summary_sources AS source "
                    "JOIN time_summaries AS summary ON summary.summary_id = source.summary_id "
                    "WHERE summary.owner_id = ? AND summary.workspace_id = ? "
                    "AND summary.memory_domain = ? "
                    "AND source.turn_id IN (SELECT value FROM json_each(?))",
                    (
                        memory_id,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        json.dumps(sorted(turn_ids)),
                    ),
                ).fetchall()
            summary_seed_ids = {str(row[0]) for row in summary_seed_rows}
            summary_ids: set[str] = set()
            if summary_seed_ids:
                placeholders = ",".join("?" for _ in summary_seed_ids)
                summary_ids = {
                    str(row[0])
                    for row in conn.execute(
                        "WITH RECURSIVE affected(summary_id) AS ("
                        f"SELECT summary_id FROM time_summaries WHERE summary_id IN ({placeholders}) "
                        "UNION SELECT sibling.summary_id FROM affected "
                        "JOIN time_summaries AS current ON current.summary_id = affected.summary_id "
                        "JOIN time_summaries AS sibling ON sibling.owner_id = current.owner_id "
                        "AND sibling.workspace_id = current.workspace_id "
                        "AND sibling.memory_domain = current.memory_domain "
                        "AND sibling.summary_type = current.summary_type "
                        "AND sibling.bucket_key = current.bucket_key "
                        "UNION SELECT link.parent_summary_id FROM affected "
                        "JOIN time_summary_links AS link "
                        "ON link.child_summary_id = affected.summary_id) "
                        "SELECT DISTINCT summary_id FROM affected",
                        tuple(sorted(summary_seed_ids)),
                    ).fetchall()
                }
            source_ids = {
                *turn_ids,
                *compressed_ids,
                *profile_ids,
                *summary_ids,
                *({memory_id} if memory_id else set()),
            }

            counts["memory_promotions_revoked"] = revoke_promotions_for_source(
                conn,
                source_memory_ids=sorted(source_ids),
                source_domain=memory_domain,
                scope=scope,
                revoked_by=request.memory_actor.value,
            )
            counts["memory_promotion_candidates_rejected"] = (
                reject_promotion_candidates_for_source(
                    conn,
                    source_memory_ids=sorted(source_ids),
                    source_domain=memory_domain,
                    scope=scope,
                )
            )

            trace_rows = conn.execute(
                "SELECT trace_id, session_id, source_domains, selected_results "
                "FROM recall_traces WHERE owner_id = ? AND workspace_id = ?",
                (scope.owner_id, scope.workspace_id),
            ).fetchall()
            trace_ids_to_delete = {
                str(trace_id)
                for trace_id, trace_session_id, source_domains, _ in trace_rows
                if session_id
                and str(trace_session_id or "") == session_id
                and memory_domain in _json_string_set(source_domains)
            }
            if trace_ids_to_delete:
                placeholders = ",".join("?" for _ in trace_ids_to_delete)
                cursor = conn.execute(
                    "DELETE FROM recall_feedback WHERE owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ? "
                    f"AND trace_id IN ({placeholders})",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(trace_ids_to_delete),
                    ),
                )
                counts["recall_feedback"] += max(0, int(cursor.rowcount or 0))
                cursor = conn.execute(
                    "DELETE FROM recall_traces WHERE owner_id = ? AND workspace_id = ? "
                    f"AND trace_id IN ({placeholders})",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        *sorted(trace_ids_to_delete),
                    ),
                )
                counts["recall_traces"] += max(0, int(cursor.rowcount or 0))

            for trace_id, _, _, selected_json in trace_rows:
                if str(trace_id) in trace_ids_to_delete:
                    continue
                try:
                    selected = json.loads(selected_json or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    selected = []
                if not isinstance(selected, list):
                    selected = []
                retained = [
                    item
                    for item in selected
                    if not isinstance(item, dict)
                    or (
                        str(item.get("id") or "") not in source_ids
                        and str(item.get("source_memory_id") or "") not in source_ids
                    )
                ]
                if len(retained) == len(selected):
                    continue
                conn.execute(
                    "UPDATE recall_traces SET selected_results = ?, result_count = ? "
                    "WHERE trace_id = ? AND owner_id = ? AND workspace_id = ?",
                    (
                        json.dumps(retained, ensure_ascii=False),
                        len(retained),
                        trace_id,
                        scope.owner_id,
                        scope.workspace_id,
                    ),
                )
                counts["recall_trace_references"] += 1

            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                cursor = conn.execute(
                    "DELETE FROM recall_feedback WHERE owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ? "
                    f"AND memory_id IN ({placeholders})",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(source_ids),
                    ),
                )
                counts["recall_feedback"] += max(0, int(cursor.rowcount or 0))
                cursor = conn.execute(
                    "DELETE FROM memory_embeddings WHERE owner_id = ? AND workspace_id = ? "
                    "AND memory_domain = ? "
                    f"AND memory_id IN ({placeholders})",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(source_ids),
                    ),
                )
                counts["memory_embeddings"] += max(0, int(cursor.rowcount or 0))

            for table, identifiers in (
                ("compressed_memories", compressed_ids),
                ("profile_memories", profile_ids),
            ):
                if not identifiers:
                    continue
                placeholders = ",".join("?" for _ in identifiers)
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE owner_id = ? AND workspace_id = ? "
                    f"AND memory_domain = ? AND memory_id IN ({placeholders})",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(identifiers),
                    ),
                )
                counts[table] += max(0, int(cursor.rowcount or 0))

            if summary_ids:
                placeholders = ",".join("?" for _ in summary_ids)
                cursor = conn.execute(
                    "DELETE FROM time_summary_links WHERE "
                    f"parent_summary_id IN ({placeholders}) OR "
                    f"child_summary_id IN ({placeholders})",
                    (*sorted(summary_ids), *sorted(summary_ids)),
                )
                counts["time_summary_links"] += max(0, int(cursor.rowcount or 0))
                cursor = conn.execute(
                    "DELETE FROM session_summary_sources WHERE "
                    f"summary_id IN ({placeholders})",
                    tuple(sorted(summary_ids)),
                )
                counts["session_summary_sources"] += max(
                    0,
                    int(cursor.rowcount or 0),
                )
                cursor = conn.execute(
                    "DELETE FROM time_summaries WHERE owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ? "
                    f"AND summary_id IN ({placeholders})",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(summary_ids),
                    ),
                )
                counts["time_summaries"] += max(0, int(cursor.rowcount or 0))

            if turn_ids:
                placeholders = ",".join("?" for _ in turn_ids)
                cursor = conn.execute(
                    "DELETE FROM profile_memory_tombstones WHERE owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ? AND ("
                    f"source_turn_id IN ({placeholders}) OR EXISTS (SELECT 1 FROM "
                    "json_each(profile_memory_tombstones.evidence_turns) "
                    f"WHERE value IN ({placeholders})))",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(turn_ids),
                        *sorted(turn_ids),
                    ),
                )
                counts["profile_memory_tombstones"] += max(0, int(cursor.rowcount or 0))
                cursor = conn.execute(
                    "DELETE FROM compression_quality_audit WHERE owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ? AND EXISTS (SELECT 1 FROM "
                    "json_each(compression_quality_audit.sample_turn_ids) "
                    f"WHERE value IN ({placeholders}))",
                    (
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                        *sorted(turn_ids),
                    ),
                )
                counts["compression_quality_audit"] += max(0, int(cursor.rowcount or 0))
                for table in ("turns", "turns_archive"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE owner_id = ? AND workspace_id = ? "
                        f"AND memory_domain = ? AND turn_id IN ({placeholders})",
                        (
                            scope.owner_id,
                            scope.workspace_id,
                            memory_domain,
                            *sorted(turn_ids),
                        ),
                    )
                    counts[table] += max(0, int(cursor.rowcount or 0))
            if session_id:
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE session_id = ? AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ?",
                    (session_id, scope.owner_id, scope.workspace_id, memory_domain),
                )
                counts["sessions"] += max(0, int(cursor.rowcount or 0))

            from memai.indexes.entity_graph import rebuild_entity_graph

            rebuild_entity_graph(
                conn,
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                memory_domain=memory_domain,
            )
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
                    str(self._memory_storage_value(request.reason)).strip(),
                    json.dumps(counts, sort_keys=True),
                    scope.owner_id,
                    scope.workspace_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return {
                "status": "forgotten",
                "audit_id": audit_id,
                "target_kind": target_kind,
                "memory_domain": memory_domain,
                "deleted_counts": counts,
                **scope.as_dict(),
            }

        return await self._repository_write_async(write)

    async def remember(self, request: DurableMemoryCreate):
        """Persist an explicit durable memory in the canonical Mem store."""
        scope = MemoryScope.create(request.owner_id, request.workspace_id)
        memory_domain = _authorized_write_domain(
            request.memory_actor, request.memory_domain
        )
        title = str(self._memory_storage_value(request.title)).strip()
        summary = str(self._memory_storage_value(request.summary)).strip()
        evidence_refs = list(
            dict.fromkeys(
                str(item).strip()
                for item in self._memory_storage_value(request.evidence_refs)
            )
        )
        evidence_refs = [item for item in evidence_refs if item]
        source_actor = str(
            self._memory_storage_value(request.source_actor)
        ).strip()
        event_kind = str(self._memory_storage_value(request.event_kind)).strip()
        supersedes_memory_ids = list(
            dict.fromkeys(
                str(item).strip() for item in request.supersedes_memory_ids
            )
        )
        supersedes_memory_ids = [item for item in supersedes_memory_ids if item]
        topics = list(dict.fromkeys(self._memory_storage_value(request.topics)))
        entities = list(dict.fromkeys(self._memory_storage_value(request.entities)))
        identity = json.dumps(
            {
                "title": title,
                "summary": summary,
                "evidence_refs": evidence_refs,
                "supersedes_memory_ids": supersedes_memory_ids,
                "source_actor": source_actor,
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
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            if supersedes_memory_ids:
                placeholders = ",".join("?" for _ in supersedes_memory_ids)
                rows = conn.execute(
                    "SELECT memory_id, status, superseded_by FROM compressed_memories "
                    f"WHERE memory_id IN ({placeholders}) "
                    "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                    (
                        *supersedes_memory_ids,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                ).fetchall()
                valid = {
                    str(row[0])
                    for row in rows
                    if str(row[1]) == "active" or str(row[2] or "") == memory_id
                }
                missing = [
                    target_id
                    for target_id in supersedes_memory_ids
                    if target_id not in valid
                ]
                if missing:
                    raise HTTPException(
                        status_code=409,
                        detail="Superseded memories must be active in the same scope: "
                        + ", ".join(missing),
                    )
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
                "event_kind = excluded.event_kind, evidence_refs = excluded.evidence_refs",
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
                    event_kind,
                    json.dumps(evidence_refs, ensure_ascii=False),
                    f"{source_actor}:{memory_id}",
                    now,
                    scope.owner_id,
                    scope.workspace_id,
                    now,
                ),
            )
            if supersedes_memory_ids:
                conn.execute(
                    "UPDATE compressed_memories SET status = 'superseded', "
                    "superseded_by = ?, weight = weight * 0.3 "
                    f"WHERE memory_id IN ({placeholders}) AND owner_id = ? "
                    "AND workspace_id = ? AND memory_domain = ? AND status = 'active'",
                    (
                        memory_id,
                        *supersedes_memory_ids,
                        scope.owner_id,
                        scope.workspace_id,
                        memory_domain,
                    ),
                )
            from memai.indexes.entity_graph import rebuild_entity_graph

            rebuild_entity_graph(
                conn,
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                memory_domain=memory_domain,
            )
            row = conn.execute(
                f"SELECT {_CMEM_COLUMNS} FROM compressed_memories WHERE memory_id = ? "
                "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, memory_domain),
            ).fetchone()
            return _cmem_row_to_dict(row)

        if request.idempotency_key:
            fingerprint_payload = {
                "title": title,
                "summary": summary,
                "topics": topics,
                "entities": entities,
                "evidence_refs": evidence_refs,
                "supersedes_memory_ids": supersedes_memory_ids,
                "event_kind": event_kind,
                "importance": request.importance,
                "source_actor": source_actor,
                "owner_id": scope.owner_id,
                "workspace_id": scope.workspace_id,
                "memory_domain": memory_domain,
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    fingerprint_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            receipt = await self._repository_idempotent_write_async(
                receipt_key=(
                    f"remember:{scope.owner_id}:{scope.workspace_id}:"
                    f"{memory_domain}:{request.idempotency_key}"
                ),
                operation="remember",
                fingerprint=fingerprint,
                owner_id=scope.owner_id,
                workspace_id=scope.workspace_id,
                memory_domain=memory_domain,
                callback=write,
            )
            memory = dict(receipt.value)
            self._semantic_wake.set()
            return {
                "status": "remembered",
                "memory": memory,
                "write_status": "committed",
                "commit_revision": receipt.commit_revision,
                "replayed": receipt.replay,
            }

        memory = await self._repository_write_async(write)
        self._semantic_wake.set()
        return {
            "status": "remembered",
            "memory": memory,
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
        cache_key = (
            "compressed",
            memory_id,
            scope.owner_id,
            scope.workspace_id,
            authorized_domain,
        )
        cached = self._cached_read(cache_key)
        if cached is not None:
            return cached
        row = self._repository_read(
            lambda conn: conn.execute(
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
        )
        if not row:
            raise HTTPException(status_code=404, detail="Compressed memory not found")
        payload = _cmem_row_to_dict(row)
        payload["commit_revision"] = getattr(self._repository, "commit_revision", 0)
        return self._store_cached_read(cache_key, payload)

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
        rows = self._repository_read(
            lambda conn: conn.execute(
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
        )
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
        from memai.application.identity_seed import is_founding_memory_id

        if is_founding_memory_id(memory_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Founding identity is canonical and read-only; submit an "
                    "identity revision proposal with evidence"
                ),
            )

    @staticmethod
    def _rebuild_scoped_entity_graph(
        conn: sqlite3.Connection,
        *,
        scope: MemoryScope,
        memory_domain: str,
    ) -> None:
        from memai.indexes.entity_graph import rebuild_entity_graph

        rebuild_entity_graph(
            conn,
            owner_id=scope.owner_id,
            workspace_id=scope.workspace_id,
            memory_domain=memory_domain,
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
        def write(conn: sqlite3.Connection) -> None:
            cur = conn.execute(
                "UPDATE compressed_memories SET pinned = 1, hidden = 0, "
                "weight = 1.0 WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Memory not found")
            self._rebuild_scoped_entity_graph(
                conn,
                scope=scope,
                memory_domain=authorized_domain,
            )

        await self._repository_write_async(write)
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
        def write(conn: sqlite3.Connection) -> None:
            cur = conn.execute(
                "UPDATE compressed_memories SET hidden = 1, pinned = 0, "
                "weight = 0.0 WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Memory not found")
            self._rebuild_scoped_entity_graph(
                conn,
                scope=scope,
                memory_domain=authorized_domain,
            )

        await self._repository_write_async(write)
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
        def write(conn: sqlite3.Connection) -> float:
            row = conn.execute(
                "SELECT memory_type, compression_level FROM compressed_memories "
                "WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?",
                (memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Memory not found")
            _mem_type, level = row[0], row[1] or 0
            base_w = self._LEVEL_WEIGHT.get(level, 0.2)
            conn.execute(
                "UPDATE compressed_memories SET pinned = 0, hidden = 0, "
                "weight = ? WHERE memory_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?",
                (base_w, memory_id, scope.owner_id, scope.workspace_id, authorized_domain),
            )
            self._rebuild_scoped_entity_graph(
                conn,
                scope=scope,
                memory_domain=authorized_domain,
            )
            return base_w

        base_w = await self._repository_write_async(write)
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

    def __init__(
        self,
        config: MemoryServiceConfig = None,
        *,
        repository: MemoryRepository | None = None,
    ):
        super().__init__(config, repository=repository)
        self.app = build_memory_http_app(
            self._http_handlers(),
            lifespan=asynccontextmanager(self._app_lifespan),
            service_token=self.config.service_token,
            service_tokens=self.config.service_tokens,
            service_actor="api_a",
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
    from memai.repository.paths import get_mem_runtime_layout
    
    parser = argparse.ArgumentParser(description="VoidCube Memory Service")
    parser.add_argument("--host", default="127.0.0.1", help="Service host")
    parser.add_argument("--port", type=int, default=6001, help="Service port")
    parser.add_argument(
        "--db-path",
        default=str(get_mem_runtime_layout().memory_db),
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
