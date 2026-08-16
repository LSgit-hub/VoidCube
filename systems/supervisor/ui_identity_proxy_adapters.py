"""Memory identity and promotion HTTP adapters for the Supervisor UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from fastapi import HTTPException

from systems.memory.scope import CLI_WORKSPACE_ID, DEFAULT_OWNER_ID


_IDENTITY_MEMORY_ACTOR = "stellar_companion"
_IDENTITY_MEMORY_DOMAIN = "agent_interaction"


@dataclass(frozen=True, slots=True)
class SupervisorUIIdentityProxyContext:
    """Runtime resources needed by the UI's canonical-memory proxy routes."""

    gateway_url: str
    gateway_memory_headers: Callable[..., Dict[str, str]]


async def get_identity_archive(
    *,
    context: SupervisorUIIdentityProxyContext,
) -> Dict[str, Any]:
    """Proxy the canonical Mem archive without creating UI-owned identity state."""

    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{context.gateway_url}/api/mem/identity/archive",
                params={
                    "owner_id": DEFAULT_OWNER_ID,
                    "workspace_id": CLI_WORKSPACE_ID,
                },
                headers=context.gateway_memory_headers(
                    memory_actor=_IDENTITY_MEMORY_ACTOR
                ),
            ) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=503,
                        detail="Memory identity archive unavailable",
                    )
                return await response.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Memory identity archive unavailable: {type(exc).__name__}",
        ) from exc


async def get_identity_turns(
    *,
    context: SupervisorUIIdentityProxyContext,
    limit: int = 20,
) -> Dict[str, Any]:
    """Return recent Tier 1 turns for explicit identity verification in the room UI."""

    try:
        import aiohttp

        bounded_limit = max(1, min(int(limit), 50))
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{context.gateway_url}/api/mem/turns",
                params={
                    "limit": bounded_limit,
                    "newest_first": "true",
                    "owner_id": DEFAULT_OWNER_ID,
                    "workspace_id": CLI_WORKSPACE_ID,
                    "memory_domain": _IDENTITY_MEMORY_DOMAIN,
                },
                headers=context.gateway_memory_headers(
                    memory_actor=_IDENTITY_MEMORY_ACTOR
                ),
            ) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=503,
                        detail="Memory turns unavailable",
                    )
                payload = await response.json()
                turns = list(payload.get("turns") or [])
                return {"turns": turns, "count": len(turns)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Memory turns unavailable: {type(exc).__name__}",
        ) from exc


