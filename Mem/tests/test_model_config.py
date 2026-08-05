from __future__ import annotations

from memai import MEM_MODEL_ROLES, MemModelConfig, MemModelConfigSet
from memai.model_config import (
    _resolve_mem_api_key,
    resolve_mem_llm,
    resolve_mem_llm_client,
)


def test_mem_model_config_reads_new_voidcube_cli_memory_llm_block() -> None:
    config = {
        "memory": {
            "provider": "mem",
            "llm": {
                "provider": "openrouter",
                "model": "qwen/qwen3.6-plus",
                "api_key_env": "OPENROUTER_API_KEY",
                "base_url": "https://openrouter.ai/api/v1",
                "provider_profile": "openai",
                "response_content_style": "openai_message",
            },
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openrouter"
    assert model_config.model == "qwen/qwen3.6-plus"
    assert model_config.api_key_env == "OPENROUTER_API_KEY"
    assert model_config.base_url == "https://openrouter.ai/api/v1"
    assert model_config.response_content_style == "openai_message"


def test_mem_model_config_keeps_memory_provider_as_plugin_identity() -> None:
    config = {"memory": {"provider": "mem"}}

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openai"
    assert model_config.model is None
    assert model_config.api_key_env == "OPENAI_API_KEY"


def test_mem_model_config_ignores_memory_plugin_level_model() -> None:
    config = {
        "memory": {
            "provider": "mem",
            "model": "gpt-4o-mini",
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openai"
    assert model_config.model is None


def test_mem_model_config_does_not_treat_memory_provider_as_llm_provider() -> None:
    config = {
        "memory": {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openai"
    assert model_config.model is None
    assert model_config.api_key_env == "OPENAI_API_KEY"
    assert model_config.base_url == "https://api.openai.com/v1"


def test_mem_model_config_cli_args_override_saved_config() -> None:
    class Args:
        model = "override-model"
        api_key_env = "OVERRIDE_API_KEY"
        base_url = "https://override.example/v1"
        provider_profile = "openai"
        provider_profile_file = None
        chat_completions_path = "/override/chat"
        system_prompt_style = None
        response_format_style = "none"
        response_content_style = None

    model_config = MemModelConfig.from_voidcube_config(
        {"memory": {"llm": {"provider": "openrouter", "model": "saved-model"}}}
    ).with_cli_overrides(Args())

    assert model_config.provider == "openrouter"
    assert model_config.model == "override-model"
    assert model_config.api_key_env == "OVERRIDE_API_KEY"
    assert model_config.base_url == "https://override.example/v1"
    assert model_config.provider_profile == "openai"
    assert model_config.chat_completions_path == "/override/chat"
    assert model_config.response_format_style == "none"


def test_mem_model_config_set_resolves_role_overrides() -> None:
    config = {
        "memory": {
            "llm": {
                "provider": "openrouter",
                "model": "default-memory-model",
                "api_key_env": "OPENROUTER_API_KEY",
                "base_url": "https://openrouter.ai/api/v1",
                "roles": {
                    "extraction": {
                        "model": "cheap-extraction-model",
                    },
                    "governance_reasoner": {
                        "provider": "deepseek",
                        "model": "deepseek-reasoner",
                    },
                    "unknown_role": {
                        "model": "ignored",
                    },
                },
            }
        }
    }

    config_set = MemModelConfigSet.from_voidcube_config(config)

    assert config_set.default.model == "default-memory-model"
    assert config_set.for_role("extraction").provider == "openrouter"
    assert config_set.for_role("extraction").model == "cheap-extraction-model"
    assert config_set.for_role("governance_reasoner").provider == "deepseek"
    assert config_set.for_role("governance_reasoner").model == "deepseek-reasoner"
    assert config_set.for_role("governance_reasoner").api_key_env == "DEEPSEEK_API_KEY"
    assert config_set.for_role("governance_reasoner").base_url == "https://api.deepseek.com/v1"
    assert "unknown_role" not in config_set.roles


def test_mem_model_roles_include_expected_governance_roles() -> None:
    assert "extraction" in MEM_MODEL_ROLES
    assert "governance_summary" in MEM_MODEL_ROLES
    assert "governance_reasoner" in MEM_MODEL_ROLES
    assert "embedding" not in MEM_MODEL_ROLES


def test_mem_model_config_rewrites_local_gateway_loopback_base_url() -> None:
    config = {
        "memory": {
            "provider": "mem",
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "base_url": "http://127.0.0.1:6000/v1",
            },
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.base_url == "https://api.deepseek.com/v1"
    assert model_config.api_key_env == "DEEPSEEK_API_KEY"


def test_mem_model_config_repairs_stale_openai_key_env_when_provider_changed() -> None:
    config = {
        "memory": {
            "provider": "mem",
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key_env": "OPENAI_API_KEY",
            },
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "deepseek"
    assert model_config.api_key_env == "DEEPSEEK_API_KEY"


def test_resolve_mem_llm_client_rejects_loopback_gateway_without_real_mem_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("VoidCube_app.config.get_env_value", lambda _key: "")
    monkeypatch.setattr(
        "VoidCube_app.provider_auth.resolve_api_key_provider_credentials",
        lambda _provider: {},
    )
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda _provider: None)
    monkeypatch.setattr(
        "memai.model_config.load_voidcube_mem_model_config_set",
        lambda: MemModelConfigSet.from_voidcube_config(
            {
                "memory": {
                    "provider": "mem",
                    "llm": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "base_url": "http://127.0.0.1:6000/v1",
                        "api_key_env": "",
                    },
                }
            }
        ),
    )

    client, model = resolve_mem_llm_client(role="default")

    assert client is None
    assert model == "deepseek-v4-flash"


def test_resolve_mem_llm_reports_policy_block_reason(monkeypatch) -> None:
    monkeypatch.setattr(
        "memai.model_config.load_voidcube_mem_model_config_set",
        lambda: MemModelConfigSet(
            default=MemModelConfig(
                provider="blocked-provider",
                model="blocked-model",
                api_key_env="BLOCKED_API_KEY",
                base_url="https://blocked.example/v1",
            ),
            roles={},
        ),
    )
    monkeypatch.setattr(
        "agent.integration_policy.require_active_integration",
        lambda *_values: (_ for _ in ()).throw(ValueError("retired integration")),
    )

    resolution = resolve_mem_llm()

    assert resolution.client is None
    assert resolution.model == "blocked-model"
    assert resolution.status == "policy_blocked"
    assert resolution.detail == "retired integration"


def test_resolve_mem_llm_reports_missing_credential_source(monkeypatch) -> None:
    monkeypatch.setattr(
        "memai.model_config.load_voidcube_mem_model_config_set",
        lambda: MemModelConfigSet(
            default=MemModelConfig(
                provider="deepseek",
                model="deepseek-v4-flash",
                api_key_env="DEEPSEEK_API_KEY",
                base_url="https://api.deepseek.com/v1",
            ),
            roles={},
        ),
    )
    monkeypatch.setattr("memai.model_config._resolve_mem_api_key", lambda _config: "")

    resolution = resolve_mem_llm()

    assert resolution.client is None
    assert resolution.status == "api_key_unavailable"
    assert resolution.detail == "no usable credential found via DEEPSEEK_API_KEY"


def test_resolve_mem_api_key_uses_matching_provider_auth_store(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("VoidCube_app.config.get_env_value", lambda _key: "")
    monkeypatch.setattr(
        "VoidCube_app.provider_auth._load_auth_store",
        lambda: {"deepseek": {"api_key": "sk-deepseek-auth-store-token-123456"}},
    )

    api_key = _resolve_mem_api_key(
        MemModelConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com/v1",
        )
    )

    assert api_key == "sk-deepseek-auth-store-token-123456"


def test_resolve_mem_api_key_reads_selected_voidcube_env_value(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "VoidCube_app.config.get_env_value",
        lambda key: "sk-deepseek-dotenv-token-123456789" if key == "DEEPSEEK_API_KEY" else "",
    )

    api_key = _resolve_mem_api_key(
        MemModelConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com/v1",
        )
    )

    assert api_key == "sk-deepseek-dotenv-token-123456789"


def test_resolve_mem_api_key_does_not_read_user_chat_provider(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("VoidCube_app.config.get_env_value", lambda _key: "")
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda _provider: None)
    monkeypatch.setattr(
        "VoidCube_app.provider_auth._load_auth_store",
        lambda: {"agnes-ai": {"api_key": "sk-agnes-user-chat-token-123456"}},
    )

    api_key = _resolve_mem_api_key(
        MemModelConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com/v1",
        )
    )

    assert api_key == ""


def test_resolve_mem_api_key_ignores_placeholder_env(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-your-key-here")
    monkeypatch.setattr(
        "VoidCube_app.provider_auth._load_auth_store",
        lambda: {"deepseek": {"api_key": "sk-real-deepseek-token-123456789"}},
    )

    api_key = _resolve_mem_api_key(
        MemModelConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com/v1",
        )
    )

    assert api_key == "sk-real-deepseek-token-123456789"
