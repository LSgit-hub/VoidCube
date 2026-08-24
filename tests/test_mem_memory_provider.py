from __future__ import annotations

import json
import sqlite3
import threading
import time
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
    assert "supersedes_memory_ids" in provider.system_prompt_block()


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
                "memory_domain": "agent_interaction",
            },
        )
    ]


@pytest.mark.unit
def test_mem_provider_forwards_explicit_supersession(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    calls = []
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda method, path, payload=None: calls.append((method, path, payload)) or {},
    )

    provider.handle_tool_call(
        "mem_remember",
        {
            "title": "Current diagnosis",
            "summary": "The repair is verified.",
            "evidence_refs": ["turn:new"],
            "supersedes_memory_ids": ["durable-old"],
        },
    )

    assert calls[0][2]["supersedes_memory_ids"] == ["durable-old"]


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
            "context": "Relevant recalled memory:\n- Current architecture: Memory is canonical.",
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
        "- Current architecture: Memory is canonical."
    )
    assert "trace_id=" not in context
    assert "recall status" not in context.lower()
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
def test_mem_provider_timeline_only_filters_an_explicit_session(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._session_id = "current-session"
    calls = []
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda method, path, payload=None: calls.append((method, path, payload)) or {},
    )

    provider.handle_tool_call("mem_timeline", {"date": "2026-08-05"})
    provider.handle_tool_call(
        "mem_timeline",
        {"date": "2026-08-05", "session_id": "chosen-session"},
    )

    assert "session_id" not in calls[0][2]
    assert calls[1][2]["session_id"] == "chosen-session"


@pytest.mark.unit
def test_mem_provider_uses_gateway_issued_session_credential(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._session_id = "session-1"
    provider._gateway_url = "http://gateway.test"
    requests = []

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._payload

    def fake_urlopen(request, timeout=None):
        requests.append((request, timeout))
        if request.full_url.endswith("/v1/sessions/register"):
            return _FakeResponse({"session_token": "session-secret"})
        return _FakeResponse({"status": "remembered"})

    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "root-secret")
    monkeypatch.setattr(provider, "_gateway_is_reachable", lambda: True)
    monkeypatch.setattr("plugins.memory.mem.urlopen", fake_urlopen)

    result = provider._request_json(
        "POST",
        "/remember",
        {"title": "Decision", "summary": "Keep rollback evidence."},
    )

    assert result == {"status": "remembered"}
    registration_request = requests[0][0]
    memory_request = requests[1][0]
    assert registration_request.get_header("Authorization") == "Bearer root-secret"
    assert json.loads(registration_request.data) == {
        "session_id": "session-1",
        "source": "agent_memory_provider",
        "owner_id": "local-user",
        "workspace_id": "default",
    }
    assert memory_request.get_header("X-voidcube-session-id") == "session-1"
    assert memory_request.get_header("X-voidcube-session-token") == "session-secret"


@pytest.mark.unit
def test_mem_provider_caches_failed_gateway_probe(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._gateway_url = "http://unreachable.test:6000"
    attempts = []

    def fail_connection(address, timeout):
        attempts.append((address, timeout))
        raise TimeoutError("unreachable")

    monkeypatch.setattr(
        "plugins.memory.mem.socket.create_connection",
        fail_connection,
    )
    monkeypatch.setattr(
        "plugins.memory.mem.urlopen",
        lambda *_args, **_kwargs: pytest.fail(
            "HTTP request ran after a failed gateway probe"
        ),
    )

    for _ in range(2):
        with pytest.raises(ConnectionError, match="unreachable"):
            provider._request_json("POST", "/recall", {"query": "x"})

    assert attempts == [(('unreachable.test', 6000), 0.25)]


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
        "No recalled evidence is available for this turn. Do not assume that prior "
        "decisions, preferences, or events were recalled. This status only describes "
        "evidence retrieval for the current turn. Do not infer or claim that "
        "no prior memory was ever saved."
    )
    assert not hasattr(provider, "_db")
    assert not hasattr(provider, "_memory_state")


