from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent.chat_transport import ChatTransport


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


def test_agent_mainline_has_no_direct_chat_completion_transport_calls():
    source = (Path(__file__).resolve().parents[1] / "run_agent.py").read_text(
        encoding="utf-8"
    )

    assert ".chat.completions.create(" not in source


def _chunk(*, content=None, finish_reason=None, choices=True, usage=None):
    values = []
    if choices:
        values.append(
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    reasoning_content=None,
                    reasoning=None,
                    tool_calls=None,
                ),
                finish_reason=finish_reason,
            )
        )
    return SimpleNamespace(model="safe-model", usage=usage, choices=values)


class _Client:
    def __init__(self, create) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Lifecycle:
    def __init__(self, clients) -> None:
        self.clients = list(clients)
        self.created: list[tuple[object, str]] = []
        self.closed: list[tuple[object, str]] = []
        self.replaced: list[str] = []

    def create_request_client(self, *, reason):
        client = self.clients.pop(0)
        self.created.append((client, reason))
        return client

    def close_request_client(self, client, *, reason):
        client.close()
        self.closed.append((client, reason))

    def replace_primary(self, *, reason):
        self.replaced.append(reason)
        return True


def _transport(lifecycle, **kwargs):
    return ChatTransport(
        client_lifecycle=lifecycle,
        base_url=lambda: "https://api.example/v1",
        model=lambda: "safe-model",
        interrupted=lambda: False,
        poll_interval=0.01,
        **kwargs,
    )


def test_non_streaming_request_uses_and_closes_worker_client():
    client = _Client(lambda **kwargs: {"request": kwargs})
    lifecycle = _Lifecycle([client])

    response = _transport(lifecycle).complete({"model": "safe-model"})

    assert response == {"request": {"model": "safe-model"}}
    assert lifecycle.closed == [(client, "request_complete")]


def test_stream_assembles_response_and_emits_updates(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=2, completion_tokens=1)
    stream = [
        _chunk(content="hello "),
        _chunk(content="world", finish_reason="stop"),
        _chunk(choices=False, usage=usage),
    ]
    client = _Client(lambda **_kwargs: stream)
    lifecycle = _Lifecycle([client])
    updates = []
    first = []
    activity = []
    monkeypatch.setenv("VOIDCUBE_STREAM_STALE_TIMEOUT", "60")

    response = _transport(lifecycle, activity=activity.append).stream(
        {"model": "safe-model", "messages": []},
        on_update=updates.append,
        on_first_delta=lambda: first.append(True),
    )

    assert [update.content for update in updates if update.content] == [
        "hello ",
        "world",
    ]
    assert first == [True]
    assert response.usage is usage
    assert response.choices[0].message.content == "hello world"
    assert activity == [
        "waiting for provider response (streaming)",
        "receiving stream response",
    ]
    assert lifecycle.closed == [(client, "stream_request_complete")]


def test_stream_transport_respects_per_request_timeout():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return [_chunk(content="ok", finish_reason="stop")]

    lifecycle = _Lifecycle([_Client(create)])

    _transport(lifecycle).stream(
        {"model": "safe-model", "messages": [], "timeout": 7.0},
        on_update=lambda _update: None,
    )

    timeout = captured["timeout"]
    assert timeout.connect == 7.0
    assert timeout.read == 7.0
    assert timeout.write == 7.0
    assert timeout.pool == 7.0


def test_unsupported_stream_falls_back_to_non_streaming(monkeypatch):
    streaming = _Client(
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("stream is not supported")
        )
    )
    fallback_response = SimpleNamespace(choices=[1])
    fallback = _Client(lambda **_kwargs: fallback_response)
    lifecycle = _Lifecycle([streaming, fallback])
    warnings = []
    monkeypatch.setenv("VOIDCUBE_STREAM_RETRIES", "0")

    response = _transport(lifecycle, emit_warning=warnings.append).stream(
        {"model": "safe-model", "messages": []},
        on_update=lambda _update: None,
    )

    assert response is fallback_response
    assert warnings == [
        "Streaming is not supported for this model/provider; falling back to "
        "non-streaming."
    ]
    assert [reason for _, reason in lifecycle.closed] == [
        "stream_fallback_cleanup",
        "request_complete",
    ]


def test_transient_stream_failure_rebuilds_and_retries(monkeypatch):
    failed = _Client(
        lambda **_kwargs: (_ for _ in ()).throw(
            httpx.RemoteProtocolError("peer closed")
        )
    )
    recovered = _Client(
        lambda **_kwargs: [_chunk(content="recovered", finish_reason="stop")]
    )
    lifecycle = _Lifecycle([failed, recovered])
    statuses = []
    monkeypatch.setenv("VOIDCUBE_STREAM_RETRIES", "1")

    response = _transport(lifecycle, emit_status=statuses.append).stream(
        {"model": "safe-model", "messages": []},
        on_update=lambda _update: None,
    )

    assert response.choices[0].message.content == "recovered"
    assert lifecycle.replaced == ["stream_retry_pool_cleanup"]
    assert statuses and "Reconnecting" in statuses[0]


