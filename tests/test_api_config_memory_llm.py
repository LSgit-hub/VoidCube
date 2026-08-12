from __future__ import annotations

import pytest

from VoidCube_cli.api_config import (
    api_a_key_configured,
    api_b_key_configured,
    api_config_summary,
    has_configured_api_key,
    persist_api_a_selection,
    persist_api_b_config,
    persist_image_generation_config,
    persist_video_generation_config,
    persist_provider_pool_entry,
    provider_credential_sources,
    provider_has_usable_credential,
    provider_pool_api_key,
    refresh_provider_pool_catalog,
    render_api_config_summary,
)
from VoidCube_cli.config import (
    _normalize_provider_runtime_config,
    get_active_model_config,
    set_provider_model,
)


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
        },
        {"deepseek": {"api_key_env": "DEEPSEEK_API_KEY", "auth_mode": "env"}},
    ) is True


def test_api_b_key_configured_uses_provider_default_key_env(monkeypatch):
    seen = []

    def fake_get_env_value(key):
        seen.append(key)
        return "sk-real-deepseek-token-123456789" if key == "DEEPSEEK_API_KEY" else ""

    monkeypatch.setattr("VoidCube_cli.config.get_env_value", fake_get_env_value)

    assert api_b_key_configured(
        {"provider": "deepseek"},
        {"deepseek": {"api_key_env": "DEEPSEEK_API_KEY", "auth_mode": "env"}},
    ) is True
    assert seen == ["DEEPSEEK_API_KEY"]


def test_api_b_key_configured_uses_shared_provider_stored_key():
    assert api_b_key_configured(
        {"provider": "deepseek"},
        {
            "deepseek": {
                "api_key": "sk-deepseek-provider-pool-token-123456",
                "auth_mode": "stored",
            }
        },
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


def test_persist_api_a_selection_does_not_touch_memory_llm():
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
                "auth_mode": "stored",
            },
            "deepseek": {
                "api_key_env": "DEEPSEEK_API_KEY",
                "auth_mode": "env",
            },
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

    cfg = persist_provider_pool_entry(
        cfg,
        provider_key="openrouter",
        label="OpenRouter",
        model_catalog=["deepseek/deepseek-chat"],
        provider_type="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        auth_mode="env",
    )
    updated = persist_api_a_selection(
        cfg,
        provider="openrouter",
        model="deepseek/deepseek-chat",
    )

    assert updated["runtime"]["active_provider"] == "openrouter"
    assert updated["providers"]["openrouter"]["selected_model"] == "deepseek/deepseek-chat"
    assert updated["memory"]["llm"]["provider"] == "deepseek"
    assert updated["memory"]["llm"]["model"] == "deepseek-v4-flash"


def test_persist_provider_pool_entry_normalizes_chat_completions_url():
    updated = persist_provider_pool_entry(
        {},
        provider_key="agnes-ai",
        label="Agnes-AI",
        model_catalog=["agnes-2.5-flash"],
        provider_type="custom",
        base_url="https://api.agnes-ai.cn/v1/chat/completions",
        api_key_env="OPENAI_API_KEY",
    )

    assert updated["providers"]["agnes-ai"]["base_url"] == "https://api.agnes-ai.cn/v1"


def test_refresh_provider_pool_catalog_uses_existing_env_key(monkeypatch):
    cfg = {
        "providers": {
            "agnes-ai": {
                "label": "Agnes-AI",
                "base_url": "https://api.agnes-ai.cn/v1",
                "api_key_env": "AGNES_API_KEY",
                "auth_mode": "env",
                "selected_model": "old-model",
            }
        }
    }
    monkeypatch.setattr(
        "VoidCube_app.config.get_env_value",
        lambda key: "sk-agnes-provider-token-123456" if key == "AGNES_API_KEY" else "",
    )
    seen = {}

    def fake_models(provider, *, api_key="", base_url=""):
        seen.update(provider=provider, api_key=api_key, base_url=base_url)
        return [("agnes-2.5-pro", ""), ("agnes-2.5-flash", "")]

    monkeypatch.setattr("VoidCube_cli.api_config.get_provider_models_from_api", fake_models)

    updated, models = refresh_provider_pool_catalog(cfg, "agnes-ai")

    assert models == ["agnes-2.5-pro", "agnes-2.5-flash"]
    assert seen == {
        "provider": "agnes-ai",
        "api_key": "sk-agnes-provider-token-123456",
        "base_url": "https://api.agnes-ai.cn/v1",
    }
    assert updated["providers"]["agnes-ai"]["selected_model"] == "agnes-2.5-pro"
    assert updated["providers"]["agnes-ai"]["model_catalog"]["models"] == models


