"""Auditable cross-domain memory promotion references.

Promotion never copies source memory content. It records a governed reference
that may be dereferenced during recall through the target domain.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, Field, field_validator

from systems.memory.domain import MemoryActor, MemoryDomain
from systems.memory.scope import (
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


class MemoryPromotionCreate(BaseModel):
    source_memory_id: str = Field(min_length=1, max_length=300)
    source_type: MemorySourceType
    source_domain: MemoryDomain
    target_domain: MemoryDomain
    reason: str = Field(min_length=8, max_length=2000)
    approved_by: str = Field(min_length=1, max_length=100)
    approval_ref: str = Field(default="", max_length=500)
    expires_at: datetime | None = None
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    memory_actor: MemoryActor = MemoryActor.MEMORY_MAINTENANCE

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


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


def create_memory_promotion(
    conn: sqlite3.Connection,
    request: MemoryPromotionCreate,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    actor = authorize_promotion_manager(request.memory_actor)
    source_domain, target_domain = validate_promotion_pair(
        request.source_domain,
        request.target_domain,
    )
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

    promotion_id = f"promotion-{uuid.uuid4()}"
    try:
        conn.execute(
            "INSERT INTO memory_promotion_refs (promotion_id, source_type, "
            "source_memory_id, source_domain, target_domain, reason, approved_by, "
            "approval_ref, created_by, status, created_at, expires_at, owner_id, "
            "workspace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (
                promotion_id,
                request.source_type.value,
                request.source_memory_id,
                source_domain.value,
                target_domain.value,
                request.reason.strip(),
                request.approved_by.strip(),
                request.approval_ref.strip() or None,
                actor.value,
                current.isoformat(),
                expires_at.isoformat() if expires_at else None,
                scope.owner_id,
                scope.workspace_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise MemoryPromotionConflictError(
            "equivalent active memory promotion already exists"
        ) from exc
    row = conn.execute(
        "SELECT * FROM memory_promotion_refs WHERE promotion_id = ?",
        (promotion_id,),
    ).fetchone()
    return promotion_row_to_dict(row)


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