def test_retry_survives_observer_and_primary_rebuild_errors(monkeypatch):
    failed = _Client(
        lambda **_kwargs: (_ for _ in ()).throw(
            httpx.RemoteProtocolError("peer closed")
        )
    )
    recovered = _Client(
        lambda **_kwargs: [_chunk(content="ok", finish_reason="stop")]
    )

    class _NoisyLifecycle(_Lifecycle):
        def replace_primary(self, *, reason):
            raise RuntimeError(f"cannot replace: {reason}")

    lifecycle = _NoisyLifecycle([failed, recovered])
    monkeypatch.setenv("VOIDCUBE_STREAM_RETRIES", "1")

    response = _transport(
        lifecycle,
        emit_status=lambda _message: (_ for _ in ()).throw(
            RuntimeError("observer failed")
        ),
    ).stream(
        {"model": "safe-model", "messages": []},
        on_update=lambda _update: None,
    )

    assert response.choices[0].message.content == "ok"


def test_partial_visible_stream_returns_stub_without_retry(monkeypatch):
    class _BrokenStream:
        response = None

        def __iter__(self):
            yield _chunk(content="partial")
            raise httpx.RemoteProtocolError("connection lost")

    client = _Client(lambda **_kwargs: _BrokenStream())
    lifecycle = _Lifecycle([client])
    delivered = []
    monkeypatch.setenv("VOIDCUBE_STREAM_RETRIES", "2")

    response = _transport(lifecycle).stream(
        {"model": "safe-model", "messages": []},
        on_update=delivered.append,
    )

    assert [update.content for update in delivered] == ["partial"]
    assert response.id == "partial-stream-stub"
    assert len(lifecycle.created) == 1
    assert lifecycle.replaced == []


def test_preexisting_interrupt_does_not_create_a_client():
    lifecycle = _Lifecycle([])
    transport = ChatTransport(
        client_lifecycle=lifecycle,
        base_url=lambda: "https://api.example/v1",
        model=lambda: "safe-model",
        interrupted=lambda: True,
    )

    with pytest.raises(InterruptedError, match="before API call"):
        transport.complete({"model": "safe-model"})
    with pytest.raises(InterruptedError, match="before streaming"):
        transport.stream(
            {"model": "safe-model", "messages": []},
            on_update=lambda _update: None,
        )

    assert lifecycle.created == []


def test_stale_stream_closes_request_and_retries(monkeypatch):
    released = threading.Event()

    class _StaleStream:
        response = None

        def __iter__(self):
            assert released.wait(timeout=2)
            raise httpx.RemoteProtocolError("closed by monitor")
            yield

    stale = _Client(lambda **_kwargs: _StaleStream())

    def close_stale():
        stale.closed = True
        released.set()

    stale.close = close_stale
    recovered = _Client(
        lambda **_kwargs: [_chunk(content="ok", finish_reason="stop")]
    )
    lifecycle = _Lifecycle([stale, recovered])
    statuses = []
    monkeypatch.setenv("VOIDCUBE_STREAM_STALE_TIMEOUT", "0")
    monkeypatch.setenv("VOIDCUBE_STREAM_RETRIES", "1")

    response = _transport(lifecycle, emit_status=statuses.append).stream(
        {"model": "safe-model", "messages": []},
        on_update=lambda _update: None,
    )

    assert response.choices[0].message.content == "ok"
    assert any(reason == "stale_stream_kill" for _, reason in lifecycle.closed)
    assert lifecycle.replaced == ["stale_stream_pool_cleanup"]
    assert any("No response" in status for status in statuses)


def test_invalid_transport_environment_values_use_defaults(monkeypatch):
    monkeypatch.setenv("VOIDCUBE_API_TIMEOUT", "invalid")
    monkeypatch.setenv("VOIDCUBE_STREAM_READ_TIMEOUT", "invalid")
    monkeypatch.setenv("VOIDCUBE_STREAM_STALE_TIMEOUT", "invalid")
    monkeypatch.setenv("VOIDCUBE_STREAM_RETRIES", "invalid")
    client = _Client(
        lambda **_kwargs: [_chunk(content="ok", finish_reason="stop")]
    )
    lifecycle = _Lifecycle([client])
    transport = _transport(lifecycle)

    response = transport.stream(
        {"model": "safe-model", "messages": []},
        on_update=lambda _update: None,
    )

    assert response.choices[0].message.content == "ok"
    assert transport.stream_stale_timeout({"messages": []}) == 180.0
