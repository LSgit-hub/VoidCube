from __future__ import annotations

from VoidCube_cli.api_config import (
    api_a_key_configured,
    api_b_key_configured,
    api_config_summary,
    has_configured_api_key,
    memory_llm_provider_defaults,
    memory_llm_provider_options,
    persist_api_a_config,
    persist_api_b_config,
    provider_credential_sources,
    provider_has_usable_credential,
    render_api_config_summary,
)
from VoidCube_cli.config import (
    _normalize_provider_runtime_config,
    get_active_model_config,
    set_provider_model,
)


def test_memory_llm_provider_defaults_uses_builtin_provider_fields():
    defaults = memory_llm_provider_defaults("deepseek")

    assert defaults["api_key_env"] == "DEEPSEEK_API_KEY"
    assert defaults["base_url"] == "https://api.deepseek.com/v1"
    assert defaults["provider_profile"] == "openai"


def test_memory_llm_provider_defaults_does_not_read_user_chat_provider_entries():
    defaults = memory_llm_provider_defaults("agnes-ai")

    assert defaults["api_key_env"] == ""
    assert defaults["base_url"] == ""
    assert defaults["provider_profile"] == "openai"


def test_has_configured_api_key_rejects_template_placeholder(monkeypatch):
    monkeypatch.setattr("VoidCube_cli.config.get_env_value", lambda key: "sk-your-key-here")

    assert has_configured_api_key("DEEPSEEK_API_KEY") is False


def test_has_configured_api_key_accepts_real_key(monkeypatch):
    monkeypatch.setattr("VoidCube_cli.config.get_env_value", lambda key: "sk-real-deepseek-token-123456789")

    assert has_configured_api_key("DEEPSEEK_API_KEY") is True


def test_api_a_key_configured_accepts_stored_user_provider_key():
    assert api_a_key_configured(
        {
            "auth_mode": "stored",
            "api_key": "sk-agnes-user-chat-token-123456",
        }
    ) is True


def test_api_b_key_configured_uses_memory_llm_key_env(monkeypatch):
    monkeypatch.setattr("VoidCube_cli.config.get_env_value", lambda key: "sk-real-deepseek-token-123456789")

    assert api_b_key_configured(
        {
            "provider": "deepseek",
            "api_key_env": "DEEPSEEK_API_KEY",
        }
    ) is True


def test_api_b_key_configured_uses_provider_default_key_env(monkeypatch):
    seen = []

    def fake_get_env_value(key):
        seen.append(key)
        return "sk-real-deepseek-token-123456789" if key == "DEEPSEEK_API_KEY" else ""

    monkeypatch.setattr("VoidCube_cli.config.get_env_value", fake_get_env_value)

    assert api_b_key_configured({"provider": "deepseek"}) is True
    assert seen == ["DEEPSEEK_API_KEY"]


def test_api_b_key_configured_uses_matching_provider_auth_store(monkeypatch):
    monkeypatch.setattr("VoidCube_cli.config.get_env_value", lambda key: "")
    monkeypatch.setattr(
        "VoidCube_app.provider_auth.resolve_api_key_provider_credentials",
        lambda provider: {"api_key": "sk-deepseek-auth-store-token-123456"},
    )

    assert api_b_key_configured(
        {
            "provider": "deepseek",
            "api_key_env": "DEEPSEEK_API_KEY",
        }
    ) is True


def test_provider_has_usable_credential_uses_matching_pool(monkeypatch):
    class Entry:
        runtime_api_key = "sk-deepseek-pool-token-123456789"
        access_token = ""
        api_key = ""

    class Pool:
        def has_credentials(self):
            return True

        def select(self):
            return Entry()

    monkeypatch.setattr("VoidCube_cli.config.get_env_value", lambda key: "")
    monkeypatch.setattr(
        "VoidCube_app.provider_auth.resolve_api_key_provider_credentials",
        lambda provider: {"api_key": "", "access_token": ""},
    )
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda provider: Pool())

    assert provider_has_usable_credential("deepseek", "DEEPSEEK_API_KEY") is True


