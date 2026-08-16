"""Auditable cross-domain memory promotion references.

Promotion never copies source memory content. It records a governed reference
that may be dereferenced during recall through the target domain.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field, field_validator

from memai.domain.domain import (
    MemoryActor,
    MemoryDomain,
    MemoryDomainAccessError,
    authorize_read,
)
from memai.domain.scope import (
    DEFAULT_OWNER_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_SCOPE_ID,
    MemoryScope,
)


class MemorySourceType(str, Enum):
    TURN = "turn"
    ARCHIVE = "archive"
    COMPRESSED = "compressed"
    PROFILE = "profile"


class MemoryPromotionCandidateCreate(BaseModel):
    source_memory_id: str = Field(min_length=1, max_length=300)
    source_type: MemorySourceType
    source_domain: MemoryDomain
    target_domain: MemoryDomain
    reason: str = Field(min_length=8, max_length=2000)
    governance_ref: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = MemoryActor.GOVERNOR

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class MemoryPromotionConsent(BaseModel):
    approved: bool
    reason: str = Field(min_length=3, max_length=2000)
    consented_by: Literal["local-owner"] = "local-owner"
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = MemoryActor.GOVERNOR


class MemoryPromotionRevoke(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    revoked_by: str = Field(min_length=1, max_length=100)
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = MemoryActor.MEMORY_MAINTENANCE


class MemoryPromotionAccessError(ValueError):
    """Raised when an actor cannot manage promotion references."""


class MemoryPromotionValidationError(ValueError):
    """Raised when a promotion violates the fixed domain policy."""


class MemoryPromotionConflictError(ValueError):
    """Raised when an equivalent active promotion already exists."""


class MemoryPromotionNotFoundError(LookupError):
    """Raised when a promotion or source record cannot be found."""


_PROMOTION_MANAGERS = frozenset(
    {MemoryActor.MEMORY_MAINTENANCE, MemoryActor.GOVERNOR}
)

# Promotion is intentionally one-way into COMPANION. Private companion memories
# cannot flow back into agent or evolution domains.
_ALLOWED_PROMOTION_TARGETS: dict[MemoryDomain, frozenset[MemoryDomain]] = {
    MemoryDomain.AGENT_INTERACTION: frozenset({MemoryDomain.COMPANION}),
    MemoryDomain.COMPANION: frozenset(),
    MemoryDomain.EVOLUTION: frozenset({MemoryDomain.COMPANION}),
}

_SOURCE_TABLES: dict[MemorySourceType, tuple[str, str, str]] = {
    MemorySourceType.TURN: ("turns", "turn_id", ""),
    MemorySourceType.ARCHIVE: ("turns_archive", "turn_id", ""),
    MemorySourceType.COMPRESSED: (
        "compressed_memories",
        "memory_id",
        "AND status = 'active' AND hidden = 0",
    ),
    MemorySourceType.PROFILE: (
        "profile_memories",
        "memory_id",
        "AND status = 'active'",
    ),
}


def authorize_promotion_manager(actor: MemoryActor | str) -> MemoryActor:
    resolved = MemoryActor(actor)
    if resolved not in _PROMOTION_MANAGERS:
        raise MemoryPromotionAccessError(
            f"{resolved.value} cannot manage memory promotion references"
        )
    return resolved


def validate_promotion_pair(
    source_domain: MemoryDomain | str,
    target_domain: MemoryDomain | str,
) -> tuple[MemoryDomain, MemoryDomain]:
    source = MemoryDomain(source_domain)
    target = MemoryDomain(target_domain)
    if target not in _ALLOWED_PROMOTION_TARGETS[source]:
        raise MemoryPromotionValidationError(
            f"memory promotion from {source.value} to {target.value} is not allowed"
        )
    return source, target


def setup_memory_promotion_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_promotion_candidates ("
        "candidate_id TEXT PRIMARY KEY, "
        "source_type TEXT NOT NULL, source_memory_id TEXT NOT NULL, "
        "source_domain TEXT NOT NULL, target_domain TEXT NOT NULL, "
        "reason TEXT NOT NULL, proposed_by TEXT NOT NULL, "
        "governance_ref TEXT NOT NULL, status TEXT NOT NULL "
        "DEFAULT 'awaiting_user_consent', requested_at TEXT NOT NULL, "
        "expires_at TEXT, consented_at TEXT, consented_by TEXT, "
        "consent_reason TEXT, promotion_id TEXT UNIQUE, "
        "owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, "
        "CHECK(source_domain != target_domain), "
        "CHECK(status IN ('awaiting_user_consent', 'approved', 'rejected')))"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_promotion_candidate_pending_unique "
        "ON memory_promotion_candidates(owner_id, workspace_id, source_type, "
        "source_memory_id, source_domain, target_domain) "
        "WHERE status = 'awaiting_user_consent'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_promotion_candidate_status "
        "ON memory_promotion_candidates(owner_id, workspace_id, source_domain, "
        "target_domain, status, requested_at DESC)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_promotion_refs ("
        "promotion_id TEXT PRIMARY KEY, "
        "source_type TEXT NOT NULL, source_memory_id TEXT NOT NULL, "
        "source_domain TEXT NOT NULL, target_domain TEXT NOT NULL, "
        "reason TEXT NOT NULL, approved_by TEXT NOT NULL, approval_ref TEXT, "
        "created_by TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', "
        "created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, "
        "revoked_by TEXT, revoke_reason TEXT, "
        "owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, "
        "CHECK(source_domain != target_domain), "
        "CHECK(status IN ('active', 'revoked', 'expired')))"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_promotion_active_unique "
        "ON memory_promotion_refs(owner_id, workspace_id, source_type, "
        "source_memory_id, source_domain, target_domain) WHERE status = 'active'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_promotion_target_active "
        "ON memory_promotion_refs(owner_id, workspace_id, target_domain, "
        "status, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_promotion_source "
        "ON memory_promotion_refs(owner_id, workspace_id, source_domain, "
        "source_type, source_memory_id, status)"
    )


def expire_memory_promotions(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    cursor = conn.execute(
        "UPDATE memory_promotion_refs SET status = 'expired' "
        "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
        (timestamp,),
    )
    return max(0, int(cursor.rowcount or 0))


def source_memory_exists(
    conn: sqlite3.Connection,
    *,
    source_type: MemorySourceType,
    source_memory_id: str,
    source_domain: MemoryDomain,
    scope: MemoryScope,
) -> bool:
    """Check a private or globally visible source without widening ref scope."""
    table, id_column, active_clause = _SOURCE_TABLES[source_type]
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE {id_column} = ? "
        "AND ((owner_id = ? AND workspace_id = ?) OR "
        "(owner_id = ? AND workspace_id = ?)) AND memory_domain = ? "
        f"{active_clause} LIMIT 1",
        (
            source_memory_id,
            scope.owner_id,
            scope.workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
            source_domain.value,
        ),
    ).fetchone()
    return row is not None


def create_memory_promotion_candidate(
    conn: sqlite3.Connection,
    request: MemoryPromotionCandidateCreate,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    actor = authorize_promotion_manager(request.memory_actor)
    source_domain, target_domain = validate_promotion_pair(
        request.source_domain,
        request.target_domain,
    )
    try:
        authorize_read(actor, [source_domain])
    except MemoryDomainAccessError as exc:
        raise MemoryPromotionAccessError(str(exc)) from exc
    scope = MemoryScope.create(request.owner_id, request.workspace_id)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expire_memory_promotions(conn, now=current)
    if request.expires_at is not None:
        expires_at = request.expires_at.astimezone(timezone.utc)
        if expires_at <= current:
            raise MemoryPromotionValidationError("expires_at must be in the future")
    else:
        expires_at = None
    if not source_memory_exists(
        conn,
        source_type=request.source_type,
        source_memory_id=request.source_memory_id,
        source_domain=source_domain,
        scope=scope,
    ):
        raise MemoryPromotionNotFoundError("promotion source memory not found")

    existing = conn.execute(
        "SELECT promotion_id FROM memory_promotion_refs WHERE owner_id = ? "
        "AND workspace_id = ? AND source_type = ? AND source_memory_id = ? "
        "AND source_domain = ? AND target_domain = ? AND status = 'active'",
        (
            scope.owner_id,
            scope.workspace_id,
            request.source_type.value,
            request.source_memory_id,
            source_domain.value,
            target_domain.value,
        ),
    ).fetchone()
    if existing:
        raise MemoryPromotionConflictError(
            f"active memory promotion already exists: {existing[0]}"
        )

    pending = conn.execute(
        "SELECT candidate_id FROM memory_promotion_candidates WHERE owner_id = ? "
        "AND workspace_id = ? AND source_type = ? AND source_memory_id = ? "
        "AND source_domain = ? AND target_domain = ? "
        "AND status = 'awaiting_user_consent'",
        (
            scope.owner_id,
            scope.workspace_id,
            request.source_type.value,
            request.source_memory_id,
            source_domain.value,
            target_domain.value,
        ),
    ).fetchone()
    if pending:
        raise MemoryPromotionConflictError(
            f"pending memory promotion candidate already exists: {pending[0]}"
        )

    candidate_id = f"promotion-candidate-{uuid.uuid4()}"
    try:
        conn.execute(
            "INSERT INTO memory_promotion_candidates (candidate_id, source_type, "
            "source_memory_id, source_domain, target_domain, reason, proposed_by, "
            "governance_ref, status, requested_at, expires_at, owner_id, "
            "workspace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
            "'awaiting_user_consent', ?, ?, ?, ?)",
            (
                candidate_id,
                request.source_type.value,
                request.source_memory_id,
                source_domain.value,
                target_domain.value,
                request.reason.strip(),
                actor.value,
                request.governance_ref.strip(),
                current.isoformat(),
                expires_at.isoformat() if expires_at else None,
                scope.owner_id,
                scope.workspace_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise MemoryPromotionConflictError(
            "equivalent pending memory promotion candidate already exists"
        ) from exc
    row = conn.execute(
        "SELECT * FROM memory_promotion_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return promotion_candidate_row_to_dict(row)


def list_memory_promotion_candidates(
    conn: sqlite3.Connection,
    *,
    scope: MemoryScope,
    source_domain: MemoryDomain | str | None = None,
    target_domain: MemoryDomain | str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["owner_id = ?", "workspace_id = ?"]
    params: list[Any] = [scope.owner_id, scope.workspace_id]
    if source_domain is not None:
        clauses.append("source_domain = ?")
        params.append(MemoryDomain(source_domain).value)
    if target_domain is not None:
        clauses.append("target_domain = ?")
        params.append(MemoryDomain(target_domain).value)
    if status:
        normalized_status = str(status).strip().lower()
        if normalized_status not in {
            "awaiting_user_consent",
            "approved",
            "rejected",
        }:
            raise MemoryPromotionValidationError("invalid promotion candidate status")
        clauses.append("status = ?")
        params.append(normalized_status)
    params.append(max(1, min(int(limit), 500)))
    rows = conn.execute(
        "SELECT * FROM memory_promotion_candidates WHERE "
        + " AND ".join(clauses)
        + " ORDER BY requested_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [promotion_candidate_row_to_dict(row) for row in rows]


def consent_memory_promotion_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    request: MemoryPromotionConsent,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    actor = authorize_promotion_manager(request.memory_actor)
    scope = MemoryScope.create(request.owner_id, request.workspace_id)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expire_memory_promotions(conn, now=current)
    row = conn.execute(
        "SELECT * FROM memory_promotion_candidates WHERE candidate_id = ? "
        "AND owner_id = ? AND workspace_id = ?",
        (candidate_id, scope.owner_id, scope.workspace_id),
    ).fetchone()
    if not row:
        raise MemoryPromotionNotFoundError("memory promotion candidate not found")
    candidate = promotion_candidate_row_to_dict(row)
    if candidate["status"] != "awaiting_user_consent":
        raise MemoryPromotionConflictError(
            f"memory promotion candidate is already {candidate['status']}"
        )

    promotion: dict[str, Any] | None = None
    promotion_id: str | None = None
    if request.approved:
        expires_at_text = candidate.get("expires_at")
        if expires_at_text:
            expires_at = datetime.fromisoformat(str(expires_at_text)).astimezone(
                timezone.utc
            )
            if expires_at <= current:
                raise MemoryPromotionConflictError(
                    "memory promotion candidate validity has expired"
                )
        source_type = MemorySourceType(candidate["source_type"])
        source_domain, target_domain = validate_promotion_pair(
            candidate["source_domain"], candidate["target_domain"]
        )
        if not source_memory_exists(
            conn,
            source_type=source_type,
            source_memory_id=candidate["source_memory_id"],
            source_domain=source_domain,
            scope=scope,
        ):
            raise MemoryPromotionNotFoundError("promotion source memory not found")
        existing = conn.execute(
            "SELECT promotion_id FROM memory_promotion_refs WHERE owner_id = ? "
            "AND workspace_id = ? AND source_type = ? AND source_memory_id = ? "
            "AND source_domain = ? AND target_domain = ? AND status = 'active'",
            (
                scope.owner_id,
                scope.workspace_id,
                source_type.value,
                candidate["source_memory_id"],
                source_domain.value,
                target_domain.value,
            ),
        ).fetchone()
        if existing:
            raise MemoryPromotionConflictError(
                f"active memory promotion already exists: {existing[0]}"
            )
        promotion_id = f"promotion-{uuid.uuid4()}"
        try:
            conn.execute(
                "INSERT INTO memory_promotion_refs (promotion_id, source_type, "
                "source_memory_id, source_domain, target_domain, reason, approved_by, "
                "approval_ref, created_by, status, created_at, expires_at, owner_id, "
                "workspace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    promotion_id,
                    source_type.value,
                    candidate["source_memory_id"],
                    source_domain.value,
                    target_domain.value,
                    candidate["reason"],
                    request.consented_by,
                    candidate["governance_ref"],
                    actor.value,
                    current.isoformat(),
                    candidate.get("expires_at"),
                    scope.owner_id,
                    scope.workspace_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise MemoryPromotionConflictError(
                "equivalent active memory promotion already exists"
            ) from exc

    status = "approved" if request.approved else "rejected"
    cursor = conn.execute(
        "UPDATE memory_promotion_candidates SET status = ?, consented_at = ?, "
        "consented_by = ?, consent_reason = ?, promotion_id = ? "
        "WHERE candidate_id = ? AND status = 'awaiting_user_consent'",
        (
            status,
            current.isoformat(),
            request.consented_by,
            request.reason.strip(),
            promotion_id,
            candidate_id,
        ),
    )
    if int(cursor.rowcount or 0) != 1:
        raise MemoryPromotionConflictError(
            "memory promotion candidate was decided concurrently"
        )
    updated = conn.execute(
        "SELECT * FROM memory_promotion_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if promotion_id:
        promotion_row = conn.execute(
            "SELECT * FROM memory_promotion_refs WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        promotion = promotion_row_to_dict(promotion_row)
    return promotion_candidate_row_to_dict(updated), promotion


def list_memory_promotions(
    conn: sqlite3.Connection,
    *,
    scope: MemoryScope,
    target_domains: Sequence[MemoryDomain | str] | None = None,
    status: str | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    expire_memory_promotions(conn, now=now)
    clauses = ["owner_id = ?", "workspace_id = ?"]
    params: list[Any] = [scope.owner_id, scope.workspace_id]
    domains = tuple(dict.fromkeys(MemoryDomain(item).value for item in target_domains or ()))
    if domains:
        placeholders = ",".join("?" for _ in domains)
        clauses.append(f"target_domain IN ({placeholders})")
        params.extend(domains)
    if status:
        normalized_status = str(status).strip().lower()
        if normalized_status not in {"active", "revoked", "expired"}:
            raise MemoryPromotionValidationError("invalid promotion status")
        clauses.append("status = ?")
        params.append(normalized_status)
    params.append(max(1, min(int(limit), 500)))
    rows = conn.execute(
        "SELECT * FROM memory_promotion_refs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [promotion_row_to_dict(row) for row in rows]


def revoke_memory_promotion(
    conn: sqlite3.Connection,
    promotion_id: str,
    request: MemoryPromotionRevoke,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    authorize_promotion_manager(request.memory_actor)
    scope = MemoryScope.create(request.owner_id, request.workspace_id)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expire_memory_promotions(conn, now=current)
    row = conn.execute(
        "SELECT status FROM memory_promotion_refs WHERE promotion_id = ? "
        "AND owner_id = ? AND workspace_id = ?",
        (promotion_id, scope.owner_id, scope.workspace_id),
    ).fetchone()
    if not row:
        raise MemoryPromotionNotFoundError("memory promotion not found")
    if str(row[0]) != "active":
        raise MemoryPromotionConflictError(
            f"memory promotion is already {row[0]}"
        )
    conn.execute(
        "UPDATE memory_promotion_refs SET status = 'revoked', revoked_at = ?, "
        "revoked_by = ?, revoke_reason = ? WHERE promotion_id = ?",
        (
            current.isoformat(),
            request.revoked_by.strip(),
            request.reason.strip(),
            promotion_id,
        ),
    )
    updated = conn.execute(
        "SELECT * FROM memory_promotion_refs WHERE promotion_id = ?",
        (promotion_id,),
    ).fetchone()
    return promotion_row_to_dict(updated)


def revoke_promotions_for_source(
    conn: sqlite3.Connection,
    *,
    source_memory_ids: Sequence[str],
    source_domain: MemoryDomain | str,
    scope: MemoryScope,
    revoked_by: str,
    reason: str = "source_memory_deleted",
    now: datetime | None = None,
) -> int:
    ids = tuple(dict.fromkeys(str(item) for item in source_memory_ids if str(item)))
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    cursor = conn.execute(
        "UPDATE memory_promotion_refs SET status = 'revoked', revoked_at = ?, "
        "revoked_by = ?, revoke_reason = ? WHERE owner_id = ? AND workspace_id = ? "
        "AND source_domain = ? AND status = 'active' "
        f"AND source_memory_id IN ({placeholders})",
        (
            current,
            revoked_by,
            reason,
            scope.owner_id,
            scope.workspace_id,
            MemoryDomain(source_domain).value,
            *ids,
        ),
    )
    return max(0, int(cursor.rowcount or 0))


def reject_promotion_candidates_for_source(
    conn: sqlite3.Connection,
    *,
    source_memory_ids: Sequence[str],
    source_domain: MemoryDomain | str,
    scope: MemoryScope,
    reason: str = "source_memory_deleted",
    now: datetime | None = None,
) -> int:
    ids = tuple(dict.fromkeys(str(item) for item in source_memory_ids if str(item)))
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    cursor = conn.execute(
        "UPDATE memory_promotion_candidates SET status = 'rejected', "
        "consented_at = ?, consented_by = 'memory-maintenance', "
        "consent_reason = ? WHERE owner_id = ? AND workspace_id = ? "
        "AND source_domain = ? AND status = 'awaiting_user_consent' "
        f"AND source_memory_id IN ({placeholders})",
        (
            current,
            reason,
            scope.owner_id,
            scope.workspace_id,
            MemoryDomain(source_domain).value,
            *ids,
        ),
    )
    return max(0, int(cursor.rowcount or 0))


def promotion_candidate_row_to_dict(
    row: sqlite3.Row | Sequence[Any],
) -> dict[str, Any]:
    return {
        "candidate_id": row[0],
        "source_type": row[1],
        "source_memory_id": row[2],
        "source_domain": row[3],
        "target_domain": row[4],
        "reason": row[5],
        "proposed_by": row[6],
        "governance_ref": row[7],
        "status": row[8],
        "requested_at": row[9],
        "expires_at": row[10],
        "consented_at": row[11],
        "consented_by": row[12],
        "consent_reason": row[13],
        "promotion_id": row[14],
        "owner_id": row[15],
        "workspace_id": row[16],
    }


def promotion_row_to_dict(row: sqlite3.Row | Sequence[Any]) -> dict[str, Any]:
    return {
        "promotion_id": row[0],
        "source_type": row[1],
        "source_memory_id": row[2],
        "source_domain": row[3],
        "target_domain": row[4],
        "reason": row[5],
        "approved_by": row[6],
        "approval_ref": row[7],
        "created_by": row[8],
        "status": row[9],
        "created_at": row[10],
        "expires_at": row[11],
        "revoked_at": row[12],
        "revoked_by": row[13],
        "revoke_reason": row[14],
        "owner_id": row[15],
        "workspace_id": row[16],
    }


def promotion_source_key(item: dict[str, Any]) -> tuple[str, str, str]:
    tier = str(item.get("tier") or "")
    source_type = {
        "tier1": MemorySourceType.TURN.value,
        "archive": MemorySourceType.ARCHIVE.value,
        "tier2": MemorySourceType.COMPRESSED.value,
        "profile": MemorySourceType.PROFILE.value,
    }.get(tier, "")
    return (
        source_type,
        str(item.get("id") or ""),
        str(item.get("memory_domain") or ""),
    )