@pytest.mark.unit
def test_mem_provider_makes_recall_miss_explicit(monkeypatch):
    provider = MemMemoryProvider()
    provider._initialized = True
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args, **kwargs: {
            "trace_id": "trace-miss",
            "recall_status": "miss",
            "context": "",
        },
    )

    assert provider.prefetch("unmatched") == (
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
                "- Mem: identity"
            ),
        },
    )

    context = provider.prefetch("你是谁你记得吗")

    assert "continuing identity is 星子" in context
    assert "model, provider, and Agent runtime are replaceable carriers" in context
    assert "not the persistent identity" in context
    assert "identity-founding-purpose" not in context
    assert "trace_id=" not in context


@pytest.mark.unit
def test_mem_provider_prompt_keeps_recall_metadata_internal() -> None:
    prompt = MemMemoryProvider().system_prompt_block()

    assert "never expose trace IDs" in prompt
    assert "never expose trace IDs, memory IDs" in prompt
    assert "Use those fields internally" in prompt


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
            "recall_status": "miss",
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
    provider._redact_before_store = True
    provider._outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")
    monkeypatch.setattr(
        "plugins.memory.mem.redact_sensitive_text",
        lambda value: value.replace("secret-value", "<redacted>"),
    )

    outcome = provider.sync_turn(
        "api_key=secret-value",
        "stored secret-value",
        tags=["evaluation", "evaluation", "suite=memory"],
    )
    pending = provider._outbox.next_due()

    assert pending is not None
    assert pending["user_content"] == "api_key=<redacted>"
    assert pending["assistant_content"] == "stored <redacted>"
    assert pending["tags"] == ["evaluation", "evaluation", "suite=memory"]
    assert pending["owner_id"] == "local-user"
    assert pending["workspace_id"] == "default"
    assert outcome.status == "queued"
    assert outcome.details["write_id"] == pending["write_id"]
    assert outcome.details["durable_outbox"] is True


@pytest.mark.unit
def test_mem_provider_preserves_raw_outbox_content_by_default(tmp_path):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._auto_sync = True
    provider._session_id = "session-raw"
    provider._outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")

    provider.sync_turn("api_key=secret-value", "stored secret-value")
    pending = provider._outbox.next_due()

    assert pending is not None
    assert pending["user_content"] == "api_key=secret-value"
    assert pending["assistant_content"] == "stored secret-value"


@pytest.mark.unit
def test_mem_provider_reports_when_completed_turn_cannot_be_queued(tmp_path):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._auto_sync = True
    provider._session_id = "session-1"

    missing_outbox = provider.sync_turn("question", "answer")
    assert missing_outbox.status == "failed"
    assert missing_outbox.error == "Memory outbox is unavailable"

    class _FailingOutbox:
        def enqueue(self, _payload):
            raise OSError("disk full")

    provider._outbox = _FailingOutbox()
    enqueue_failure = provider.sync_turn("question", "answer")
    assert enqueue_failure.status == "failed"
    assert "disk full" in (enqueue_failure.error or "")


@pytest.mark.unit
def test_mem_provider_reports_intentional_sync_skip_reasons():
    provider = MemMemoryProvider()

    not_initialized = provider.sync_turn("question", "answer")
    assert not_initialized.status == "skipped"
    assert not_initialized.details["reason"] == "not_initialized"

    provider._initialized = True
    provider._auto_sync = False
    disabled = provider.sync_turn("question", "answer")
    assert disabled.status == "skipped"
    assert disabled.details["reason"] == "auto_sync_disabled"


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


@pytest.mark.unit
def test_memory_outbox_upgrades_legacy_rows_without_losing_pending_writes(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    payload = {
        "write_id": "legacy-write",
        "session_id": "legacy-session",
        "user_content": "question",
        "assistant_content": "answer",
    }
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE pending_writes ("
            "write_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, "
            "next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT, "
            "created_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO pending_writes "
            "(write_id, payload, attempts, next_attempt_at, created_at) "
            "VALUES (?, ?, 0, 0, ?)",
            ("legacy-write", json.dumps(payload), time.time()),
        )

    outbox = MemoryWriteOutbox(path)

    assert outbox.pending_count() == 1
    assert outbox.next_due()["write_id"] == "legacy-write"
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_writes)")}
    assert "first_failed_at" in columns


