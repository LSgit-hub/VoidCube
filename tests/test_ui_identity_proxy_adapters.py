from types import SimpleNamespace
import sys

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


def _context():
    return SupervisorUIIdentityProxyContext(
        gateway_url="http://gateway",
        gateway_memory_headers=lambda **kwargs: {
            "X-VoidCube-Memory-Actor": kwargs["memory_actor"]
        },
    )


@pytest.mark.asyncio
async def test_identity_archive_uses_gateway_and_fixed_scope(monkeypatch):
    captured = {}

    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, params=None, headers=None):
            captured.update(url=url, params=params, headers=headers)
            return _Response({"layers": {}})

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    result = await get_identity_archive(context=_context())

    assert result == {"layers": {}}
    assert captured == {
        "url": "http://gateway/api/mem/identity/archive",
        "params": {"owner_id": "local-user", "workspace_id": "VoidCube"},
        "headers": {"X-VoidCube-Memory-Actor": "stellar_companion"},
    }


class _Response:
    def __init__(self, payload, status=200):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_identity_proxy_uses_gateway_and_bounds_turn_scope(monkeypatch):
    requests = []

    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, params=None, headers=None):
            requests.append((url, params, headers))
            return _Response({"turns": [{"turn_id": "turn-1"}]})

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    result = await get_identity_turns(context=_context(), limit=999)

    assert requests == [
        (
            "http://gateway/api/mem/turns",
            {
                "limit": 50,
                "newest_first": "true",
                "owner_id": "local-user",
                "workspace_id": "VoidCube",
                "memory_domain": "agent_interaction",
            },
            {"X-VoidCube-Memory-Actor": "stellar_companion"},
        ),
    ]
    assert result == {"turns": [{"turn_id": "turn-1"}], "count": 1}


@pytest.mark.asyncio
async def test_promotion_proxy_owner_filters_direction_and_public_fields(monkeypatch):
    captured = {}
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

    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, params=None, headers=None):
            captured.update(url=url, params=params, headers=headers)
            return _Response({"promotions": rows})

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    result = await get_evolution_promotion_audit(context=_context(), limit=1)

    assert captured["params"] == {"limit": 500, "target_domain": "companion"}
    assert captured["headers"] == {"X-VoidCube-Memory-Actor": "stellar_companion"}
    assert [item["promotion_id"] for item in result["promotions"]] == ["allowed"]
    assert "owner_id" not in result["promotions"][0]
    assert result["status_counts"] == {"active": 1, "revoked": 0, "expired": 0}


@pytest.mark.asyncio
async def test_promotion_candidate_owner_forces_pending_direction_and_limit(monkeypatch):
    captured = {}

    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, params=None, headers=None):
            captured.update(url=url, params=params, headers=headers)
            return _Response(
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

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    result = await get_evolution_promotion_candidates(context=_context(), limit=999)

    assert captured["params"] == {
        "limit": 100,
        "status": "awaiting_user_consent",
        "source_domain": "evolution",
        "target_domain": "companion",
    }
    assert captured["headers"] == {"X-VoidCube-Memory-Actor": "governor"}
    assert [item["candidate_id"] for item in result["candidates"]] == [
        "candidate-1",
        "candidate-2",
    ]
    assert "owner_id" not in result["candidates"][0]


@pytest.mark.asyncio
async def test_promotion_consent_owner_maps_validation_to_422(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs),
    )

    with pytest.raises(HTTPException) as raised:
        await consent_evolution_promotion_candidate(
            context=_context(),
            candidate_id="candidate-1",
            request={"approved": True, "reason": ""},
        )

    assert raised.value.status_code == 422
