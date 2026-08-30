from types import SimpleNamespace
import sys

import pytest

from voidcube.systems.supervisor.ui_memory_status_adapters import (
    SupervisorUIMemoryStatusContext,
    fetch_tier1_stats,
)


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_memory_status_owner_projects_gateway_and_rules_health(monkeypatch):
    requests = []

    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, timeout=None):
            requests.append((url, timeout))
            if url.endswith("/admin/services"):
                return _Response(
                    {
                        "services": [
                            {"service_type": "memory", "address": "http://memory"}
                        ]
                    }
                )
            if url.endswith("/tier1/stats"):
                return _Response({"turn_count": 3})
            if url.endswith("/health"):
                return _Response(
                    {
                        "maintenance": {
                            "tier2_bridge": {
                                "eligible_candidate_count": 7,
                            }
                        },
                        "transport_outboxes": {
                            "status": "healthy",
                            "outboxes": {
                                "api_a": {"status": "healthy", "pending_count": 0}
                            },
                        }
                    }
                )
            return _Response(
                {
                    "rules": {"tier1_decay": {"run_count": 1}},
                    "llm_healthy": True,
                    "llm_model": "active-model",
                    "effective_activity_at": "2999-01-01T00:00:00+00:00",
                    "maintenance_run": {
                        "run_id": "maintenance-1",
                        "status": "running",
                    },
                }
            )

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    stats = await fetch_tier1_stats(
        context=SupervisorUIMemoryStatusContext(gateway_url="http://gateway")
    )

    assert [url for url, _ in requests] == [
        "http://gateway/admin/services",
        "http://memory/tier1/stats",
        "http://memory/compressed/rules-status",
        "http://memory/health",
    ]
    assert stats["turn_count"] == 3
    assert stats["rules"] == {"tier1_decay": {"run_count": 1}}
    assert stats["llm_healthy"] is True
    assert stats["memory_active"] is True
    assert stats["pending_compression_count"] == 7
    assert stats["maintenance_run"] == {
        "run_id": "maintenance-1",
        "status": "running",
    }
    assert stats["transport_outboxes"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_memory_status_owner_reports_registry_and_transport_failures(monkeypatch):
    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, timeout=None):
            return _Response({"services": {}})

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    unavailable = await fetch_tier1_stats(
        context=SupervisorUIMemoryStatusContext(gateway_url="http://gateway")
    )

    assert unavailable == {
        "memory_unavailable": True,
        "memory_unavailable_reason": "memory_service_not_registered",
        "memory_active": False,
    }
