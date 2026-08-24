from __future__ import annotations

from src.voidcube.extensions.tools import configuration as policy
from src.voidcube.extensions.tools.provider_configuration import (
    detect_active_provider_index,
    needs_configuration_prompt,
    visible_providers,
)
from src.voidcube.extensions.tools.token_estimation import ToolTokenEstimator
from src.voidcube.infrastructure.providers import credentials


def test_default_cli_toolsets_resolve_through_canonical_policy():
    enabled = policy.get_platform_tools({}, "cli", include_default_mcp_servers=False)

    assert "voidcube" in enabled
    assert "terminal" in enabled
    assert "file" in enabled


def test_explicit_config_does_not_reexpand_disabled_leaf_toolsets():
    enabled = policy.get_platform_tools(
        {"platform_toolsets": {"cli": ["terminal", "file"]}},
        "cli",
        include_default_mcp_servers=False,
    )

    assert enabled == {"terminal", "file"}


def test_no_mcp_sentinel_disables_global_mcp_defaults():
    enabled = policy.get_platform_tools(
        {
            "platform_toolsets": {"cli": ["terminal", "no_mcp"]},
            "mcp_servers": {"github": {"enabled": True}},
        },
        "cli",
    )

    assert "github" not in enabled
    assert "no_mcp" not in enabled


def test_save_platform_tools_preserves_mcp_entries(monkeypatch):
    saved = []
    monkeypatch.setattr(policy, "save_config", lambda config: saved.append(config))
    config = {"platform_toolsets": {"cli": ["github", "voidcube"]}}

    policy.save_platform_tools(config, "cli", {"terminal"})

    assert config["platform_toolsets"]["cli"] == ["github", "terminal"]
    assert saved == [config]


def test_apply_mcp_change_reports_unknown_servers():
    config = {"mcp_servers": {"github": {"tools": {"exclude": []}}}}

    missing = policy.apply_mcp_change(config, ["github:create_issue", "missing:tool"], "disable")

    assert missing == {"missing"}
    assert config["mcp_servers"]["github"]["tools"]["exclude"] == ["create_issue"]


def test_provider_configuration_policy_filters_and_detects_active_provider():
    class Feature:
        managed_by_nous = False

    class Features:
        nous_auth_present = True
        features = {"tts": Feature()}

    category = {
        "providers": [
            {"name": "managed", "requires_nous_auth": True, "managed_nous_feature": "tts"},
            {"name": "edge", "tts_provider": "edge", "env_vars": []},
        ]
    }
    providers = visible_providers(
        category,
        features=Features(),
        managed_tools_enabled=lambda: True,
    )

    assert [item["name"] for item in providers] == ["managed", "edge"]
    assert detect_active_provider_index(
        providers,
        {"tts": {"provider": "edge"}},
        features=Features(),
        get_env_value=lambda _name: "",
    ) == 1
    assert needs_configuration_prompt(
        "tts",
        {"tts": {"provider": "edge"}},
        categories={"tts": category},
        has_keys=lambda *_args: False,
    ) is False


def test_token_estimator_is_lazy_and_memoized():
    class Encoder:
        def encode(self, text):
            return list(text)

    class Registry:
        def get_all_tool_names(self):
            return ["demo"]

        def get_schema(self, name):
            return {"name": name}

    estimator = ToolTokenEstimator()
    first = estimator.estimate(encoding_factory=Encoder, registry=Registry())
    second = estimator.estimate(
        encoding_factory=lambda: (_ for _ in ()).throw(RuntimeError("not called")),
        registry=Registry(),
    )

    assert first == second
    assert first["demo"] > 0


def test_provider_credentials_prefer_configured_environment_value(monkeypatch):
    monkeypatch.setattr(
        credentials,
        "configured_env_value",
        lambda name: "sk-test-credential-value" if name == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(credentials, "load_auth_store", lambda: {})

    result = credentials.resolve_api_key_provider_credentials("openai")

    assert result["api_key"] == "sk-test-credential-value"
    assert result["base_url"] == "https://api.openai.com/v1"
