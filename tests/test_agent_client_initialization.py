from types import SimpleNamespace

import pytest

from voidcube.runtime.agent.client_initialization import (
    AgentClientInitializationPorts,
    AgentClientInitializationRuntime,
)


class _Lifecycle:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.initialize_reasons: list[str] = []

    def initialize_primary(self, *, reason: str):
        self.initialize_reasons.append(reason)
        return object()


def _ports(**overrides):
    values = {
        "requested_api_key": "key",
        "requested_base_url": "https://api.example/v1",
        "provider": "custom",
        "model": "model",
        "acp_command": None,
        "acp_args": (),
        "provider_client_resolver": lambda *_args, **_kwargs: (None, None),
        "lifecycle_factory": _Lifecycle,
        "provider_reader": lambda: "custom",
        "model_reader": lambda: "model",
        "base_url_reader": lambda: "https://api.example/v1",
    }
    values.update(overrides)
    return AgentClientInitializationPorts(**values)


def test_explicit_credentials_build_lifecycle_and_acp_args():
    runtime = AgentClientInitializationRuntime(
        _ports(
            provider="copilot-acp",
            acp_command="copilot",
            acp_args=("--stdio",),
        )
    )

    result = runtime.initialize()

    assert result.api_key == "key"
    assert result.base_url == "https://api.example/v1"
    assert result.client_kwargs["command"] == "copilot"
    assert result.client_kwargs["args"] == ["--stdio"]
    assert result.lifecycle.initialize_reasons == ["agent_init"]
    assert result.lifecycle.kwargs["provider"]() == "custom"


def test_routed_client_is_converted_to_lifecycle_kwargs():
    routed = SimpleNamespace(
        api_key="routed-key",
        base_url="https://routed.example/v1",
        _default_headers={"X-Test": "routed"},
    )
    calls = []

    def resolve(provider, *, model):
        calls.append((provider, model))
        return routed, "resolved-model"

    result = AgentClientInitializationRuntime(
        _ports(
            requested_api_key=None,
            requested_base_url=None,
            provider="auto",
            provider_client_resolver=resolve,
        )
    ).initialize()

    assert calls == [("auto", "model")]
    assert result.client_kwargs == {
        "api_key": "routed-key",
        "base_url": "https://routed.example/v1",
        "default_headers": {"X-Test": "routed"},
    }


def test_unconfigured_explicit_provider_fails_before_client_creation():
    lifecycle_created = False

    def lifecycle_factory(**_kwargs):
        nonlocal lifecycle_created
        lifecycle_created = True
        return _Lifecycle()

    with pytest.raises(RuntimeError, match="Provider 'deepseek'"):
        AgentClientInitializationRuntime(
            _ports(
                requested_api_key=None,
                requested_base_url=None,
                provider="deepseek",
                lifecycle_factory=lifecycle_factory,
            )
        ).initialize()

    assert lifecycle_created is False


def test_auto_mode_uses_openrouter_default_when_router_has_no_client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    result = AgentClientInitializationRuntime(
        _ports(
            requested_api_key=None,
            requested_base_url=None,
            provider="auto",
        )
    ).initialize()

    assert result.api_key == "env-key"
    assert result.base_url == "https://openrouter.ai/api/v1"
    assert result.client_kwargs["default_headers"]["X-OpenRouter-Title"] == "Voidcube Agent"


def test_primary_client_failure_keeps_initialization_error_boundary():
    class _FailingLifecycle(_Lifecycle):
        def initialize_primary(self, *, reason: str):
            raise ValueError("bad credentials")

    with pytest.raises(RuntimeError, match="Failed to initialize OpenAI-compatible client") as exc:
        AgentClientInitializationRuntime(
            _ports(lifecycle_factory=_FailingLifecycle)
        ).initialize()

    assert isinstance(exc.value.__cause__, ValueError)