@pytest.mark.unit
def test_memory_outbox_lease_prevents_duplicate_cross_process_delivery(
    monkeypatch,
    tmp_path,
):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "plugins.memory.mem.outbox.time.time",
        lambda: clock["now"],
    )
    path = tmp_path / "outbox.sqlite3"
    first = MemoryWriteOutbox(path, lease_seconds=5)
    second = MemoryWriteOutbox(path, lease_seconds=5)
    first.enqueue(
        {
            "write_id": "leased-write",
            "session_id": "session-1",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )

    assert first.next_due()["write_id"] == "leased-write"
    assert second.next_due() is None
    assert first.health_snapshot()["inflight_count"] == 1

    clock["now"] += 6.0
    assert second.next_due()["write_id"] == "leased-write"
    first.mark_delivered("leased-write")
    assert second.pending_count() == 1
    second.mark_delivered("leased-write")
    assert second.pending_count() == 0


@pytest.mark.unit
def test_memory_outbox_failure_is_retained_until_exponential_retry_is_due(
    monkeypatch,
    tmp_path,
):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "plugins.memory.mem.outbox.time.time",
        lambda: clock["now"],
    )
    outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(
        {
            "write_id": "write-1",
            "session_id": "session-1",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )

    outbox.mark_failed("write-1", attempts=1, error="service unavailable")

    assert outbox.pending_count() == 1
    assert outbox.next_due() is None

    clock["now"] += 2.0
    retry = outbox.next_due()

    assert retry is not None
    assert retry["write_id"] == "write-1"
    assert retry["_outbox_attempts"] == 1


@pytest.mark.unit
def test_memory_outbox_dead_letters_permanent_failures_and_reports_health(
    monkeypatch, tmp_path
):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "plugins.memory.mem.outbox.time.time",
        lambda: clock["now"],
    )
    outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3", max_attempts=2)
    outbox.enqueue(
        {
            "write_id": "write-dead",
            "session_id": "session-1",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )
    outbox.mark_failed("write-dead", attempts=1, error="schema rejected")
    outbox.mark_failed("write-dead", attempts=2, error="schema rejected")

    assert outbox.next_due() is None
    health = outbox.health_snapshot()
    assert health["pending_count"] == 0
    assert health["dead_letter_count"] == 1
    assert health["oldest_failure_at"] == "1970-01-01T00:16:40+00:00"
    assert health["last_success_at"] is None
    assert outbox.requeue_dead_letter("write-dead") is True
    requeued = outbox.next_due()
    assert requeued["write_id"] == "write-dead"
    assert requeued["_outbox_attempts"] == 0
    assert outbox.health_snapshot()["oldest_failure_at"] is None


@pytest.mark.unit
def test_memory_outbox_dead_letter_does_not_block_later_ordered_write(
    monkeypatch, tmp_path
):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "plugins.memory.mem.outbox.time.time",
        lambda: clock["now"],
    )
    outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3", max_attempts=1)
    outbox.enqueue({"write_id": "write-dead", "operation": "turn"})
    outbox.enqueue({"write_id": "write-close", "operation": "close_session"})

    claimed = outbox.next_due()
    assert claimed["write_id"] == "write-dead"
    outbox.mark_failed("write-dead", attempts=1, error="gateway unavailable")

    assert outbox.has_blocking_writes_before("write-close") is False
    next_item = outbox.next_due()
    assert next_item["write_id"] == "write-close"


@pytest.mark.unit
def test_memory_outbox_active_older_write_still_blocks_later_ordered_write(tmp_path):
    outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue({"write_id": "write-old", "operation": "turn"})
    outbox.enqueue({"write_id": "write-close", "operation": "close_session"})

    assert outbox.has_blocking_writes_before("write-close") is True
    claimed = outbox.next_due()
    assert claimed["write_id"] == "write-old"
    assert outbox.has_blocking_writes_before("write-close") is True


