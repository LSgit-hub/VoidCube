from __future__ import annotations

import json
import importlib

import pytest

from VoidCube_app.configuration import ApplicationConfigRuntime
from VoidCube_app.gateway import GatewayPresenceClient
from VoidCube_app.model_normalization import (
    AGGREGATOR_PROVIDERS,
    normalize_model_for_provider,
)


pytestmark = [pytest.mark.unit]


def test_config_runtime_loads_once_and_reloads_in_place() -> None:
    runtime = ApplicationConfigRuntime()
    first = {"delegation": {"model": "first"}}
    loaded = runtime.get(lambda: first)

    reloaded = runtime.reload(lambda: {"delegation": {"model": "second"}})

    assert loaded is first
    assert reloaded is loaded
    assert loaded == {"delegation": {"model": "second"}}
    assert runtime.section("delegation") == {"model": "second"}


def test_config_runtime_requires_loader_before_first_access() -> None:
    runtime = ApplicationConfigRuntime()

    with pytest.raises(RuntimeError, match="has not been loaded"):
        runtime.get()


def test_shared_model_normalization_preserves_provider_identifier() -> None:
    assert AGGREGATOR_PROVIDERS == {"openrouter", "nous"}
    assert normalize_model_for_provider("  vendor/model-v2  ", "custom") == "vendor/model-v2"


@pytest.mark.parametrize(
    ("compatibility_name", "canonical_name"),
    [
        ("VoidCube_cli.config", "VoidCube_app.config"),
        ("VoidCube_cli.env_loader", "VoidCube_app.environment"),
        ("VoidCube_cli.default_soul", "VoidCube_app.default_identity"),
        ("VoidCube_cli.runtime_provider", "VoidCube_app.runtime_provider"),
        ("VoidCube_cli.models", "VoidCube_app.models"),
    ],
)
def test_retained_cli_configuration_paths_alias_canonical_modules(
    compatibility_name: str,
    canonical_name: str,
) -> None:
    assert importlib.import_module(compatibility_name) is importlib.import_module(
        canonical_name
    )


def test_cli_auth_adapter_reuses_shared_provider_contract() -> None:
    cli_auth = importlib.import_module("VoidCube_cli.auth")
    provider_auth = importlib.import_module("VoidCube_app.provider_auth")

    assert cli_auth.PROVIDER_REGISTRY is provider_auth.PROVIDER_REGISTRY
    assert callable(cli_auth.login_command)
    assert not hasattr(provider_auth, "login_command")


def test_cli_plugin_adapter_reuses_shared_registry() -> None:
    cli_plugins = importlib.import_module("VoidCube_cli.plugins")
    shared_plugins = importlib.import_module("VoidCube_app.plugins")

    assert cli_plugins.get_plugin_manager() is shared_plugins.get_plugin_manager()
    assert cli_plugins.PluginManager is shared_plugins.PluginManager


def test_gateway_registration_uses_configured_address_and_auth() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))

    client = GatewayPresenceClient(
        "http://gateway.example:6123/base/",
        auth_token="gateway-token",
    )

    assert client.register_session(
        "session-1",
        "model-1",
        "provider-1",
        source="cli",
        opener=opener,
    )
    request, timeout = requests[0]
    assert request.full_url == "http://gateway.example:6123/base/v1/sessions/register"
    assert request.headers["Authorization"] == "Bearer gateway-token"
    assert timeout == 3
    assert json.loads(request.data) == {
        "session_id": "session-1",
        "model": "model-1",
        "provider": "provider-1",
        "source": "cli",
    }


def test_gateway_registration_can_forward_memory_scope() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))

    client = GatewayPresenceClient("http://gateway.example:6000")
    assert client.register_session(
        "session-scoped",
        "model-1",
        "provider-1",
        source="cli",
        owner_id="local-user",
        workspace_id="VoidCube",
        opener=opener,
    )

    payload = json.loads(requests[0][0].data)
    assert payload["owner_id"] == "local-user"
    assert payload["workspace_id"] == "VoidCube"


def test_cli_gateway_registration_uses_mem_provider_scope(monkeypatch) -> None:
    cli_app = importlib.import_module("VoidCube_cli.app")
    captured = {}

    def register(session_id, model, provider, **kwargs):
        captured.update(
            session_id=session_id,
            model=model,
            provider=provider,
            **kwargs,
        )
        return True

    monkeypatch.setattr(cli_app, "_register_gateway_session", register)

    assert cli_app._register_with_gateway("session-1", "model-1", "provider-1")
    assert captured == {
        "session_id": "session-1",
        "model": "model-1",
        "provider": "provider-1",
        "source": "cli",
        "owner_id": "local-user",
        "workspace_id": "VoidCube",
    }


def test_gateway_scene_projection_preserves_lane_metadata() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))

    client = GatewayPresenceClient("http://gateway.example:6000")
    result = client.push_agent_scene(
        " LEARNING ",
        source_service="cli_agent",
        session_id="session-1",
        task_id="task-1",
        execution_kind="research",
        agent_role="supervisor_task",
        subagent_summary={
            "foreground_count": 1,
            "background_count": 2,
            "focus_tool": "terminal",
        },
        opener=opener,
    )

    assert result is True
    payload = json.loads(requests[0][0].data)
    assert payload["metadata"] == {
        "scene": "learning",
        "task_id": "task-1",
        "execution_kind": "research",
        "agent_role": "supervisor_task",
        "subagent_foreground_count": 1,
        "subagent_background_count": 2,
        "subagent_total_count": 3,
        "subagent_focus_tool": "terminal",
    }
