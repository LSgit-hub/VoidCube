from __future__ import annotations

import json

import pytest

from voidcube.infrastructure.memory.client import (
    MemoryClient,
    MemoryClientIdentity,
    MemoryProtocolError,
)


def _client(**kwargs) -> MemoryClient:
    return MemoryClient(
        "http://127.0.0.1:6001",
        identity=MemoryClientIdentity(
            actor="api_a",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
        ),
        **kwargs,
    )


def test_memory_client_sends_direct_service_request_with_fixed_identity(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(request, timeout):
        captured.update(
            {
                "url": request.full_url,
                "method": request.method,
                "headers": dict(request.header_items()),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return Response()

    monkeypatch.setattr(
        "voidcube.infrastructure.memory.client.urlopen",
        fake_urlopen,
    )

    result = _client(service_token="local-token").request_json(
        "POST",
        "/remember",
        {"title": "Decision", "summary": "Use owner service."},
        identity_session_id="session-1",
        idempotency_key="write-1",
        request_id="request-1",
    )

    assert result == {"status": "ok"}
    assert captured["url"] == "http://127.0.0.1:6001/remember"
    assert captured["method"] == "POST"
    assert captured["body"]["memory_actor"] == "api_a"
    assert captured["body"]["owner_id"] == "local-user"
    assert captured["body"]["workspace_id"] == "default"
    assert captured["body"]["memory_domain"] == "agent_interaction"
    assert captured["headers"]["Authorization"] == "Bearer local-token"
    assert captured["headers"]["X-voidcube-protocol-version"] == "1"
    assert captured["headers"]["X-voidcube-request-id"] == "request-1"
    assert captured["headers"]["Idempotency-key"] == "write-1"
    assert captured["timeout"] == 2.0


def test_memory_client_rejects_identity_override_before_network(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.memory.client.urlopen",
        lambda *_args, **_kwargs: pytest.fail("request must not be sent"),
    )

    with pytest.raises(MemoryProtocolError, match="memory_actor"):
        _client().request_json(
            "POST",
            "/remember",
            {"memory_actor": "stellar_auto"},
        )


def test_memory_client_retries_transient_http_error(monkeypatch):
    attempts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(_request, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            from urllib.error import HTTPError

            raise HTTPError("http://memory", 503, "busy", {}, None)
        return Response()

    monkeypatch.setattr(
        "voidcube.infrastructure.memory.client.urlopen",
        fake_urlopen,
    )

    assert _client(max_retries=1, retry_base_seconds=0).request_json(
        "POST", "/health"
    ) == {"status": "ok"}
    assert attempts == [2.0, 2.0]


def test_memory_client_does_not_accept_non_http_endpoint():
    with pytest.raises(ValueError, match="http or https"):
        MemoryClient(
            "memory://local",
            identity=MemoryClientIdentity(
                actor="api_a",
                owner_id="local-user",
                workspace_id="default",
                memory_domain="agent_interaction",
            ),
        )