def test_provider_pool_api_key_accepts_stored_pool_key():
    assert provider_pool_api_key(
        {"api_key": "sk-stored-provider-token-123456", "auth_mode": "stored"}
    ) == "sk-stored-provider-token-123456"


def test_persist_api_b_config_does_not_touch_api_a_provider():
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {
                "label": "agnes-ai",
                "selected_model": "agnes-2.0-flash",
                "api_key": "sk-agnes-user-chat-token-123456",
                "auth_mode": "stored",
            },
            "deepseek": {
                "api_key_env": "DEEPSEEK_API_KEY",
                "auth_mode": "env",
            },
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
    assert updated["memory"]["llm"] == {"provider": "deepseek", "model": "deepseek-v4-flash"}


def test_persist_custom_api_b_config_is_isolated_and_normalizes_url():
    cfg = {
        "runtime": {"active_provider": "agnes-ai"},
        "providers": {
            "agnes-ai": {"selected_model": "agnes-2.5-flash"},
            "custom": {
                "base_url": "https://memory.example/v1",
                "api_key_env": "VOIDCUBE_MEMORY_CUSTOM_API_KEY",
                "auth_mode": "env",
            },
        },
        "memory": {"provider": "mem", "llm": {}},
    }

    updated = persist_api_b_config(
        cfg,
        provider="custom",
        model="memory-reasoner",
    )

    assert updated["runtime"] == cfg["runtime"]
    assert updated["providers"] == cfg["providers"]
    assert updated["memory"]["llm"] == {"provider": "custom", "model": "memory-reasoner"}


def test_image_and_video_generation_config_updates_are_isolated():
    original = {
        "runtime": {"active_provider": "openrouter"},
        "providers": {"openrouter": {"selected_model": "chat-model"}},
        "memory": {"llm": {"provider": "deepseek", "model": "memory-model"}},
        "multimodal": {"base_url": "https://legacy.example/v1"},
        "video_generation": {
            "endpoint": "https://video.example/v1/videos",
            "result_endpoint": "https://video.example/result",
            "model": "existing-video-model",
        },
    }

    with_image = persist_image_generation_config(
        original,
        endpoint="https://image.example/v1/images/generations/",
        model="new-image-model",
    )
    with_video = persist_video_generation_config(
        with_image,
        endpoint="https://new-video.example/v1/videos/",
        result_endpoint="https://new-video.example/result/",
        model="new-video-model",
    )

    assert "multimodal" not in with_image
    assert with_image["video_generation"] == original["video_generation"]
    assert with_image["image_generation"]["endpoint"] == (
        "https://image.example/v1/images/generations"
    )
    assert with_video["image_generation"] == with_image["image_generation"]
    assert with_video["video_generation"]["endpoint"] == (
        "https://new-video.example/v1/videos"
    )
    assert with_video["video_generation"]["result_endpoint"] == (
        "https://new-video.example/result"
    )
    assert with_video["runtime"] == original["runtime"]
    assert with_video["providers"] == original["providers"]
    assert with_video["memory"] == original["memory"]


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
            }
        },
        "model": {"api_key": "sk-old-model-token-123456"},
        "custom_providers": [{"api_key": "sk-old-custom-token-123456"}],
        "image_generation": {
            "provider": "agnes-ai",
            "api_key_env": "AGNES_API_KEY",
            "endpoint": "https://image.example/v1/images/generations",
            "model": "image-model",
            "api_key": "sk-image-secret-token-123456",
        },
        "video_generation": {
            "provider": "agnes-ai",
            "api_key_env": "AGNES_API_KEY",
            "endpoint": "https://video.example/v1/videos",
            "result_endpoint": "https://video.example/result",
            "model": "video-model",
            "api_key": "sk-video-secret-token-123456",
        },
    }

    summary = api_config_summary(cfg)
    rendered = "\n".join(render_api_config_summary(cfg))
    combined = f"{summary}\n{rendered}"

    assert "sk-" not in combined
    assert summary["api_a"]["provider"] == "agnes-ai"
    assert summary["api_b"]["provider"] == "deepseek"
    assert summary["api_b"]["api_key_env"] != "DEEPSEEK_API_KEY"
    assert summary["image_generation"]["model"] == "image-model"
    assert summary["video_generation"]["model"] == "video-model"
    assert "language_model" not in combined
    assert "multimodal" not in summary
    assert summary["retired_fields_present"] == ["model", "custom_providers"]


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