async def get_evolution_promotion_audit(
    *,
    context: SupervisorUIIdentityProxyContext,
    limit: int = 100,
) -> Dict[str, Any]:
    """Expose read-only evolution-to-companion promotion metadata."""

    bounded_limit = max(1, min(int(limit), 500))
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{context.gateway_url}/api/mem/promotions",
                params={"limit": 500, "target_domain": "companion"},
                headers=context.gateway_memory_headers(
                    memory_actor="stellar_companion"
                ),
            ) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=503,
                        detail="Memory promotion audit unavailable",
                    )
                payload = await response.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Memory promotion audit unavailable: {type(exc).__name__}",
        ) from exc

    raw_promotions = payload.get("promotions") if isinstance(payload, dict) else None
    if not isinstance(raw_promotions, list):
        raise HTTPException(
            status_code=503,
            detail="Memory promotion audit returned an invalid payload",
        )

    allowed_fields = (
        "promotion_id",
        "source_type",
        "source_memory_id",
        "source_domain",
        "target_domain",
        "reason",
        "approved_by",
        "approval_ref",
        "created_by",
        "status",
        "created_at",
        "expires_at",
        "revoked_at",
        "revoked_by",
        "revoke_reason",
    )
    promotions = [
        {field: item.get(field) for field in allowed_fields}
        for item in raw_promotions
        if isinstance(item, dict)
        and str(item.get("source_domain") or "") == "evolution"
        and str(item.get("target_domain") or "") == "companion"
    ][:bounded_limit]
    status_counts = {"active": 0, "revoked": 0, "expired": 0}
    for item in promotions:
        status = str(item.get("status") or "").strip().lower()
        if status in status_counts:
            status_counts[status] += 1

    return {
        "direction": {
            "source_domain": "evolution",
            "target_domain": "companion",
        },
        "promotions": promotions,
        "count": len(promotions),
        "status_counts": status_counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_evolution_promotion_candidates(
    *,
    context: SupervisorUIIdentityProxyContext,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return only pending evolution-to-companion consent metadata."""

    bounded_limit = max(1, min(int(limit), 100))
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{context.gateway_url}/api/mem/promotion-candidates",
                params={
                    "limit": bounded_limit,
                    "status": "awaiting_user_consent",
                    "source_domain": "evolution",
                    "target_domain": "companion",
                },
                headers=context.gateway_memory_headers(memory_actor="governor"),
            ) as response:
                payload = await response.json()
                if response.status != 200:
                    detail = payload.get("detail") if isinstance(payload, dict) else None
                    raise HTTPException(
                        status_code=response.status,
                        detail=detail or "Memory promotion candidates unavailable",
                    )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Memory promotion candidates unavailable: {type(exc).__name__}",
        ) from exc

    raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(raw_candidates, list):
        raise HTTPException(
            status_code=503,
            detail="Memory promotion candidates returned an invalid payload",
        )
    allowed_fields = (
        "candidate_id",
        "source_type",
        "source_memory_id",
        "source_domain",
        "target_domain",
        "reason",
        "proposed_by",
        "governance_ref",
        "status",
        "requested_at",
        "expires_at",
    )
    candidates = [
        {field: item.get(field) for field in allowed_fields}
        for item in raw_candidates
        if isinstance(item, dict)
        and str(item.get("source_domain") or "") == "evolution"
        and str(item.get("target_domain") or "") == "companion"
        and str(item.get("status") or "") == "awaiting_user_consent"
    ][:bounded_limit]
    return {
        "direction": {
            "source_domain": "evolution",
            "target_domain": "companion",
        },
        "candidates": candidates,
        "count": len(candidates),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def consent_evolution_promotion_candidate(
    *,
    context: SupervisorUIIdentityProxyContext,
    candidate_id: str,
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """Record the local owner's immutable decision through the Gateway."""

    try:
        import aiohttp

        from systems.memory.promotion import MemoryPromotionConsent

        consent = MemoryPromotionConsent.model_validate(
            {
                "approved": request.get("approved"),
                "reason": request.get("reason"),
                "consented_by": "local-owner",
                "memory_actor": "governor",
            }
        )
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{context.gateway_url}/api/mem/promotion-candidates/{candidate_id}/consent",
                json=consent.model_dump(mode="json"),
                headers=context.gateway_memory_headers(memory_actor="governor"),
            ) as response:
                payload = await response.json()
                if response.status != 200:
                    detail = payload.get("detail") if isinstance(payload, dict) else None
                    raise HTTPException(
                        status_code=response.status,
                        detail=detail or "Memory promotion consent failed",
                    )
                return payload
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Memory promotion consent unavailable: {type(exc).__name__}",
        ) from exc


async def verify_identity_experience(
    *,
    context: SupervisorUIIdentityProxyContext,
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """Proxy an explicit identity-experience decision to canonical Mem."""

    try:
        import aiohttp

        from systems.memory.memory_service import IdentityExperienceVerification

        payload = IdentityExperienceVerification.model_validate(
            {
                **request,
                "owner_id": DEFAULT_OWNER_ID,
                "workspace_id": CLI_WORKSPACE_ID,
                "memory_actor": _IDENTITY_MEMORY_ACTOR,
                "memory_domain": _IDENTITY_MEMORY_DOMAIN,
            }
        ).model_dump(mode="json")
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{context.gateway_url}/api/mem/identity/experiences/verify",
                json=payload,
                headers=context.gateway_memory_headers(
                    memory_actor=_IDENTITY_MEMORY_ACTOR
                ),
            ) as response:
                response_payload = await response.json()
                if response.status != 200:
                    detail = (
                        response_payload.get("detail")
                        if isinstance(response_payload, dict)
                        else None
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=detail or "Identity experience verification failed",
                    )
                return response_payload
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Identity experience verification unavailable: {type(exc).__name__}",
        ) from exc
