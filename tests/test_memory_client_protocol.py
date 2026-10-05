from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from voidcube.infrastructure.memory.client import (
    AsyncMemoryClient,
    MemoryClient,
    MemoryClientIdentity,
    MemoryProtocolError,
    MemoryServiceUnavailable,
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


def test_memory_client_get_compressed_quotes_opaque_id(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"memory_id":"m/1","summary":"authorized"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["data"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("voidcube.infrastructure.memory.client.urlopen", fake_urlopen)
    assert _client().get_compressed("m/1", session_id="session-7")["summary"] == "authorized"
    assert captured["url"].startswith("http://127.0.0.1:6001/compressed/m%2F1?")
    assert "memory_actor=api_a" in captured["url"]
    assert "owner_id=local-user" in captured["url"]
    assert captured["method"] == "GET"
    assert captured["data"] is None


def test_memory_client_retries_transient_http_error(monkeypatch):
    attempts = []
    sleeps = []

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
    monkeypatch.setattr("voidcube.infrastructure.memory.client.time.sleep", sleeps.append)

    assert _client(max_retries=1, retry_base_seconds=0.05).request_json(
        "GET", "/health"
    ) == {"status": "ok"}
    assert attempts == [2.0, 2.0]
    assert sleeps == [0.05]


def test_memory_client_honors_bounded_retry_after(monkeypatch):
    attempts = []
    sleeps = []

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

            raise HTTPError(
                "http://memory",
                503,
                "busy",
                {"Retry-After": "7"},
                None,
            )
        return Response()

    monkeypatch.setattr("voidcube.infrastructure.memory.client.urlopen", fake_urlopen)
    monkeypatch.setattr("voidcube.infrastructure.memory.client.time.sleep", sleeps.append)

    assert _client(max_retries=1, retry_base_seconds=0.05).request_json(
        "GET", "/health"
    ) == {"status": "ok"}
    assert sleeps == [7.0]


def test_memory_client_does_not_replay_ambiguous_post_transport_failure(monkeypatch):
    attempts = []

    def fail_urlopen(_request, timeout):
        attempts.append(timeout)
        from urllib.error import URLError

        raise URLError("connection lost")

    monkeypatch.setattr(
        "voidcube.infrastructure.memory.client.urlopen",
        fail_urlopen,
    )

    with pytest.raises(MemoryServiceUnavailable):
        _client(max_retries=2, retry_base_seconds=0).request_json(
            "POST", "/remember", {"summary": "write without receipt"}
        )
    assert attempts == [2.0]


@pytest.mark.asyncio
async def test_async_memory_client_does_not_replay_ambiguous_post_transport_failure(
    monkeypatch,
):
    attempts = []

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, *_args, **_kwargs):
            attempts.append(True)
            raise RuntimeError("connection lost")

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientSession=Session,
            ClientTimeout=lambda **_kwargs: object(),
        ),
    )
    client = AsyncMemoryClient(
        "http://127.0.0.1:6001",
        identity=MemoryClientIdentity(
            actor="api_a",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
        ),
        max_retries=2,
        retry_base_seconds=0,
    )

    with pytest.raises(MemoryServiceUnavailable):
        await client.request_json(
            "POST", "/remember", {"summary": "write without receipt"}
        )
    assert attempts == [True]


@pytest.mark.asyncio
async def test_async_memory_client_classifies_malformed_json_as_protocol_error(monkeypatch):
    attempts = []

    class ResponseContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        status = 200

        async def json(self):
            attempts.append(True)
            raise ValueError("invalid json")

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, *_args, **_kwargs):
            return ResponseContext()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientSession=Session,
            ClientTimeout=lambda **_kwargs: object(),
        ),
    )
    client = AsyncMemoryClient(
        "http://127.0.0.1:6001",
        identity=MemoryClientIdentity(
            actor="api_a",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
        ),
        max_retries=2,
        retry_base_seconds=0,
    )

    with pytest.raises(MemoryProtocolError, match="invalid JSON"):
        await client.request_json("GET", "/health")
    assert attempts == [True]


