from __future__ import annotations

from types import SimpleNamespace

import pytest

import voidcube.interfaces.cli.application as cli_module
from voidcube.interfaces.cli.application import VoidcubeCLI
import voidcube.interfaces.cli.commands.registry as command_handler_registry
from voidcube.interfaces.cli.commands.handlers.model import (
    ModelCommandPorts,
    handle_model_command,
)
from voidcube.interfaces.cli.commands.registry import install_cli_command_execution
from voidcube.interfaces.cli.commands.router import parse_cli_command
from voidcube.interfaces.cli.model_switch import ModelSwitchResult


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_load_cli_config_returns_shared_loader_result(monkeypatch) -> None:
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
    monkeypatch.setattr("voidcube.infrastructure.config.configuration.load_config", lambda: shared_config)

    loaded = cli_module.load_cli_config()

    assert loaded is shared_config


def test_resolve_cli_provider_config_uses_unified_provider_map() -> None:
    config = {
        "runtime": {"active_provider": "primary"},
        "providers": {
            "primary": {"selected_model": "primary-model"},
            "secondary": {"selected_model": "secondary-model"},
        },
    }

    provider_key, provider_config = cli_module._resolve_cli_provider_config(config)
    override_key, override_config = cli_module._resolve_cli_provider_config(
        config,
        "secondary",
    )

    assert provider_key == "primary"
    assert provider_config == {"selected_model": "primary-model"}
    assert override_key == "secondary"
    assert override_config == {"selected_model": "secondary-model"}


def test_load_cli_config_does_not_hide_shared_loader_errors(monkeypatch) -> None:
    def fail_load_config():
        raise RuntimeError("shared config failed")

    monkeypatch.setattr("voidcube.infrastructure.config.configuration.load_config", fail_load_config)

    with pytest.raises(RuntimeError, match="shared config failed"):
        cli_module.load_cli_config()


def test_execution_table_routes_model_command_with_preserved_arguments(
    monkeypatch,
) -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.model = "old-model"
    app.provider = "provider-a"
    app.base_url = "https://old.example/v1"
    app.api_key = "old-key"
    arguments: list[str] = []
    applied: list[tuple[ModelSwitchResult, bool]] = []
    result = ModelSwitchResult(success=True, new_model="new-model")
    monkeypatch.setattr(
        command_handler_registry,
        "_model_command_ports",
        lambda _host, *, emit: ModelCommandPorts(
            parse_flags=lambda value: arguments.append(value) or ("new-model", "", False),
            user_providers=lambda: None,
            model=lambda: "old-model",
            provider=lambda: "provider-a",
            base_url=lambda: "https://old.example/v1",
            api_key=lambda: "old-key",
            provider_label=lambda value: value,
            list_configured_providers=lambda **_kwargs: [],
            switch_model=lambda **_kwargs: result,
            open_picker=lambda *_args: pytest.fail("must switch when a model is given"),
            apply_result=lambda value, persist: applied.append((value, persist)),
            emit=emit,
        ),
    )
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/model Keep/MixedCase --session-only") is True
    assert arguments == ["Keep/MixedCase --session-only"]
    assert applied == [(result, False)]


def test_model_handler_delegates_result_to_single_apply_path() -> None:
    result = ModelSwitchResult(
        success=True,
        new_model="next-model",
        target_provider="provider-b",
    )
    applied: list[tuple[ModelSwitchResult, bool]] = []
    observed: list[dict[str, object]] = []

    handle_model_command(
        parse_cli_command("/model next-model --provider provider-b --session-only"),
        ports=ModelCommandPorts(
            parse_flags=lambda _raw: ("next-model", "provider-b", False),
            user_providers=lambda: {"provider-b": {}},
            model=lambda: "old-model",
            provider=lambda: "provider-a",
            base_url=lambda: "https://old.example/v1",
            api_key=lambda: "old-key",
            provider_label=lambda value: value,
            list_configured_providers=lambda **_kwargs: [],
            switch_model=lambda **kwargs: observed.append(kwargs) or result,
            open_picker=lambda *_args: pytest.fail("must not open picker"),
            apply_result=lambda value, persist: applied.append((value, persist)),
            emit=lambda _text: None,
        ),
    )

    assert applied == [(result, False)]
    assert observed == [
        {
            "raw_input": "next-model",
            "current_provider": "provider-a",
            "current_model": "old-model",
            "current_base_url": "https://old.example/v1",
            "current_api_key": "old-key",
            "is_global": False,
            "explicit_provider": "provider-b",
            "user_providers": {"provider-b": {}},
        }
    ]


def test_model_handler_opens_picker_from_configured_provider_snapshot() -> None:
    opened: list[tuple[object, ...]] = []
    providers = [{"slug": "provider-a", "is_current": True}]
    user_providers = {"provider-a": {"selected_model": "old-model"}}

    handle_model_command(
        parse_cli_command("/model"),
        ports=ModelCommandPorts(
            parse_flags=lambda _raw: ("", "", True),
            user_providers=lambda: user_providers,
            model=lambda: "old-model",
            provider=lambda: "provider-a",
            base_url=lambda: "",
            api_key=lambda: "",
            provider_label=lambda value: f"Label {value}",
            list_configured_providers=lambda **kwargs: providers,
            switch_model=lambda **_kwargs: pytest.fail("must not switch without a selection"),
            open_picker=lambda *args: opened.append(args),
            apply_result=lambda *_args: pytest.fail("must not apply without a selection"),
            emit=lambda _text: pytest.fail("must open picker when providers exist"),
        ),
    )

    assert opened == [(providers, "old-model", "Label provider-a", user_providers)]


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
