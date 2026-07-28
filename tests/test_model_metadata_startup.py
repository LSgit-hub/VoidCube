import socket

from agent import model_metadata


def test_local_server_detection_stops_after_failed_tcp_preflight(monkeypatch):
    base_url = "http://127.0.0.1:61999/v1"
    model_metadata._local_server_type_cache.pop(base_url.rstrip("/"), None)
    attempts = []

    def fail_connect(address, timeout):
        attempts.append((address, timeout))
        raise OSError("offline")

    monkeypatch.setattr(socket, "create_connection", fail_connect)

    assert model_metadata.detect_local_server_type(base_url) is None
    assert attempts == [(('127.0.0.1', 61999), model_metadata._LOCAL_CONNECT_TIMEOUT)]


def test_unavailable_local_endpoint_uses_fallback_without_remote_metadata(monkeypatch):
    monkeypatch.setattr(model_metadata, "get_cached_context_length", lambda *_: None)
    monkeypatch.setattr(model_metadata, "detect_local_server_type", lambda *_: None)
    monkeypatch.setattr(
        model_metadata,
        "fetch_endpoint_model_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("endpoint probe")),
    )
    monkeypatch.setattr(
        model_metadata,
        "fetch_model_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote probe")),
    )

    result = model_metadata.get_model_context_length(
        "offline-model",
        base_url="http://127.0.0.1:9/v1",
        provider="custom",
    )

    assert result == model_metadata.DEFAULT_FALLBACK_CONTEXT


def test_custom_endpoint_does_not_fall_back_to_unrelated_provider_metadata(monkeypatch):
    monkeypatch.setattr(model_metadata, "get_cached_context_length", lambda *_: None)
    monkeypatch.setattr(
        model_metadata,
        "fetch_endpoint_model_metadata",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        model_metadata,
        "fetch_model_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote probe")),
    )

    result = model_metadata.get_model_context_length(
        "shared-model-name",
        base_url="https://models.example.test/v1",
        provider="custom",
    )

    assert result == model_metadata.DEFAULT_FALLBACK_CONTEXT
