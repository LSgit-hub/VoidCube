from __future__ import annotations

import pytest

from voidcube.infrastructure.providers.registry import (
    PROVIDER_REGISTRY,
    RUNTIME_PROVIDER_IDS,
)
from voidcube.infrastructure.providers.auth import normalize_openai_compatible_base_url
from voidcube.infrastructure.providers.credentials import resolve_api_key_provider_credentials
from voidcube.infrastructure.providers.registry import (
    PROVIDER_REGISTRY as CANONICAL_PROVIDER_REGISTRY,
    RUNTIME_PROVIDER_IDS as CANONICAL_RUNTIME_PROVIDER_IDS,
)
from voidcube.infrastructure.providers.model_catalog import (
    curated_models_for_provider,
    fetch_ollama_models,
    list_available_providers,
    parse_model_input,
    provider_model_ids,
    validate_requested_model,
)
from voidcube.interfaces.cli.providers import resolve_provider_full
from voidcube.infrastructure.providers.auxiliary_client import resolve_provider_client
from voidcube.infrastructure.providers.runtime import AuthError, resolve_runtime_provider


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

_EXPECTED_RUNTIME_PROVIDERS = (
    "ollama",
    "openrouter",
    "nous",
    "openai",
    "deepseek",
    "agnes-ai",
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
    assert PROVIDER_REGISTRY is CANONICAL_PROVIDER_REGISTRY
    assert RUNTIME_PROVIDER_IDS is CANONICAL_RUNTIME_PROVIDER_IDS


def test_ollama_is_explicitly_local_and_unauthenticated():
    provider = resolve_provider_full("ollama")
    assert provider is not None
    assert provider.auth_type == "none"
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.api_key_env_vars == ()


def test_ollama_native_tags_catalog_preserves_model_tags(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"models": [{"name": "qwen3:8b"}, {"model": "llama3.2"}, {"name": "qwen3:8b"}]}'

    monkeypatch.setattr(
        "voidcube.infrastructure.providers.model_catalog.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    assert fetch_ollama_models("http://localhost:11434/v1") == ["qwen3:8b", "llama3.2"]


def test_model_catalogs_are_live_api_only(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.runtime.resolve_runtime_provider",
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

    monkeypatch.setattr("voidcube.infrastructure.providers.model_catalog.fetch_api_models", fetch_models)

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
        "voidcube.infrastructure.providers.runtime.resolve_runtime_provider",
        lambda requested: {
            "provider": requested,
            "base_url": "https://models.example/v1",
            "api_key": "sk-model-list-token",
        },
    )
    monkeypatch.setattr("voidcube.infrastructure.providers.model_catalog.fetch_api_models", lambda *_args: None)

    assert provider_model_ids("zai") == []
    assert curated_models_for_provider("zai") == []


def test_model_validation_rejects_ids_missing_from_live_catalog(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.model_catalog.fetch_api_models",
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
    monkeypatch.setattr("voidcube.infrastructure.providers.model_catalog.fetch_api_models", lambda *_args: None)

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
        "voidcube.infrastructure.providers.runtime.load_config",
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
    monkeypatch.setattr("voidcube.infrastructure.providers.runtime.load_config", lambda: config)

    runtime = resolve_runtime_provider(requested="research-endpoint")

    assert runtime["provider"] == "research-endpoint"
    assert runtime["base_url"] == "https://models.example/v1"
    assert runtime["api_key"] == "sk-research-token"
    assert runtime["model"] == "research-model"


def test_named_custom_provider_does_not_fall_back_to_global_api_key(monkeypatch):
    config = {
        "providers": {
            "untrusted-endpoint": {
                "base_url": "https://models.example/v1",
                "selected_model": "untrusted-model",
            }
        },
        "runtime": {},
        "agent": {},
    }
    monkeypatch.setattr("voidcube.infrastructure.providers.runtime.load_config", lambda: config)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-global-secret-123456789")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-router-secret-123456789")

    with pytest.raises(AuthError, match="requires a configured API key"):
        resolve_runtime_provider(requested="untrusted-endpoint")

    client, model = resolve_provider_client(
        "untrusted-endpoint",
        model="untrusted-model",
    )
    assert client is None
    assert model is None


def test_named_custom_provider_normalizes_legacy_completion_endpoint(monkeypatch):
    config = {
        "providers": {
            "agnes-ai": {
                "label": "Agnes-AI",
                "base_url": "https://api.agnes-ai.cn/v1/chat/completions",
                "api_key": "sk-research-token",
                "selected_model": "agnes-2.5-flash",
            }
        },
        "runtime": {},
        "agent": {},
    }
    monkeypatch.setattr("voidcube.infrastructure.providers.runtime.load_config", lambda: config)

    runtime = resolve_runtime_provider(requested="agnes-ai")

    assert runtime["base_url"] == "https://api.agnes-ai.cn/v1"


def test_openai_compatible_url_normalizer_uses_v1_for_host_root():
    assert normalize_openai_compatible_base_url("https://example.test") == "https://example.test/v1"


def test_api_key_provider_reads_persisted_voidcube_environment(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.get_env_value",
        lambda name: "sk-persisted-agnes-token-123456789" if name == "AGNES_API_KEY" else None,
    )

    credentials = resolve_api_key_provider_credentials("agnes-ai")

    assert credentials["api_key"] == "sk-persisted-agnes-token-123456789"