@pytest.mark.unit
def test_mem_provider_reports_outbox_health_through_authenticated_memory_path(
    tmp_path,
):
    provider = MemMemoryProvider()
    provider._session_id = "session-health"
    provider._outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")
    provider._outbox.enqueue(
        {
            "write_id": "write-health",
            "session_id": "session-health",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )
    captured = {}

    def capture(method, path, payload=None):
        captured.update({"method": method, "path": path, "payload": payload})
        return {"status": "recorded"}

    provider._request_json = capture

    provider._report_outbox_health_if_due(force=True)

    assert captured["method"] == "POST"
    assert captured["path"] == "/outbox/health"
    assert captured["payload"]["session_id"] == "session-health"
    assert captured["payload"]["outbox_id"] == provider._outbox.outbox_id
    assert captured["payload"]["pending_count"] == 1


@pytest.mark.unit
def test_mem_provider_shutdown_drains_immediately_deliverable_writes(tmp_path):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._session_id = "session-shutdown"
    provider._request_timeout_seconds = 0.1
    provider._outbox_shutdown_drain_timeout_seconds = 1.0
    provider._outbox = MemoryWriteOutbox(tmp_path / "outbox.sqlite3")
    for index in range(2):
        provider._outbox.enqueue(
            {
                "write_id": f"shutdown-{index}",
                "session_id": "session-shutdown",
                "user_content": "question",
                "assistant_content": "answer",
            }
        )

    delivered = []
    provider._write_turn_pair = lambda item: delivered.append(item["write_id"])
    provider._report_outbox_health_if_due = lambda **_kwargs: None
    provider._sync_stop.clear()
    provider._sync_thread = threading.Thread(target=provider._background_sync)
    provider._sync_thread.start()

    provider.shutdown()

    assert sorted(delivered) == ["shutdown-0", "shutdown-1"]
    assert provider._outbox.pending_count() == 0
    assert provider._sync_thread is None


@pytest.mark.unit
def test_mem_provider_shutdown_preserves_delayed_retry_without_forcing_it(tmp_path):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._session_id = "session-shutdown-retry"
    provider._request_timeout_seconds = 0.1
    provider._outbox_shutdown_drain_timeout_seconds = 1.0
    provider._outbox = MemoryWriteOutbox(
        tmp_path / "outbox.sqlite3",
        retry_base_seconds=60.0,
        retry_max_seconds=60.0,
    )
    provider._outbox.enqueue(
        {
            "write_id": "shutdown-retry",
            "session_id": "session-shutdown-retry",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )
    claimed = provider._outbox.next_due()
    provider._outbox.mark_failed(
        "shutdown-retry",
        attempts=1,
        error="service unavailable",
    )
    assert claimed is not None
    assert provider._outbox.drainable_count() == 0

    provider._report_outbox_health_if_due = lambda **_kwargs: None
    provider._sync_stop.clear()
    provider._sync_thread = threading.Thread(target=provider._background_sync)
    provider._sync_thread.start()
    started = time.monotonic()

    provider.shutdown()

    assert time.monotonic() - started < 0.5
    assert provider._outbox.pending_count() == 1
    assert provider._outbox.next_due() is None


@pytest.mark.unit
def test_mem_provider_shutdown_drain_is_bounded_when_delivery_blocks(tmp_path):
    provider = MemMemoryProvider()
    provider._initialized = True
    provider._session_id = "session-shutdown-timeout"
    provider._outbox_shutdown_drain_timeout_seconds = 0.05
    provider._outbox = MemoryWriteOutbox(
        tmp_path / "outbox.sqlite3",
        lease_seconds=1.0,
    )
    provider._outbox.enqueue(
        {
            "write_id": "shutdown-timeout",
            "session_id": "session-shutdown-timeout",
            "user_content": "question",
            "assistant_content": "answer",
        }
    )

    delivery_started = threading.Event()
    release_delivery = threading.Event()

    def blocked_delivery(_item):
        delivery_started.set()
        release_delivery.wait(timeout=1.0)

    provider._write_turn_pair = blocked_delivery
    provider._report_outbox_health_if_due = lambda **_kwargs: None
    provider._sync_stop.clear()
    provider._sync_thread = threading.Thread(target=provider._background_sync)
    provider._sync_thread.start()
    assert delivery_started.wait(timeout=1.0)
    worker = provider._sync_thread
    started = time.monotonic()

    provider.shutdown()

    assert time.monotonic() - started < 0.5
    assert provider._sync_thread is worker
    assert worker.is_alive()

    release_delivery.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert provider._outbox.pending_count() == 0
