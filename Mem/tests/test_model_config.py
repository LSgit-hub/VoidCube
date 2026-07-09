from __future__ import annotations

from memai import MEM_MODEL_ROLES, MemModelConfig, MemModelConfigSet
from memai.model_config import resolve_mem_llm_client


def test_mem_model_config_reads_new_voidcube_cli_memory_llm_block() -> None:
    config = {
        "memory": {
            "provider": "mem",
            "llm": {
                "provider": "openrouter",
                "model": "anthropic/claude-3.5-haiku",
                "api_key_env": "OPENROUTER_API_KEY",
                "base_url": "https://openrouter.ai/api/v1",
                "provider_profile": "openai",
                "response_content_style": "openai_message",
            },
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openrouter"
    assert model_config.model == "anthropic/claude-3.5-haiku"
    assert model_config.api_key_env == "OPENROUTER_API_KEY"
    assert model_config.base_url == "https://openrouter.ai/api/v1"
    assert model_config.response_content_style == "openai_message"


def test_mem_model_config_keeps_memory_provider_as_plugin_identity() -> None:
    config = {"memory": {"provider": "mem"}}

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openai"
    assert model_config.model is None
    assert model_config.api_key_env == "OPENAI_API_KEY"


def test_mem_model_config_reads_legacy_memory_model_without_stealing_plugin_provider() -> None:
    config = {
        "memory": {
            "provider": "mem",
            "model": "gpt-4o-mini",
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openai"
    assert model_config.model == "gpt-4o-mini"


def test_mem_model_config_reads_legacy_memory_provider_when_it_was_llm_provider() -> None:
    config = {
        "memory": {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
        }
    }

    model_config = MemModelConfig.from_voidcube_config(config)

    assert model_config.provider == "openrouter"
    assert model_config.model == "google/gemini-2.5-flash"
    assert model_config.api_key_env == "OPENROUTER_API_KEY"
    assert model_config.base_url == "https://openrouter.ai/api/v1"


def test_mem_model_config_cli_args_override_saved_config() -> None:
    class Args:
        model = "override-model"
        api_key_env = "OVERRIDE_API_KEY"
        base_url = "https://override.example/v1"
        provider_profile = "legacy-compatible"
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
    assert model_config.provider_profile == "legacy-compatible"
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
    assert config_set.for_role("embedding") == config_set.default
    assert "unknown_role" not in config_set.roles


def test_mem_model_roles_include_expected_governance_roles() -> None:
    assert "extraction" in MEM_MODEL_ROLES
    assert "governance_summary" in MEM_MODEL_ROLES
    assert "governance_reasoner" in MEM_MODEL_ROLES
    assert "embedding" in MEM_MODEL_ROLES


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
