from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
import yaml

from VoidCube_cli.env_loader import is_placeholder_secret, load_VoidCube_dotenv
from VoidCube_app.provider_auth import get_auth_status, read_credential_pool, write_credential_pool


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("sk-your-key-here", True),
        ('"YOUR_API_KEY"', True),
        ("sk-real-provider-token-123456789", False),
        ("", False),
    ),
)
def test_placeholder_secret_detection(value, expected):
    assert is_placeholder_secret(value) is expected


def test_load_config_normalizes_required_mapping_sections(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / "config.yaml").write_text(
        """runtime: invalid
providers: []
agent: invalid
display: false
terminal: null
checkpoints: disabled
compression: []
delegation: false
auxiliary: invalid
clarify: invalid
max_turns: 17
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    from VoidCube_cli.config import load_config

    config = load_config()

    for section in (
        "runtime",
        "providers",
        "agent",
        "display",
        "terminal",
        "checkpoints",
        "compression",
        "delegation",
        "auxiliary",
        "clarify",
    ):
        assert isinstance(config[section], dict)
    assert config["agent"]["max_turns"] == 17
    assert config["clarify"]["timeout"] == 120


def test_retired_display_settings_migrate_once_to_platforms(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    retired_key = "tool_progress_" + "overrides"
    unused_key = "tool_progress_" + "command"
    (home / "config.yaml").write_text(
        f"""_config_version: 21
display:
  skin: custom-theme
  future_display_option: retained
  {unused_key}: true
  {retired_key}:
    cli: all
  platforms:
    cli:
      tool_progress: 'off'
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    from VoidCube_cli.config import load_config, migrate_config

    loaded = load_config()

    assert retired_key not in loaded["display"]
    assert unused_key not in loaded["display"]
    assert "skin" not in loaded["display"]
    assert loaded["display"]["future_display_option"] == "retained"
    assert loaded["display"]["platforms"]["cli"]["tool_progress"] == "off"

    first_result = migrate_config(interactive=False, quiet=True)

    saved_text = (home / "config.yaml").read_text(encoding="utf-8")
    saved = yaml.safe_load(saved_text)
    assert retired_key not in saved["display"]
    assert unused_key not in saved["display"]
    assert "skin" not in saved["display"]
    assert saved["display"]["future_display_option"] == "retained"
    assert saved["display"]["platforms"] == loaded["display"]["platforms"]
    assert any("retired display overrides" in item for item in first_result["config_added"])
    assert "removed unused display progress command flag" in first_result["config_added"]

    second_result = migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == saved_text
    assert not any(
        "retired display overrides" in item
        for item in second_result["config_added"]
    )
    assert "removed unused display progress command flag" not in second_result["config_added"]


def test_retired_tool_progress_env_migrates_once_on_current_config(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    enabled_key = "VOIDCUBE_TOOL_" + "PROGRESS"
    mode_key = "VOIDCUBE_TOOL_" + "PROGRESS_MODE"
    (home / "config.yaml").write_text("_config_version: 21\n", encoding="utf-8")
    (home / ".env").write_text(
        f"{enabled_key}=false\n"
        f"{mode_key}=verbose\n"
        "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv(enabled_key, raising=False)
    monkeypatch.delenv(mode_key, raising=False)

    import VoidCube_cli.config as config_module

    first_result = config_module.migrate_config(interactive=False, quiet=True)

    saved_text = (home / "config.yaml").read_text(encoding="utf-8")
    saved = yaml.safe_load(saved_text)
    assert saved["display"]["tool_progress"] == "off"
    assert "display.tool_progress=off" in first_result["config_added"][0]
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert enabled_key not in env_text
    assert mode_key not in env_text
    assert "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789" in env_text

    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda config: pytest.fail("second migration rewrote config.yaml"),
    )
    second_result = config_module.migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == saved_text
    assert not any(
        "retired environment setting" in item
        for item in second_result["config_added"]
    )


def test_retired_tool_progress_env_does_not_override_explicit_config(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    enabled_key = "VOIDCUBE_TOOL_" + "PROGRESS"
    mode_key = "VOIDCUBE_TOOL_" + "PROGRESS_MODE"
    original_config = "_config_version: 21\ndisplay:\n  tool_progress: new\n"
    (home / "config.yaml").write_text(original_config, encoding="utf-8")
    (home / ".env").write_text(
        f"{enabled_key}=false\n{mode_key}=verbose\nKEEP_ME=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv(enabled_key, raising=False)
    monkeypatch.delenv(mode_key, raising=False)

    import VoidCube_cli.config as config_module

    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda config: pytest.fail("explicit tool progress should not be rewritten"),
    )
    result = config_module.migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original_config
    assert (home / ".env").read_text(encoding="utf-8") == "KEEP_ME=value\n"
    assert not any("retired environment setting" in item for item in result["config_added"])


@pytest.mark.parametrize(
    ("enabled", "mode", "expected"),
    (
        ("false", "verbose", "off"),
        ("true", "verbose", "verbose"),
        ("yes", "new", "new"),
        ("1", "all", "all"),
        ("true", "unexpected", "all"),
    ),
)
def test_retired_tool_progress_env_mode_mapping(
    tmp_path,
    monkeypatch,
    enabled,
    mode,
    expected,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    enabled_key = "VOIDCUBE_TOOL_" + "PROGRESS"
    mode_key = "VOIDCUBE_TOOL_" + "PROGRESS_MODE"
    (home / "config.yaml").write_text("_config_version: 21\n", encoding="utf-8")
    (home / ".env").write_text(
        f"{enabled_key}={enabled}\n{mode_key}={mode}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv(enabled_key, raising=False)
    monkeypatch.delenv(mode_key, raising=False)

    from VoidCube_cli.config import migrate_config

    migrate_config(interactive=False, quiet=True)

    saved = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert saved["display"]["tool_progress"] == expected


def test_retired_tool_progress_env_is_not_configurable():
    from VoidCube_cli.config import OPTIONAL_ENV_VARS

    retired_names = {
        "VOIDCUBE_TOOL_" + "PROGRESS",
        "VOIDCUBE_TOOL_" + "PROGRESS_MODE",
    }
    assert retired_names.isdisjoint(OPTIONAL_ENV_VARS)


def test_retired_messaging_env_is_removed_without_rewriting_config(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    original_config = "_config_version: 21\n"
    (home / "config.yaml").write_text(original_config, encoding="utf-8")
    retired_values = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_KEY": "legacy-key",
        "WEBHOOK_SECRET": "webhook-secret",
        "MESSAGING_CWD": "/old/workspace",
    }
    (home / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in retired_values.items())
        + "KEEP_ME=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    for key in retired_values:
        monkeypatch.delenv(key, raising=False)

    import VoidCube_cli.config as config_module

    assert set(config_module._RETIRED_MESSAGING_ENV_VARS).isdisjoint(
        config_module.OPTIONAL_ENV_VARS
    )
    assert all(
        metadata.get("category") != "messaging"
        for metadata in config_module.OPTIONAL_ENV_VARS.values()
    )
    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda config: pytest.fail("retired env cleanup rewrote config.yaml"),
    )

    config_module.migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original_config
    assert (home / ".env").read_text(encoding="utf-8") == "KEEP_ME=value\n"
    assert all(key not in os.environ for key in retired_values)


def test_unused_optional_env_registrations_are_removed(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    original_config = "_config_version: 21\n"
    (home / "config.yaml").write_text(original_config, encoding="utf-8")
    (home / ".env").write_text(
        "GEMINI_BASE_URL=https://unused.example/v1\n"
        "DASHSCOPE_API_KEY=unused-secret\n"
        "OPENCODE_ZEN_API_KEY=unused-secret\n"
        "OPENCODE_GO_API_KEY=unused-secret\n"
        "HF_TOKEN=unused-secret\n"
        "XIAOMI_API_KEY=unused-secret\n"
        "KEEP_ME=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    import VoidCube_cli.config as config_module

    retired_names = set(config_module._RETIRED_UNUSED_CONFIG_ENV_VARS)
    for key in retired_names:
        monkeypatch.delenv(key, raising=False)
    assert retired_names.isdisjoint(config_module.OPTIONAL_ENV_VARS)
    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda config: pytest.fail("retired env cleanup rewrote config.yaml"),
    )

    config_module.migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original_config
    assert (home / ".env").read_text(encoding="utf-8") == "KEEP_ME=value\n"
    assert all(key not in os.environ for key in retired_names)


def test_tool_config_platforms_ignore_retired_messaging_env(monkeypatch):
    monkeypatch.setenv("API_SERVER_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_SECRET", "legacy-secret")

    from VoidCube_cli.tools_config import _get_enabled_platforms

    assert _get_enabled_platforms() == ["cli"]


def test_retired_prefill_env_migrates_once_to_agent_config(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    retired_key = "VOIDCUBE_PREFILL_" + "MESSAGES_FILE"
    (home / "config.yaml").write_text("_config_version: 21\n", encoding="utf-8")
    (home / ".env").write_text(
        f"{retired_key}=C:/prompts/prefill.json\nKEEP_ME=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv(retired_key, raising=False)

    import VoidCube_cli.config as config_module

    assert retired_key not in config_module.OPTIONAL_ENV_VARS
    first_result = config_module.migrate_config(interactive=False, quiet=True)

    saved_text = (home / "config.yaml").read_text(encoding="utf-8")
    saved = yaml.safe_load(saved_text)
    assert saved["agent"]["prefill_messages_file"] == "C:/prompts/prefill.json"
    assert (home / ".env").read_text(encoding="utf-8") == "KEEP_ME=value\n"
    assert any(
        "agent.prefill_messages_file" in item
        for item in first_result["config_added"]
    )

    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda config: pytest.fail("second prefill migration rewrote config.yaml"),
    )
    second_result = config_module.migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == saved_text
    assert not any(
        "agent.prefill_messages_file" in item
        for item in second_result["config_added"]
    )


def test_retired_prefill_env_does_not_override_explicit_config(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    retired_key = "VOIDCUBE_PREFILL_" + "MESSAGES_FILE"
    original_config = (
        "_config_version: 21\n"
        "agent:\n"
        "  prefill_messages_file: C:/prompts/current.json\n"
    )
    (home / "config.yaml").write_text(original_config, encoding="utf-8")
    (home / ".env").write_text(
        f"{retired_key}=C:/prompts/old.json\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv(retired_key, raising=False)

    import VoidCube_cli.config as config_module

    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda config: pytest.fail("explicit prefill config should not be rewritten"),
    )
    config_module.migrate_config(interactive=False, quiet=True)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original_config
    assert (home / ".env").read_text(encoding="utf-8") == ""


def test_voidcube_dotenv_placeholder_does_not_override_real_env(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / ".env").write_text("DEEPSEEK_API_KEY=sk-your-key-here\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek-token-123456789")

    load_VoidCube_dotenv(VoidCube_home=home, force_reload=True)

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-real-deepseek-token-123456789"


def test_save_env_value_updates_current_process_env(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    from VoidCube_cli.config import save_env_value

    save_env_value("DEEPSEEK_API_KEY", "sk-real-deepseek-token-123456789")

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-real-deepseek-token-123456789"
    assert "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789" in (home / ".env").read_text(
        encoding="utf-8"
    )


def test_migrate_config_removes_retired_model_env_vars(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / "config.yaml").write_text("_config_version: 18\n", encoding="utf-8")
    (home / ".env").write_text(
        "LLM_MODEL=old-model\n"
        "LLM_BASE_URL=https://old.example/v1\n"
        "OPENAI_MODEL=old-openai-model\n"
        "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    from VoidCube_cli.config import migrate_config

    migrate_config(interactive=False, quiet=True)

    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "LLM_MODEL=" not in env_text
    assert "LLM_BASE_URL=" not in env_text
    assert "OPENAI_MODEL=" not in env_text
    assert "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789" in env_text


def test_migrate_config_consolidates_auxiliary_routes_and_removes_old_sources(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / "config.yaml").write_text(
        """_config_version: 18
compression:
  enabled: true
  summary_provider: deepseek
  summary_model: summary-model
  summary_base_url: https://summary.example/v1
auxiliary:
  vision:
    provider: auto
    model: ''
    base_url: ''
    api_key: old-config-secret
""",
        encoding="utf-8",
    )
    (home / ".env").write_text(
        "AUXILIARY_WEB_EXTRACT_PROVIDER=openrouter\n"
        "AUXILIARY_WEB_EXTRACT_MODEL=web-model\n"
        "AUXILIARY_VISION_API_KEY=old-env-secret\n"
        "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    for name in (
        "AUXILIARY_WEB_EXTRACT_PROVIDER",
        "AUXILIARY_WEB_EXTRACT_MODEL",
        "AUXILIARY_VISION_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    from VoidCube_cli.config import migrate_config

    migrate_config(interactive=False, quiet=True)

    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["_config_version"] == 21
    assert config["compression"]["enabled"] is True
    assert not {
        "summary_provider",
        "summary_model",
        "summary_base_url",
    } & config["compression"].keys()
    compression_route = config["auxiliary"]["compression"]
    assert compression_route["provider"] == "deepseek"
    assert compression_route["model"] == "summary-model"
    assert compression_route["base_url"] == "https://summary.example/v1"
    assert config["auxiliary"]["web_extract"]["provider"] == "openrouter"
    assert config["auxiliary"]["web_extract"]["model"] == "web-model"
    assert config["auxiliary"]["vision"]["api_key"] == "old-config-secret"

    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "AUXILIARY_" not in env_text
    assert "old-env-secret" not in env_text
    assert "DEEPSEEK_API_KEY=sk-real-deepseek-token-123456789" in env_text


def test_migrate_config_moves_legacy_cache_directories_to_v20_layout(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / "config.yaml").write_text("_config_version: 19\n", encoding="utf-8")
    legacy_dirs = {
        "document_cache": "documents",
        "image_cache": "images",
        "audio_cache": "audio",
        "browser_screenshots": "screenshots",
    }
    for legacy_name in legacy_dirs:
        legacy_dir = home / legacy_name
        legacy_dir.mkdir()
        (legacy_dir / "cached.bin").write_text(legacy_name, encoding="utf-8")
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    from VoidCube_cli.config import migrate_config

    migrate_config(interactive=False, quiet=True)

    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["_config_version"] == 21
    for legacy_name, canonical_name in legacy_dirs.items():
        assert not (home / legacy_name).exists()
        assert (home / "cache" / canonical_name / "cached.bin").read_text(
            encoding="utf-8"
        ) == legacy_name


def test_migrate_config_preserves_canonical_and_conflicting_legacy_cache_files(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".VoidCube"
    canonical = home / "cache" / "images"
    legacy = home / "image_cache"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    (home / "config.yaml").write_text("_config_version: 19\n", encoding="utf-8")
    (canonical / "same.png").write_text("canonical", encoding="utf-8")
    (canonical / "same.png.legacy-1").write_text("reserved", encoding="utf-8")
    (legacy / "same.png").write_text("legacy", encoding="utf-8")
    (legacy / "only-old.png").write_text("only-old", encoding="utf-8")
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    from VoidCube_cli.config import migrate_config

    migrate_config(interactive=False, quiet=True)

    assert not legacy.exists()
    assert (canonical / "same.png").read_text(encoding="utf-8") == "canonical"
    assert (canonical / "same.png.legacy-1").read_text(encoding="utf-8") == "reserved"
    assert (canonical / "same.png.legacy-2").read_text(encoding="utf-8") == "legacy"
    assert (canonical / "only-old.png").read_text(encoding="utf-8") == "only-old"


def test_credential_pool_round_trips_by_provider(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth_store.json"
    monkeypatch.setattr("VoidCube_app.provider_auth._get_auth_store_path", lambda: auth_path)

    write_credential_pool(
        "deepseek",
        [
            {
                "source": "manual:test",
                "auth_type": "api_key",
                "access_token": "sk-deepseek-pool-token-123456789",
            }
        ],
    )

    assert read_credential_pool("deepseek") == [
        {
            "source": "manual:test",
            "auth_type": "api_key",
            "access_token": "sk-deepseek-pool-token-123456789",
        }
    ]
    saved = json.loads(auth_path.read_text(encoding="utf-8"))
    assert "deepseek" in saved["credential_pool"]


def test_custom_provider_pool_key_uses_providers_map(monkeypatch):
    from agent.credential_pool import get_custom_provider_pool_key

    monkeypatch.setattr(
        "agent.credential_pool._load_config_safe",
        lambda: {
            "runtime": {"active_provider": "my-endpoint"},
            "providers": {
                "my-endpoint": {
                    "label": "My Endpoint",
                    "type": "openai_compatible",
                    "base_url": "https://models.example/v1",
                    "selected_model": "my-model",
                    "api_key": "sk-custom-token-123456789",
                }
            },
        },
    )

    assert get_custom_provider_pool_key("https://models.example/v1") == "custom:my-endpoint"


def test_auth_status_rejects_placeholder_provider_key(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth_store.json"
    monkeypatch.setattr("VoidCube_app.provider_auth._get_auth_store_path", lambda: auth_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-your-key-here")

    status = get_auth_status("deepseek")

    assert status["authenticated"] is False
    assert status["configured"] is False


def test_login_api_key_save_failure_does_not_echo_secret(monkeypatch, capsys):
    from VoidCube_cli.auth import _login_api_key

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "sk-real-secret-token-123456789")
    monkeypatch.setattr(
        "VoidCube_cli.config.save_env_value",
        lambda key, value: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    monkeypatch.setattr("VoidCube_cli.config.get_env_path", lambda: "C:/Users/test/.VoidCube/.env")

    _login_api_key("deepseek", SimpleNamespace())

    output = capsys.readouterr().out
    assert "sk-real-secret-token-123456789" not in output
    assert "DEEPSEEK_API_KEY=<redacted>" in output
