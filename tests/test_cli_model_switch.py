from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli as cli_module
from cli import VoidcubeCLI
from VoidCube_cli.command_execution import initialize_command_execution
from VoidCube_cli.model_switch import ModelSwitchResult


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_load_cli_config_uses_shared_loader_and_normalizes(monkeypatch) -> None:
    shared_config = {
        "runtime": {"active_provider": "primary"},
        "providers": {
            "primary": {
                "base_url": "https://example.test/v1",
                "api_key": "test-key",
                "selected_model": "test-model",
            }
        },
    }
    monkeypatch.setattr("VoidCube_cli.config.load_config", lambda: shared_config)

    loaded = cli_module.load_cli_config()

    assert loaded["runtime"]["active_provider"] == "primary"
    assert loaded["model"] == {
        "default": "test-model",
        "model": "test-model",
        "base_url": "https://example.test/v1",
        "provider": "primary",
        "api_key": "test-key",
    }
    assert loaded["terminal"] == {}
    assert loaded["agent"] == {}


def test_load_cli_config_does_not_hide_shared_loader_errors(monkeypatch) -> None:
    def fail_load_config():
        raise RuntimeError("shared config failed")

    monkeypatch.setattr("VoidCube_cli.config.load_config", fail_load_config)

    with pytest.raises(RuntimeError, match="shared config failed"):
        cli_module.load_cli_config()


def test_execution_table_routes_model_command_with_original_arguments() -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    handled: list[str] = []
    app._handle_model_switch = handled.append
    initialize_command_execution(app)

    assert app.process_command("/model Keep/MixedCase --session-only") is True
    assert handled == ["/model Keep/MixedCase --session-only"]


def test_model_command_delegates_result_to_single_apply_path(monkeypatch) -> None:
    result = ModelSwitchResult(
        success=True,
        new_model="next-model",
        target_provider="provider-b",
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app.provider = "provider-a"
    app.model = "old-model"
    app.base_url = "https://old.example/v1"
    app.api_key = "old-key"
    applied: list[tuple[ModelSwitchResult, bool]] = []
    app._apply_model_switch_result = lambda value, persist: applied.append(
        (value, persist)
    )

    monkeypatch.setattr(
        "VoidCube_cli.model_switch.parse_model_flags",
        lambda raw: ("next-model", "provider-b", False),
    )
    monkeypatch.setattr(
        "VoidCube_cli.model_switch.switch_model",
        lambda **kwargs: result,
    )
    monkeypatch.setattr("VoidCube_cli.config.load_config", lambda: {"providers": {}})

    app._handle_model_switch(
        "/model next-model --provider provider-b --session-only"
    )

    assert applied == [(result, False)]


def test_apply_model_switch_updates_cli_running_agent_and_turn_note(monkeypatch) -> None:
    output: list[str] = []
    agent_calls: list[dict] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app.model = "old-model"
    app.provider = "provider-a"
    app.requested_provider = "provider-a"
    app.api_key = "old-key"
    app.base_url = "https://old.example/v1"
    app._explicit_api_key = "old-key"
    app._explicit_base_url = "https://old.example/v1"
    app.agent = SimpleNamespace(
        switch_model=lambda **kwargs: agent_calls.append(kwargs)
    )
    result = ModelSwitchResult(
        success=True,
        new_model="next-model",
        target_provider="provider-b",
        api_key="next-key",
        base_url="https://next.example/v1",
        provider_label="Provider B",
        model_info=SimpleNamespace(
            context_window=128_000,
            max_output=8_192,
            has_cost_data=lambda: False,
            format_capabilities=lambda: "tools",
        ),
    )
    monkeypatch.setattr(cli_module, "_cprint", output.append)

    app._apply_model_switch_result(result, persist_global=False)

    assert app.model == "next-model"
    assert app.provider == "provider-b"
    assert app.requested_provider == "provider-b"
    assert app.api_key == "next-key"
    assert app.base_url == "https://next.example/v1"
    assert app._explicit_api_key == "next-key"
    assert app._explicit_base_url == "https://next.example/v1"
    assert agent_calls == [
        {
            "new_model": "next-model",
            "new_provider": "provider-b",
            "api_key": "next-key",
            "base_url": "https://next.example/v1",
        }
    ]
    assert "old-model" in app._pending_model_switch_note
    assert "next-model" in app._pending_model_switch_note
    assert any("Model switched: next-model" in line for line in output)
    assert any("session only" in line for line in output)
