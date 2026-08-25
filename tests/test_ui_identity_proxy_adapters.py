import pytest
from fastapi import HTTPException

from voidcube.systems.supervisor.ui_identity_proxy_adapters import (
    SupervisorUIIdentityProxyContext,
    consent_evolution_promotion_candidate,
    get_evolution_promotion_audit,
    get_evolution_promotion_candidates,
    get_identity_archive,
    get_identity_turns,
)


class _MemoryClient:
    def __init__(self, calls, payload):
        self._calls = calls
        self._payload = payload

    async def request_json(self, method, path, payload=None, **kwargs):
        self._calls.append((method, path, payload, kwargs))
        return self._payload


def _context(payload):
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return _MemoryClient(calls, payload)

    return SupervisorUIIdentityProxyContext(memory_client_factory=factory), calls


@pytest.mark.asyncio
async def test_identity_archive_uses_memory_client_and_fixed_scope():
    context, calls = _context({"layers": {}})

    result = await get_identity_archive(context=context)

    assert result == {"layers": {}}
    assert calls == [
        {
            "memory_actor": "stellar_companion",
            "memory_domain": "agent_interaction",
            "owner_id": "local-user",
            "workspace_id": "VoidCube",
            "timeout_seconds": 5,
        },
        ("GET", "/identity/archive", None, {}),
    ]


@pytest.mark.asyncio
async def test_identity_proxy_uses_memory_client_and_bounds_turn_scope():
    context, calls = _context({"turns": [{"turn_id": "turn-1"}]})

    result = await get_identity_turns(context=context, limit=999)

    assert calls == [
        {
            "memory_actor": "stellar_companion",
            "memory_domain": "agent_interaction",
            "owner_id": "local-user",
            "workspace_id": "VoidCube",
            "timeout_seconds": 5,
        },
        ("GET", "/turns", {"limit": 50, "newest_first": "true"}, {}),
    ]
    assert result == {"turns": [{"turn_id": "turn-1"}], "count": 1}


@pytest.mark.asyncio
async def test_promotion_proxy_owner_filters_direction_and_public_fields():
    rows = [
        {
            "promotion_id": "allowed",
            "source_domain": "evolution",
            "target_domain": "companion",
            "status": "active",
            "reason": "approved",
            "owner_id": "must-not-leak",
        },
        {
            "promotion_id": "wrong-direction",
            "source_domain": "agent_interaction",
            "target_domain": "companion",
            "status": "active",
        },
    ]
    context, calls = _context({"promotions": rows})

    result = await get_evolution_promotion_audit(context=context, limit=1)

    assert calls[0]["memory_actor"] == "stellar_companion"
    assert calls[1] == (
        "GET",
        "/promotions",
        {"limit": 500, "target_domain": "companion"},
        {},
    )
    assert [item["promotion_id"] for item in result["promotions"]] == ["allowed"]
    assert "owner_id" not in result["promotions"][0]
    assert result["status_counts"] == {"active": 1, "revoked": 0, "expired": 0}


@pytest.mark.asyncio
async def test_promotion_candidate_owner_forces_pending_direction_and_limit():
    context, calls = _context(
        {
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "source_domain": "evolution",
                    "target_domain": "companion",
                    "status": "awaiting_user_consent",
                    "owner_id": "must-not-leak",
                },
                {
                    "candidate_id": "candidate-2",
                    "source_domain": "evolution",
                    "target_domain": "companion",
                    "status": "awaiting_user_consent",
                },
            ]
        }
    )

    result = await get_evolution_promotion_candidates(context=context, limit=999)

    assert calls[0]["memory_actor"] == "governor"
    assert calls[1] == (
        "GET",
        "/promotion-candidates",
        {
            "limit": 100,
            "status": "awaiting_user_consent",
            "source_domain": "evolution",
            "target_domain": "companion",
        },
        {},
    )
    assert [item["candidate_id"] for item in result["candidates"]] == [
        "candidate-1",
        "candidate-2",
    ]
    assert "owner_id" not in result["candidates"][0]


@pytest.mark.asyncio
async def test_promotion_consent_owner_maps_validation_to_422():
    context, _calls = _context({})

    with pytest.raises(HTTPException) as raised:
        await consent_evolution_promotion_candidate(
            context=context,
            candidate_id="candidate-1",
            request={"approved": True, "reason": ""},
        )

    assert raised.value.status_code == 422