def test_provider_credential_sources_reports_voidcube_env_without_secret(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / ".env").write_text(
        "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("VoidCube_app.provider_auth._get_auth_store_path", lambda: home / "auth_store.json")

    sources = provider_credential_sources("deepseek", "DEEPSEEK_API_KEY")
    rendered = "\n".join(f"{item['source']} {item['status']} {item['detail']}" for item in sources)

    assert any(
        item["source"] == "voidcube_env" and item["status"] == "usable"
        for item in sources
    )
    assert "sk-real" not in rendered


def test_persist_api_a_config_does_not_touch_memory_llm():
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
                "auth_mode": "stored",
            }
        },
        "memory": {
            "provider": "mem",
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url": "https://api.deepseek.com/v1",
            },
        },
    }

    updated = persist_api_a_config(
        cfg,
        provider_key="openrouter",
        label="OpenRouter",
        selected_model="deepseek/deepseek-chat",
        provider_type="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        auth_mode="env",
    )

    assert updated["runtime"]["active_provider"] == "openrouter"
    assert updated["providers"]["openrouter"]["selected_model"] == "deepseek/deepseek-chat"
    assert updated["memory"]["llm"]["provider"] == "deepseek"
    assert updated["memory"]["llm"]["model"] == "deepseek-v4-flash"


def test_persist_api_b_config_does_not_touch_api_a_provider():
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
                "auth_mode": "stored",
            }
        },
        "memory": {"provider": "mem", "llm": {}},
    }

    updated = persist_api_b_config(
        cfg,
        provider="deepseek",
        model="deepseek-v4-flash",
    )

    assert updated["runtime"]["active_provider"] == "agnes-ai"
    assert updated["providers"]["agnes-ai"]["selected_model"] == "agnes-2.0-flash"
    assert updated["memory"]["llm"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "provider_profile": "openai",
    }


def test_api_config_summary_redacts_secret_values(monkeypatch):
    monkeypatch.setattr("VoidCube_cli.config.get_env_value", lambda key: "")
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
                "auth_mode": "stored",
            }
        },
        "memory": {
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url": "https://api.deepseek.com/v1",
            }
        },
        "model": {"api_key": "sk-old-model-token-123456"},
        "custom_providers": [{"api_key": "sk-old-custom-token-123456"}],
    }

    summary = api_config_summary(cfg)
    rendered = "\n".join(render_api_config_summary(cfg))
    combined = f"{summary}\n{rendered}"

    assert "sk-" not in combined
    assert summary["api_a"]["provider"] == "agnes-ai"
    assert summary["api_b"]["provider"] == "deepseek"
    assert summary["api_b"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert summary["retired_fields_present"] == ["model", "custom_providers"]


def test_memory_llm_provider_options_only_lists_supported_mem_providers():
    provider_ids = [provider for provider, _ in memory_llm_provider_options()]

    assert provider_ids == ["openrouter", "deepseek", "openai", "ollama"]


def test_normalized_runtime_config_drops_old_model_mirror_without_migration():
    cfg = {
        "runtime": {},
        "providers": {},
        "model": {
            "provider": "agnes-ai",
            "default": "agnes-2.0-flash",
            "base_url": "https://apihub.agnes-ai.com/v1",
            "api_key": "sk-agnes-user-chat-token-123456",
        },
    }

    normalized = _normalize_provider_runtime_config(cfg)

    assert "model" not in normalized
    assert normalized["runtime"]["active_provider"] == ""
    assert "agnes-ai" not in normalized["providers"]
    assert get_active_model_config(normalized) == {}


def test_provider_model_update_does_not_recreate_old_model_mirror():
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
            }
        },
    }

    updated = set_provider_model(cfg, "agnes-ai", "agnes-2.1-flash")

    assert "model" not in updated
    assert updated["providers"]["agnes-ai"]["selected_model"] == "agnes-2.1-flash"


def test_normalized_runtime_config_drops_retired_custom_providers_schema():
    cfg = {
        "runtime": {"active_provider": ""},
        "custom_providers": [
            {
                "name": "Old Endpoint",
                "base_url": "https://old.example/v1",
                "model": "old-model",
                "api_key": "sk-old-token-123456789",
            }
        ],
    }

    normalized = _normalize_provider_runtime_config(cfg)

    assert "custom_providers" not in normalized
    assert normalized["providers"] == {}
