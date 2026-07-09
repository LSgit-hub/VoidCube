from __future__ import annotations

import json
import os
from types import SimpleNamespace

from VoidCube_cli.env_loader import load_VoidCube_dotenv
from VoidCube_cli.auth import get_auth_status, read_credential_pool, write_credential_pool


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


def test_credential_pool_round_trips_by_provider(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth_store.json"
    monkeypatch.setattr("VoidCube_cli.auth._get_auth_store_path", lambda: auth_path)

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
    monkeypatch.setattr("VoidCube_cli.auth._get_auth_store_path", lambda: auth_path)
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
