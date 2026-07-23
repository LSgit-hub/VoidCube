from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from plugins.memory.mem import MemMemoryProvider


@pytest.mark.unit
def test_mem_provider_exposes_only_canonical_service_tools():
    provider = MemMemoryProvider()

    assert [schema["name"] for schema in provider.get_tool_schemas()] == [
        "mem_search",
        "mem_timeline",
    ]


@pytest.mark.unit
def test_mem_provider_search_and_prefetch_use_gateway_memory_route(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._gateway_url = "http://gateway.test"
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {
            "results": [
                {"title": "Current architecture", "summary": "Memory is canonical."}
            ],
            "count": 1,
            "context": "Relevant recalled memory:\n- [tier2:event] Current architecture: Memory is canonical.",
        }

    monkeypatch.setattr(provider, "_request_json", fake_request)

    tool_result = json.loads(
        provider.handle_tool_call("mem_search", {"query": "architecture", "limit": 3})
    )
    context = provider.prefetch("architecture", session_id="session-1")

    assert tool_result["success"] is True
    assert tool_result["data"]["count"] == 1
    assert context == (
        "Relevant recalled memory:\n"
        "- [tier2:event] Current architecture: Memory is canonical."
    )
    assert calls == [
        ("POST", "/recall", {"query": "architecture", "limit": 3}),
        (
            "POST",
            "/recall",
            {
                "query": "architecture",
                "limit": 5,
                "max_context_chars": 3500,
                "current_session_id": "session-1",
            },
        ),
    ]


@pytest.mark.unit
def test_mem_provider_writes_explicit_session_and_deduplicated_turn_pair(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    calls = []
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda method, path, payload=None: calls.append((method, path, payload)) or {},
    )

    provider._write_turn_pair(
        {
            "session_id": "session with space",
            "user_content": "question",
            "assistant_content": "answer",
            "write_id": "write-1",
        }
    )

    assert calls[0] == (
        "POST",
        "/sessions",
        {
            "session_id": "session with space",
            "metadata": {"source": "agent_memory_provider"},
        },
    )
    assert calls[1][1] == "/sessions/session%20with%20space/turns"
    assert calls[1][2]["metadata"]["turn_dedup_key"] == "write-1:user"
    assert calls[2][2]["speaker"] == "agent"
    assert calls[2][2]["metadata"]["turn_dedup_key"] == "write-1:agent"


@pytest.mark.unit
def test_mem_provider_reports_service_unavailable_without_local_fallback(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("down")),
    )

    result = json.loads(provider.handle_tool_call("mem_search", {"query": "x"}))

    assert result == {
        "success": False,
        "error": "memory_service_unavailable",
        "detail": "ConnectionError",
    }
    assert provider.prefetch("x") == ""
    assert not hasattr(provider, "_db")
    assert not hasattr(provider, "_memory_state")
