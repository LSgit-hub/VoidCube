from __future__ import annotations

import pytest

from VoidCube_app.provider_auth import (
    PROVIDER_REGISTRY,
    RUNTIME_PROVIDER_IDS,
    resolve_api_key_provider_credentials,
)
from VoidCube_app.models import (
    curated_models_for_provider,
    list_available_providers,
    parse_model_input,
    provider_model_ids,
    validate_requested_model,
)
from VoidCube_cli.providers import resolve_provider_full
from VoidCube_app.runtime_provider import AuthError, resolve_runtime_provider


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

_EXPECTED_RUNTIME_PROVIDERS = (
    "openrouter",
    "nous",
    "openai",
    "deepseek",
    "zai",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "qwen-oauth",
    "copilot-acp",
    "custom",
)

_UNSUPPORTED_BUILTIN_PROVIDERS = (
    "copilot",
    "gemini",
    "xai",
    "xiaomi",
    "opencode-zen",
    "opencode-go",
    "ai-gateway",
    "kilocode",
    "alibaba",
    "huggingface",
)


def test_model_menu_contains_only_runtime_provider_ids():
    menu_ids = tuple(item["id"] for item in list_available_providers())

    assert menu_ids == _EXPECTED_RUNTIME_PROVIDERS
    assert set(menu_ids) == set(RUNTIME_PROVIDER_IDS)


def test_model_catalogs_are_live_api_only(monkeypatch):
    monkeypatch.setattr(
        "VoidCube_app.runtime_provider.resolve_runtime_provider",
        lambda requested: {
            "provider": requested,
            "base_url": "https://models.example/v1",
            "api_key": "sk-model-list-token",
        },
    )
    calls = []

    def fetch_models(api_key, base_url):
        calls.append((api_key, base_url))
        return ["current-model", "new-model"]

    monkeypatch.setattr("VoidCube_app.models.fetch_api_models", fetch_models)

    assert provider_model_ids("kimi-coding") == ["current-model", "new-model"]
    assert curated_models_for_provider("kimi-coding") == [
        ("current-model", ""),
        ("new-model", ""),
    ]
    assert calls == [
        ("sk-model-list-token", "https://models.example/v1"),
        ("sk-model-list-token", "https://models.example/v1"),
    ]


def test_model_catalog_api_failure_has_no_static_fallback(monkeypatch):
    monkeypatch.setattr(
        "VoidCube_app.runtime_provider.resolve_runtime_provider",
        lambda requested: {
            "provider": requested,
            "base_url": "https://models.example/v1",
            "api_key": "sk-model-list-token",
        },
    )
    monkeypatch.setattr("VoidCube_app.models.fetch_api_models", lambda *_args: None)

    assert provider_model_ids("zai") == []
    assert curated_models_for_provider("zai") == []


def test_model_validation_rejects_ids_missing_from_live_catalog(monkeypatch):
    monkeypatch.setattr(
        "VoidCube_app.models.fetch_api_models",
        lambda *_args: ["current-model", "new-model"],
    )

    result = validate_requested_model(
        "expired-model",
        "kimi-coding",
        api_key="sk-model-list-token",
        base_url="https://models.example/v1",
    )

    assert result["accepted"] is False
    assert result["persist"] is False
    assert result["recognized"] is False


def test_model_validation_allows_unverified_input_only_when_api_is_unavailable(
    monkeypatch,
):
    monkeypatch.setattr("VoidCube_app.models.fetch_api_models", lambda *_args: None)

    result = validate_requested_model(
        "manual-model",
        "kimi-coding",
        api_key="sk-model-list-token",
        base_url="https://models.example/v1",
    )

    assert result["accepted"] is True
    assert result["persist"] is True
    assert result["recognized"] is False


@pytest.mark.parametrize(
    ("provider", "env_vars", "base_url_env_var"),
    (
        ("zai", ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), "GLM_BASE_URL"),
        ("kimi-coding", ("KIMI_API_KEY",), "KIMI_BASE_URL"),
        ("minimax", ("MINIMAX_API_KEY",), "MINIMAX_BASE_URL"),
        ("minimax-cn", ("MINIMAX_CN_API_KEY",), "MINIMAX_CN_BASE_URL"),
    ),
)
def test_api_key_provider_registry_has_complete_runtime_mapping(
    provider,
    env_vars,
    base_url_env_var,
):
    config = PROVIDER_REGISTRY[provider]

    assert tuple(config["api_key_env_vars"]) == env_vars
    assert config["base_url_env_var"] == base_url_env_var
    assert config["auth_type"] == "api_key"
    assert config["inference_base_url"].startswith("https://")


def test_api_key_credentials_honor_provider_base_url_env(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-real-kimi-token-12345678901234567890")
    monkeypatch.setenv("KIMI_BASE_URL", "https://models.example/v1")

    credentials = resolve_api_key_provider_credentials("kimi-coding")

    assert credentials == {
        "api_key": "sk-real-kimi-token-12345678901234567890",
        "base_url": "https://models.example/v1",
    }


@pytest.mark.parametrize("provider", _UNSUPPORTED_BUILTIN_PROVIDERS)
def test_unsupported_builtin_provider_has_no_catalog_or_runtime_fallback(
    monkeypatch,
    provider,
):
    monkeypatch.setattr(
        "VoidCube_app.runtime_provider.load_config",
        lambda: {"providers": {}, "runtime": {}, "agent": {}},
    )

    model_input = f"{provider}:example-model"
    assert curated_models_for_provider(provider) == []
    assert resolve_provider_full(provider) is None
    assert parse_model_input(model_input, "openrouter") == (
        "openrouter",
        model_input,
    )
    with pytest.raises(AuthError, match="not supported by the active runtime"):
        resolve_runtime_provider(requested=provider)


def test_named_custom_provider_remains_resolvable():
    providers = {
        "research-endpoint": {
            "label": "Research Endpoint",
            "base_url": "https://models.example/v1",
            "api_key_env": "RESEARCH_API_KEY",
        }
    }

    resolved = resolve_provider_full("research-endpoint", providers)

    assert resolved is not None
    assert resolved.id == "research-endpoint"
    assert resolved.base_url == "https://models.example/v1"
    assert resolved.api_key_env_vars == ("RESEARCH_API_KEY",)


def test_named_custom_provider_remains_runtime_resolvable(monkeypatch):
    config = {
        "providers": {
            "research-endpoint": {
                "label": "Research Endpoint",
                "base_url": "https://models.example/v1",
                "api_key": "sk-research-token",
                "selected_model": "research-model",
            }
        },
        "runtime": {},
        "agent": {},
    }
    monkeypatch.setattr("VoidCube_app.runtime_provider.load_config", lambda: config)

    runtime = resolve_runtime_provider(requested="research-endpoint")

    assert runtime["provider"] == "custom"
    assert runtime["base_url"] == "https://models.example/v1"
    assert runtime["api_key"] == "sk-research-token"
    assert runtime["model"] == "research-model"
