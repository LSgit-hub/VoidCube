from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from plugins.memory.mem import MemMemoryProvider
from plugins.memory.mem.outbox import MemoryWriteOutbox


@pytest.mark.unit
def test_mem_provider_exposes_only_canonical_service_tools():
    provider = MemMemoryProvider()

    assert [schema["name"] for schema in provider.get_tool_schemas()] == [
        "mem_search",
        "mem_timeline",
        "mem_remember",
        "mem_feedback",
        "mem_forget",
    ]


@pytest.mark.unit
def test_mem_provider_remember_uses_canonical_service(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._session_id = "session-1"
    calls = []
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda method, path, payload=None: calls.append((method, path, payload))
        or {"status": "remembered"},
    )

    result = json.loads(
        provider.handle_tool_call(
            "mem_remember",
            {
                "title": "Deployment decision",
                "summary": "Always create a rollback backup.",
                "evidence_refs": ["turn:turn-1"],
                "event_kind": "decision",
            },
        )
    )

    assert result["success"] is True
    assert calls == [
        (
            "POST",
            "/remember",
            {
                "title": "Deployment decision",
                "summary": "Always create a rollback backup.",
                "evidence_refs": ["turn:turn-1", "session:session-1"],
                "event_kind": "decision",
                "source_actor": "agent",
                "owner_id": "local-user",
                "workspace_id": "default",
                "memory_actor": "api_a",
                "memory_domain": "agent_interaction",
            },
        )
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
            "trace_id": "trace-1",
            "recall_status": "hit",
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
        "Memory recall status: hit (trace_id=trace-1).\n"
        "Relevant recalled memory:\n"
        "- [tier2:event] Current architecture: Memory is canonical."
    )
    assert calls == [
        (
            "POST",
            "/recall",
            {
                "query": "architecture",
                "limit": 3,
                "current_session_id": "",
                "request_source": "tool",
                "owner_id": "local-user",
                "workspace_id": "default",
                "memory_actor": "api_a",
                "memory_domain": "agent_interaction",
            },
        ),
        (
            "POST",
            "/recall",
            {
                "query": "architecture",
                "limit": 5,
                "max_context_chars": 3500,
                "current_session_id": "session-1",
                "request_source": "auto_prefetch",
                "owner_id": "local-user",
                "workspace_id": "default",
                "memory_actor": "api_a",
                "memory_domain": "agent_interaction",
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

    assert calls == [
        (
            "POST",
            "/turn-pairs",
            {
                "session_id": "session with space",
                "user_content": "question",
                "assistant_content": "answer",
                "write_id": "write-1",
            },
        )
    ]


@pytest.mark.unit
def test_mem_provider_delegates_experience_settlement_to_atomic_service_endpoint(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"status": "stored"}

    monkeypatch.setattr(provider, "_request_json", fake_request)

    provider._write_turn_pair(
        {
            "session_id": "session-1",
            "user_content": "请永远记录这个故事。",
            "assistant_content": "已记录。",
            "write_id": "write-1",
        }
    )

    assert calls == [(
        "POST",
        "/turn-pairs",
        {
            "session_id": "session-1",
            "user_content": "请永远记录这个故事。",
            "assistant_content": "已记录。",
            "write_id": "write-1",
        },
    )]


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
    assert provider.prefetch("x") == (
        "Memory recall status: unavailable for this turn "
        "(error=ConnectionError). Do not assume that prior decisions, "
        "preferences, or events were recalled. This status only describes "
        "evidence retrieval for the current turn. Do not infer or claim that "
        "no prior memory was ever saved."
    )
    assert not hasattr(provider, "_db")
    assert not hasattr(provider, "_memory_state")


@pytest.mark.unit
def test_mem_provider_makes_empty_recall_explicit(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args, **kwargs: {
            "trace_id": "trace-empty",
            "recall_status": "empty",
            "context": "",
        },
    )

    assert provider.prefetch("unmatched") == (
        "Memory recall status: empty (trace_id=trace-empty). "
        "No recalled evidence matched this turn.\n"
        "This status only describes evidence retrieval for the current turn. "
        "Do not infer or claim that no prior memory was ever saved."
    )


@pytest.mark.unit
def test_mem_provider_marks_identity_evidence_as_persistent_not_vendor_identity(
    monkeypatch,
):
    provider = MemMemoryProvider()
    provider._initialized = True
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args, **kwargs: {
            "trace_id": "trace-identity",
            "recall_status": "hit",
            "query_plan": {"intent": "identity"},
            "context": (
                "Relevant recalled memory:\n"
                "- [tier2:event id=identity-founding-purpose] Mem: identity"
            ),
        },
    )

    context = provider.prefetch("你是谁你记得吗")

    assert "continuing identity is 星子" in context
    assert "model, provider, and Agent runtime are replaceable carriers" in context
    assert "not the persistent identity" in context
    assert "identity-founding-purpose" in context


@pytest.mark.unit
def test_mem_provider_identity_empty_is_retrieval_uncertainty_not_memory_absence(
    monkeypatch,
):
    provider = MemMemoryProvider()
    provider._initialized = True
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args, **kwargs: {
            "trace_id": "trace-identity-empty",
            "recall_status": "empty",
            "query_plan": {"intent": "identity"},
            "context": "",
        },
    )

    context = provider.prefetch("who are you")

    assert "No recalled evidence matched this turn" in context
    assert "Do not infer or claim that no prior memory was ever saved" in context
    assert "continuing identity is 星子" in context


@pytest.mark.unit
def test_mem_provider_redacts_before_durable_outbox(monkeypatch, tmp_path):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._auto_sync = True
    provider._session_id = "session-1"
    provider._outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")
    monkeypatch.setattr(
        "plugins.memory.mem.redact_sensitive_text",
        lambda value: value.replace("secret-value", "<redacted>"),
    )

    provider.sync_turn("api_key=secret-value", "stored secret-value")
    pending = provider._outbox.next_due()

    assert pending is not None
    assert pending["user_content"] == "api_key=<redacted>"
    assert pending["assistant_content"] == "stored <redacted>"
    assert pending["owner_id"] == "local-user"
    assert pending["workspace_id"] == "default"


@pytest.mark.unit
def test_memory_outbox_survives_reopen_until_delivery(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    first = MemoryWriteOutbox(path)
    first.enqueue(
        {
            "write_id": "write-1",
            "session_id": "session-1",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )

    reopened = MemoryWriteOutbox(path)
    assert reopened.pending_count() == 1
    assert reopened.next_due()["write_id"] == "write-1"

    reopened.mark_delivered("write-1")
    assert MemoryWriteOutbox(path).pending_count() == 0
