from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.client_lifecycle import ChatClientLifecycle
from run_agent import AIAgent


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


class _Client:
    def __init__(self, kwargs: dict, *, connection=None) -> None:
        self.kwargs = dict(kwargs)
        self.api_key = kwargs.get("api_key")
        self.base_url = kwargs.get("base_url")
        self.is_closed = False
        self.close_count = 0
        connections = [] if connection is None else [connection]
        self._client = SimpleNamespace(
            is_closed=False,
            _transport=SimpleNamespace(
                _pool=SimpleNamespace(_connections=connections)
            ),
        )

    def close(self) -> None:
        self.close_count += 1
        self.is_closed = True
        self._client.is_closed = True


class _Socket:
    def __init__(self, *, dead: bool) -> None:
        self.dead = dead
        self.blocking: list[bool] = []
        self.shutdown_count = 0
        self.close_count = 0

    def setblocking(self, enabled: bool) -> None:
        self.blocking.append(enabled)

    def recv(self, *_args) -> bytes:
        if self.dead:
            return b""
        raise BlockingIOError

    def shutdown(self, _mode) -> None:
        self.shutdown_count += 1

    def close(self) -> None:
        self.close_count += 1


def _connection(sock: _Socket):
    return SimpleNamespace(_network_stream=SimpleNamespace(_sock=sock))


def _lifecycle(factory, *, initial=None):
    state = {
        "provider": "custom",
        "model": "safe-model",
        "base_url": "https://api.example/v1",
    }
    lifecycle = ChatClientLifecycle(
        client_kwargs=initial
        or {"api_key": "key-1", "base_url": state["base_url"]},
        provider=lambda: state["provider"],
        model=lambda: state["model"],
        base_url=lambda: state["base_url"],
        client_factory=factory,
    )
    return lifecycle, state


def test_initialize_and_request_clients_have_separate_lifetimes():
    created: list[_Client] = []

    def factory(kwargs):
        client = _Client(kwargs)
        created.append(client)
        return client

    lifecycle, _ = _lifecycle(factory)
    primary = lifecycle.initialize_primary(reason="test_init")
    request_client = lifecycle.create_request_client(reason="test_request")
    lifecycle.close_request_client(request_client, reason="test_complete")

    assert created == [primary, request_client]
    assert primary.close_count == 0
    assert request_client.close_count == 1
    assert lifecycle.primary is primary


def test_configure_replaces_primary_and_closes_old_client():
    created: list[_Client] = []

    def factory(kwargs):
        client = _Client(kwargs)
        created.append(client)
        return client

    lifecycle, _ = _lifecycle(factory)
    old_client = lifecycle.initialize_primary(reason="test_init")

    assert lifecycle.configure(
        {"api_key": "key-2", "base_url": "https://next.example/v1"},
        reason="test_switch",
    )

    assert lifecycle.primary is created[-1]
    assert old_client.close_count == 1
    assert lifecycle.snapshot_kwargs()["api_key"] == "key-2"


