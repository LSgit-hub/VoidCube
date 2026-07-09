from __future__ import annotations

from VoidCube_cli.api_config import memory_llm_provider_defaults


def test_memory_llm_provider_defaults_uses_builtin_provider_fields():
    defaults = memory_llm_provider_defaults("deepseek", {})

    assert defaults["api_key_env"] == "DEEPSEEK_API_KEY"
    assert defaults["base_url"] == "https://api.deepseek.com/v1"
    assert defaults["provider_profile"] == "openai"


def test_memory_llm_provider_defaults_does_not_read_user_chat_provider_entries():
    defaults = memory_llm_provider_defaults(
        "agnes-ai",
        {
            "providers": {
                "agnes-ai": {
                    "label": "Agnes AI",
                    "base_url": "https://apihub.agnes-ai.com/v1",
                    "api_key_env": "AGNES_AI_API_KEY",
                    "provider_profile": "openai",
                }
            }
        },
    )

    assert defaults["api_key_env"] == ""
    assert defaults["base_url"] == ""
    assert defaults["provider_profile"] == "openai"
