"""Interactive Provider pool management flows behind ``/api``."""

from __future__ import annotations

import pytest

from voidcube.interfaces.cli.configuration import (
    ProviderPromptPorts,
    add_provider_entry,
    delete_provider_entry,
    edit_provider_entry,
    manage_provider_pool,
)


pytestmark = pytest.mark.cli


class _Prompt:
    """Scripted terminal double that records everything the flow emits."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.output: list[str] = []
        self.headers: list[str] = []
        self.secrets: list[str] = []

    def prompt(self, message="", default=""):
        self.output.append(message)
        if self._answers:
            return self._answers.pop(0)
        return default

    def secret_prompt(self, message="", default=""):
        self.output.append(message)
        self.secrets.append(message)
        if self._answers:
            return self._answers.pop(0)
        return default

    def emit(self, text):
        self.output.append(text)

    def header(self, title):
        self.headers.append(title)

    def ports(self):
        return ProviderPromptPorts(
            prompt=self.prompt,
            secret_prompt=self.secret_prompt,
            emit=self.emit,
            header=self.header,
        )


@pytest.fixture
def pool(monkeypatch):
    """Redirect provider persistence to an in-memory config."""
    from voidcube.infrastructure.config import configuration as config_module
    from voidcube.infrastructure.config import provider_config
    from voidcube.interfaces.cli import configuration as cli_config

    state: dict = {"providers": {}, "runtime": {"active_provider": ""}}

    def commit(updated):
        state.clear()
        state.update(updated)
        return True

    monkeypatch.setattr(cli_config, "load_current_config", lambda: dict(state))
    monkeypatch.setattr(
        cli_config,
        "save_provider_pool_entry",
        lambda key, **kwargs: commit(
            provider_config.persist_provider_pool_entry(
                state, provider_key=key, **kwargs
            )
        ),
    )
    monkeypatch.setattr(cli_config, "save_env_value", lambda key, value: True)
    monkeypatch.setattr(
        cli_config,
        "save_remove_provider_pool_entry",
        lambda key: commit(
            provider_config.remove_provider_pool_entry(state, provider_key=key)
        ),
    )
    monkeypatch.setattr(config_module, "save_config", commit)
    monkeypatch.setattr(
        cli_config,
        "provider_has_usable_credential",
        lambda provider, api_key_env="": True,
    )
    return state


def _seed(state, key="step5", **overrides):
    entry = {
        "label": "opencode",
        "type": "openai_compatible",
        "base_url": "https://opencode.ai/zen/go/v1",
        "selected_model": "step-5-preview-free",
        "api_key_env": "VOIDCUBE_PROVIDER_STEP5_API_KEY",
        "auth_mode": "env",
        "model_catalog": {"models": ["step-5-preview-free", "glm-5.3"]},
    }
    entry.update(overrides)
    state["providers"][key] = entry
    return entry


def test_add_provider_entry_saves_key_catalog_and_selected_model(pool, monkeypatch):
    monkeypatch.setattr(
        "voidcube.interfaces.cli.configuration.get_provider_models_from_api",
        lambda key, *, api_key, base_url: [("step-5-preview-free", ""), ("glm-5.3", "")],
    )
    prompt = _Prompt(
        ["OpenCode Go", "step5", "https://opencode.ai/zen/go/v1", "oc_sk_test", "", "2"]
    )

    key = add_provider_entry(prompt.ports())

    assert key == "step5"
    entry = pool["providers"]["step5"]
    assert entry["label"] == "OpenCode Go"
    assert entry["base_url"] == "https://opencode.ai/zen/go/v1"
    assert entry["selected_model"] == "glm-5.3"
    assert entry["api_key_env"] == "VOIDCUBE_PROVIDER_STEP5_API_KEY"
    assert entry["model_catalog"]["models"] == ["step-5-preview-free", "glm-5.3"]


def test_add_provider_entry_keeps_manual_model_outside_catalog(pool, monkeypatch):
    monkeypatch.setattr(
        "voidcube.interfaces.cli.configuration.get_provider_models_from_api",
        lambda key, *, api_key, base_url: [],
    )
    prompt = _Prompt(
        ["OpenCode Go", "step5", "https://opencode.ai/zen/go/v1", "oc_sk_test", "step-5-preview-free"]
    )

    key = add_provider_entry(prompt.ports())

    assert key == "step5"
    entry = pool["providers"]["step5"]
    assert entry["selected_model"] == "step-5-preview-free"
    assert entry["model_override"] == "step-5-preview-free"


def test_add_provider_entry_rejects_missing_key(pool, monkeypatch):
    monkeypatch.setattr(
        "voidcube.interfaces.cli.configuration.get_provider_models_from_api",
        lambda key, *, api_key, base_url: [("step-5-preview-free", "")],
    )
    prompt = _Prompt(["OpenCode Go", "step5", "https://opencode.ai/zen/go/v1", ""])

    assert add_provider_entry(prompt.ports()) is None
    assert pool["providers"] == {}
    assert any("API Key 不能为空" in line for line in prompt.output)


def test_edit_provider_entry_updates_label_url_and_model(pool):
    _seed(pool)
    prompt = _Prompt(["1", "OpenCode Go", "2", "https://opencode.ai/zen/go/v1", "4", "2", "0"])

    assert edit_provider_entry(prompt.ports(), "step5") is True

    entry = pool["providers"]["step5"]
    assert entry["label"] == "OpenCode Go"
    assert entry["selected_model"] == "glm-5.3"
    assert "model_override" not in entry


def test_edit_provider_entry_clears_manual_override_when_picking_catalog_model(pool):
    _seed(pool, model_override="step-5-preview-free")
    prompt = _Prompt(["4", "2", "0"])

    assert edit_provider_entry(prompt.ports(), "step5") is True

    entry = pool["providers"]["step5"]
    assert entry["selected_model"] == "glm-5.3"
    assert "model_override" not in entry


def test_edit_provider_entry_accepts_manual_model_name(pool):
    _seed(pool)
    prompt = _Prompt(["4", "m", "step-5-preview-free", "0"])

    assert edit_provider_entry(prompt.ports(), "step5") is True

    entry = pool["providers"]["step5"]
    assert entry["selected_model"] == "step-5-preview-free"
    assert entry["model_override"] == "step-5-preview-free"


def test_edit_provider_entry_keeps_existing_key_when_left_blank(pool):
    _seed(pool)
    prompt = _Prompt(["3", "", "0"])

    assert edit_provider_entry(prompt.ports(), "step5") is True

    assert any("保留原有 API Key" in line for line in prompt.output)
    assert pool["providers"]["step5"]["api_key_env"] == "VOIDCUBE_PROVIDER_STEP5_API_KEY"


def test_edit_provider_entry_preserves_custom_headers(pool):
    _seed(pool, headers={"x-opencode-session": "{session_id}", "X-Tenant": "a"})
    prompt = _Prompt(["1", "Renamed", "0"])

    assert edit_provider_entry(prompt.ports(), "step5") is True

    assert pool["providers"]["step5"]["headers"] == {
        "x-opencode-session": "{session_id}",
        "X-Tenant": "a",
    }


def test_edit_provider_entry_refreshes_catalog(pool, monkeypatch):
    _seed(pool)
    monkeypatch.setattr(
        "voidcube.interfaces.cli.configuration.refresh_provider_pool_catalog",
        lambda config, key: (
            {**config, "providers": {**config["providers"], key: {
                **config["providers"][key],
                "model_catalog": {"models": ["glm-5.3", "kimi-k3"]},
            }}},
            ["glm-5.3", "kimi-k3"],
        ),
    )
    prompt = _Prompt(["5", "0"])

    assert edit_provider_entry(prompt.ports(), "step5") is True

    entry = pool["providers"]["step5"]
    assert entry["model_catalog"]["models"] == ["glm-5.3", "kimi-k3"]
    assert entry["selected_model"] == "glm-5.3"


def test_edit_provider_entry_rejects_unknown_provider(pool):
    prompt = _Prompt([])

    assert edit_provider_entry(prompt.ports(), "missing") is False
    assert any("不存在" in line for line in prompt.output)


def test_delete_provider_entry_requires_confirmation(pool):
    _seed(pool)
    prompt = _Prompt(["n"])

    assert delete_provider_entry(prompt.ports(), "step5") is False
    assert "step5" in pool["providers"]


def test_delete_provider_entry_removes_confirmed_provider(pool):
    _seed(pool)
    prompt = _Prompt(["y"])

    assert delete_provider_entry(prompt.ports(), "step5") is True
    assert "step5" not in pool["providers"]


def test_manage_provider_pool_routes_add_edit_and_delete(pool, monkeypatch):
    _seed(pool)
    monkeypatch.setattr(
        "voidcube.interfaces.cli.configuration.get_provider_models_from_api",
        lambda key, *, api_key, base_url: [("grok-4.6", "")],
    )
    prompt = _Prompt(
        [
            "1",  # add
            "Grok", "grok", "https://opencode.ai/zen/go/v1", "oc_sk_test", "", "1",
            "2",  # edit
            "1",  # pick step5
            "1", "OpenCode Go", "0",
            "3",  # delete
            "1",  # pick step5
            "y",
            "0",  # back
        ]
    )

    manage_provider_pool(prompt.ports())

    assert set(pool["providers"]) == {"grok"}
    assert pool["providers"]["grok"]["selected_model"] == "grok-4.6"
    assert "Provider 管理" in prompt.headers


def test_manage_provider_pool_reports_empty_pool_for_edit(pool):
    prompt = _Prompt(["2", "0"])

    manage_provider_pool(prompt.ports())

    assert any("当前没有可操作的 Provider" in line for line in prompt.output)