def test_failed_configuration_preserves_primary_and_parameters():
    calls = 0

    def factory(kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("cannot create client")
        return _Client(kwargs)

    lifecycle, _ = _lifecycle(factory)
    old_client = lifecycle.initialize_primary(reason="test_init")
    old_kwargs = lifecycle.snapshot_kwargs()

    assert lifecycle.configure(
        {"api_key": "broken", "base_url": "https://broken.example/v1"},
        reason="test_failure",
    ) is False
    assert lifecycle.primary is old_client
    assert lifecycle.snapshot_kwargs() == old_kwargs
    assert old_client.close_count == 0


def test_rebuild_cannot_restore_a_stale_configuration_during_concurrent_switch():
    rebuild_started = threading.Event()
    configure_started = threading.Event()
    release_rebuild = threading.Event()
    created: list[_Client] = []

    def factory(kwargs):
        client = _Client(kwargs)
        created.append(client)
        if len(created) > 1 and kwargs["api_key"] == "key-1":
            rebuild_started.set()
            assert release_rebuild.wait(timeout=2)
        elif kwargs["api_key"] == "key-2":
            configure_started.set()
        return client

    lifecycle, _ = _lifecycle(factory)
    lifecycle.initialize_primary(reason="test_init")
    rebuild_result: list[bool] = []
    configure_result: list[bool] = []

    rebuild_thread = threading.Thread(
        target=lambda: rebuild_result.append(
            lifecycle.replace_primary(reason="test_rebuild")
        )
    )
    rebuild_thread.start()
    assert rebuild_started.wait(timeout=2)

    configure_thread = threading.Thread(
        target=lambda: configure_result.append(
            lifecycle.configure(
                {"api_key": "key-2", "base_url": "https://next.example/v1"},
                reason="test_switch",
            )
        )
    )
    configure_thread.start()

    assert configure_started.wait(timeout=0.1) is False
    release_rebuild.set()
    rebuild_thread.join(timeout=2)
    configure_thread.join(timeout=2)

    assert rebuild_thread.is_alive() is False
    assert configure_thread.is_alive() is False
    assert rebuild_result == [True]
    assert configure_result == [True]
    assert lifecycle.snapshot_kwargs()["api_key"] == "key-2"
    assert lifecycle.primary.api_key == "key-2"


def test_adopt_uses_resolved_client_and_releases_previous_primary():
    lifecycle, _ = _lifecycle(lambda kwargs: _Client(kwargs))
    old_client = lifecycle.initialize_primary(reason="test_init")
    adopted = _Client({"api_key": "fallback", "base_url": "https://fallback.example/v1"})

    lifecycle.adopt(
        adopted,
        adopted.kwargs,
        reason="test_fallback",
    )

    assert lifecycle.primary is adopted
    assert old_client.close_count == 1
    assert adopted.close_count == 0


def test_ensure_primary_rebuilds_closed_client():
    created: list[_Client] = []

    def factory(kwargs):
        client = _Client(kwargs)
        created.append(client)
        return client

    lifecycle, _ = _lifecycle(factory)
    old_client = lifecycle.initialize_primary(reason="test_init")
    old_client.is_closed = True

    rebuilt = lifecycle.ensure_primary(reason="test_closed")

    assert rebuilt is created[-1]
    assert rebuilt is not old_client
    assert old_client.close_count == 1


def test_mock_primary_is_reused_for_request_tests():
    primary = Mock()
    lifecycle, _ = _lifecycle(lambda _kwargs: primary)
    lifecycle.initialize_primary(reason="test_init")

    assert lifecycle.create_request_client(reason="test_request") is primary


def test_dead_socket_rebuilds_primary_and_is_force_closed():
    dead_socket = _Socket(dead=True)
    created: list[_Client] = []

    def factory(kwargs):
        connection = _connection(dead_socket) if not created else None
        client = _Client(kwargs, connection=connection)
        created.append(client)
        return client

    lifecycle, _ = _lifecycle(factory)
    old_client = lifecycle.initialize_primary(reason="test_init")

    assert lifecycle.cleanup_dead_connections() is True
    assert lifecycle.primary is created[-1]
    assert lifecycle.primary is not old_client
    assert old_client.close_count == 1
    assert dead_socket.shutdown_count == 1
    assert dead_socket.close_count == 1
    assert dead_socket.blocking == [False, True]


def test_close_primary_is_idempotent():
    lifecycle, _ = _lifecycle(lambda kwargs: _Client(kwargs))
    primary = lifecycle.initialize_primary(reason="test_init")

    lifecycle.close_primary(reason="test_close")
    lifecycle.close_primary(reason="test_close_again")

    assert lifecycle.primary is None
    assert primary.close_count == 1


def test_credential_swap_only_updates_agent_after_client_creation_succeeds():
    agent = AIAgent.__new__(AIAgent)
    agent.api_key = "old-key"
    agent.base_url = "https://old.example/v1"
    agent._client_lifecycle = SimpleNamespace(configure=lambda *_args, **_kwargs: False)
    entry = SimpleNamespace(
        runtime_api_key="new-key",
        runtime_base_url="https://new.example/v1/",
    )

    assert agent._swap_credential(entry) is False
    assert agent.api_key == "old-key"
    assert agent.base_url == "https://old.example/v1"


def test_credential_swap_configures_transport_before_updating_runtime():
    configured: list[tuple[dict, str]] = []
    agent = AIAgent.__new__(AIAgent)
    agent.api_key = "old-key"
    agent.base_url = "https://old.example/v1"
    agent._client_lifecycle = SimpleNamespace(
        configure=lambda kwargs, *, reason: (
            configured.append((kwargs, reason)) or True
        )
    )
    entry = SimpleNamespace(
        runtime_api_key="new-key",
        runtime_base_url="https://new.example/v1/",
    )

    assert agent._swap_credential(entry) is True
    assert agent.api_key == "new-key"
    assert agent.base_url == "https://new.example/v1"
    assert configured == [
        (
            {"api_key": "new-key", "base_url": "https://new.example/v1"},
            "credential_rotation",
        )
    ]


def test_agent_interruptible_call_uses_and_closes_request_client():
    closed: list[tuple[object, str]] = []
    completions = SimpleNamespace(create=lambda **kwargs: {"request": kwargs})
    request_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    agent = AIAgent.__new__(AIAgent)
    agent._interrupt_requested = False
    agent._client_lifecycle = SimpleNamespace(
        create_request_client=lambda *, reason: request_client,
        close_request_client=lambda client, *, reason: closed.append(
            (client, reason)
        ),
    )

    response = agent._interruptible_api_call({"model": "safe-model"})

    assert response == {"request": {"model": "safe-model"}}
    assert closed == [(request_client, "request_complete")]


def test_failed_model_switch_restores_previous_runtime_fields():
    agent = AIAgent.__new__(AIAgent)
    agent.model = "primary-model"
    agent.provider = "custom"
    agent.base_url = "https://primary.example/v1"
    agent.api_key = "primary-key"
    agent._client_lifecycle = SimpleNamespace(configure=lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="selected model"):
        agent.switch_model(
            "next-model",
            "next-provider",
            api_key="next-key",
            base_url="https://next.example/v1",
        )

    assert (
        agent.model,
        agent.provider,
        agent.base_url,
        agent.api_key,
    ) == (
        "primary-model",
        "custom",
        "https://primary.example/v1",
        "primary-key",
    )


def test_successful_model_switch_updates_transport_and_primary_snapshot(monkeypatch):
    configured: list[tuple[dict, str]] = []

    class _Compressor:
        context_length = 100_000
        threshold_tokens = 50_000

        def update_model(self, **runtime) -> None:
            self.__dict__.update(runtime)
            self.threshold_tokens = runtime["context_length"] // 2

    lifecycle = SimpleNamespace(
        configure=lambda kwargs, *, reason: (
            configured.append((dict(kwargs), reason)) or True
        ),
        snapshot_kwargs=lambda: dict(configured[-1][0]),
    )
    agent = AIAgent.__new__(AIAgent)
    agent.model = "primary-model"
    agent.provider = "custom"
    agent.base_url = "https://primary.example/v1"
    agent.api_key = "primary-key"
    agent.context_compressor = _Compressor()
    agent._client_lifecycle = lifecycle
    agent._config_context_length = None
    agent._fallback_activated = True
    agent._fallback_index = 2
    agent._cached_system_prompt = "old prompt"
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 128_000,
    )

    agent.switch_model(
        "next-model",
        "next-provider",
        api_key="next-key",
        base_url="https://next.example/v1",
    )

    assert configured == [
        (
            {
                "api_key": "next-key",
                "base_url": "https://next.example/v1",
            },
            "switch_model",
        )
    ]
    assert agent._primary_runtime["client_kwargs"] == configured[0][0]
    assert agent.context_compressor.context_length == 128_000
    assert agent._fallback_activated is False
    assert agent._fallback_index == 0
    assert agent._cached_system_prompt is None


def test_agent_class_does_not_keep_legacy_client_lifecycle_methods():
    legacy_methods = (
        "_create_openai_client",
        "_close_openai_client",
        "_replace_primary_openai_client",
        "_ensure_primary_openai_client",
        "_create_request_openai_client",
        "_close_request_openai_client",
        "_cleanup_dead_connections",
    )

    assert all(not hasattr(AIAgent, method) for method in legacy_methods)
