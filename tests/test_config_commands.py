from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_config_command_layer_owns_cli_handlers():
    config_source = (ROOT / "VoidCube_cli" / "config.py").read_text(encoding="utf-8")
    command_source = (ROOT / "VoidCube_cli" / "config_commands.py").read_text(
        encoding="utf-8"
    )
    for name in ("show_config", "edit_config", "set_config_value", "config_command"):
        assert f"def {name}" not in config_source
        assert f"def {name}" in command_source

    main_tree = ast.parse(
        (ROOT / "VoidCube_cli" / "entrypoint_operations.py").read_text(
            encoding="utf-8"
        )
    )
    imports = {
        alias.name
        for node in ast.walk(main_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "VoidCube_cli.config_commands"
        for alias in node.names
    }
    assert "config_command" in imports


def test_set_config_value_writes_terminal_config_and_removes_legacy_env(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / "config.yaml").write_text(
        "_config_version: 20\nproviders: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("VOIDCUBE_MANAGED", raising=False)
    (home / ".env").write_text("TERMINAL_TIMEOUT=30\n", encoding="utf-8")

    from VoidCube_cli.config_commands import set_config_value

    set_config_value("terminal.timeout", "75")

    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config == {
        "_config_version": 20,
        "providers": {},
        "terminal": {"timeout": 75},
    }
    assert "TERMINAL_TIMEOUT" not in (home / ".env").read_text(encoding="utf-8")


def test_set_config_value_routes_secret_keys_only_to_env(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    (home / "config.yaml").write_text(
        "_config_version: 20\nproviders: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("VOIDCUBE_MANAGED", raising=False)
    monkeypatch.delenv("CUSTOM_SERVICE_API_KEY", raising=False)

    from VoidCube_cli.config_commands import set_config_value

    set_config_value("CUSTOM_SERVICE_API_KEY", "secret-value")

    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config == {"_config_version": 20, "providers": {}}
    assert "CUSTOM_SERVICE_API_KEY=secret-value" in (
        home / ".env"
    ).read_text(encoding="utf-8")


def test_save_config_value_updates_a_nested_config_value(monkeypatch) -> None:
    from VoidCube_app import config as config_module

    loaded = {"agent": {"system_prompt": "old"}}
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(config_module, "load_config", lambda: loaded)
    monkeypatch.setattr(config_module, "save_config", saved.append)

    assert config_module.save_config_value("agent.system_prompt", "new") is True
    assert loaded == {"agent": {"system_prompt": "new"}}
    assert saved == [loaded]


def test_config_command_dispatches_path_without_loading_full_config(tmp_path, monkeypatch, capsys):
    home = tmp_path / ".VoidCube"
    home.mkdir()
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    from VoidCube_cli.config_commands import config_command

    config_command(SimpleNamespace(config_command="path"))

    assert capsys.readouterr().out.strip() == str(home / "config.yaml")