@pytest.mark.asyncio
async def test_async_memory_client_classifies_content_type_error_as_protocol_error(monkeypatch):
    class ContentTypeError(RuntimeError):
        pass

    class ResponseContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        status = 200

        async def json(self):
            raise ContentTypeError("invalid content type")

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, *_args, **_kwargs):
            return ResponseContext()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientSession=Session, ClientTimeout=lambda **_kwargs: object()),
    )
    client = AsyncMemoryClient(
        "http://127.0.0.1:6001",
        identity=MemoryClientIdentity(
            actor="api_a",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
        ),
        max_retries=2,
        retry_base_seconds=0,
    )

    with pytest.raises(MemoryProtocolError, match="invalid JSON"):
        await client.request_json("GET", "/health")


@pytest.mark.asyncio
async def test_async_memory_client_honors_retry_after(monkeypatch):
    sleeps = []
    responses = [
        SimpleNamespace(
            status=503,
            headers={"Retry-After": "7"},
            text=lambda: None,
        ),
        SimpleNamespace(
            status=200,
            headers={},
        ),
    ]

    class ResponseContext:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, *_args):
            return False

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, *_args, **_kwargs):
            response = responses.pop(0)

            async def response_text():
                return "busy"

            async def response_json():
                return {"status": "ok"}

            response.text = response_text
            response.json = response_json
            return ResponseContext(response)

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientSession=Session,
            ClientTimeout=lambda **_kwargs: object(),
        ),
    )
    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("voidcube.infrastructure.memory.client.asyncio.sleep", fake_sleep)
    client = AsyncMemoryClient(
        "http://127.0.0.1:6001",
        identity=MemoryClientIdentity(
            actor="api_a",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
        ),
        max_retries=1,
        retry_base_seconds=0.05,
    )

    assert await client.request_json("GET", "/health") == {"status": "ok"}
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_async_memory_client_caps_retry_after(monkeypatch):
    sleeps = []
    responses = [
        SimpleNamespace(status=503, headers={"Retry-After": "999"}),
        SimpleNamespace(status=200, headers={}),
    ]

    class ResponseContext:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, *_args):
            return False

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, *_args, **_kwargs):
            response = responses.pop(0)

            async def response_text():
                return "busy"

            async def response_json():
                return {"status": "ok"}

            response.text = response_text
            response.json = response_json
            return ResponseContext(response)

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientSession=Session,
            ClientTimeout=lambda **_kwargs: object(),
        ),
    )
    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("voidcube.infrastructure.memory.client.asyncio.sleep", fake_sleep)
    client = AsyncMemoryClient(
        "http://127.0.0.1:6001",
        identity=MemoryClientIdentity(
            actor="api_a",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
        ),
        max_retries=1,
        retry_base_seconds=0.05,
    )

    assert await client.request_json("GET", "/health") == {"status": "ok"}
    assert sleeps == [60.0]


def test_memory_client_does_not_accept_non_http_endpoint():
    identity = MemoryClientIdentity(
        actor="api_a",
        owner_id="local-user",
        workspace_id="default",
        memory_domain="agent_interaction",
    )
    for url in (
        "memory://local",
        "http://",
        "http:///missing-host",
        "http://user:pass@memory.test",
        "http://memory.test?bad=1",
        "http://memory.test/#bad",
    ):
        with pytest.raises(ValueError, match="http or https"):
            MemoryClient(url, identity=identity)


def test_memory_client_classifies_non_utf8_response_as_protocol_error(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"\xff\xfe"

    monkeypatch.setattr(
        "voidcube.infrastructure.memory.client.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(MemoryProtocolError, match="invalid JSON"):
        _client().request_json("GET", "/health")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "invalid"])
def test_memory_client_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        _client(timeout_seconds=timeout)


@pytest.mark.parametrize("retry_base", [-1, float("nan"), float("inf"), "invalid"])
def test_memory_client_rejects_invalid_retry_base(retry_base):
    with pytest.raises(ValueError, match="finite non-negative number"):
        _client(retry_base_seconds=retry_base)
