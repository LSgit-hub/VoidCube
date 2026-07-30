from __future__ import annotations

import pytest

import cli as cli_module
from cli import VoidcubeCLI
from VoidCube_cli.command_router import (
    looks_like_slash_command,
    parse_cli_command,
    resolve_dynamic_command,
    slow_command_status,
)
from VoidCube_cli.command_execution import initialize_command_execution


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _resolve(
    text: str,
    *,
    quick_commands=None,
    plugin_names=None,
    skill_commands=None,
    known_commands=None,
):
    return resolve_dynamic_command(
        parse_cli_command(text),
        quick_commands=quick_commands or {},
        plugin_names=plugin_names or set(),
        skill_commands=skill_commands or {},
        known_commands=known_commands or set(),
    )


def test_slash_detection_distinguishes_commands_from_absolute_paths() -> None:
    assert looks_like_slash_command("/help") is True
    assert looks_like_slash_command("/model Model-With-Case") is True
    assert looks_like_slash_command("/Users/name/project.py please inspect") is False
    assert looks_like_slash_command("plain text") is False


def test_parse_preserves_arguments_and_resolves_registry_alias() -> None:
    request = parse_cli_command("  /R Mixed Case Session  ")

    assert request.original == "/R Mixed Case Session"
    assert request.normalized == "/r mixed case session"
    assert request.base_token == "/r"
    assert request.name == "r"
    assert request.canonical == "resume"
    assert request.arguments == "Mixed Case Session"
    assert request.suffix == " Mixed Case Session"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/skills search logs", "Searching skills..."),
        ("/skills browse", "Loading skills..."),
        ("/skills inspect item", "Inspecting skill..."),
        ("/skills install item", "Installing skill..."),
        ("/skills list", "Processing skills command..."),
        ("/reload-mcp", "Reloading MCP servers..."),
        ("/browser status", "Configuring browser..."),
        ("/other", "Processing command..."),
    ],
)
def test_slow_command_status_is_derived_from_parsed_command(
    command: str,
    expected: str,
) -> None:
    assert slow_command_status(command) == expected


def test_quick_command_has_priority_and_alias_preserves_user_arguments() -> None:
    exec_route = _resolve(
        "/deploy now",
        quick_commands={"deploy": {"type": "exec", "command": "echo deploy"}},
        plugin_names={"deploy"},
        skill_commands={"/deploy": {"name": "deploy"}},
    )
    alias_route = _resolve(
        "/switch MixedCase",
        quick_commands={"switch": {"type": "alias", "target": "model"}},
    )

    assert exec_route.kind == "quick_exec"
    assert exec_route.executable == "echo deploy"
    assert alias_route.kind == "quick_alias"
    assert alias_route.redirect_command == "/model MixedCase"


def test_plugin_precedes_skill_and_skill_route_keeps_slash_key() -> None:
    plugin_route = _resolve(
        "/inspect target",
        plugin_names={"inspect"},
        skill_commands={"/inspect": {"name": "skill-inspect"}},
    )
    skill_route = _resolve(
        "/skill-only target",
        skill_commands={"/skill-only": {"name": "skill-only"}},
    )

    assert plugin_route.kind == "plugin"
    assert plugin_route.request.arguments == "target"
    assert skill_route.kind == "skill"
    assert skill_route.request.base_token == "/skill-only"


def test_prefix_resolution_prefers_unique_shortest_and_preserves_suffix() -> None:
    route = _resolve(
        "/qui Mixed Case",
        known_commands={"/quit", "/quint-pipeline"},
    )

    assert route.kind == "redirect"
    assert route.redirect_command == "/quit Mixed Case"


def test_ambiguous_exact_and_unknown_routes_are_explicit() -> None:
    ambiguous = _resolve(
        "/mo",
        known_commands={"/model", "/money"},
    )
    exact = _resolve("/model", known_commands={"/model"})
    unknown = _resolve("/missing", known_commands={"/model"})

    assert ambiguous.kind == "ambiguous"
    assert ambiguous.matches == ("/model", "/money")
    assert exact.kind == "unknown"
    assert unknown.kind == "unknown"


def test_cli_process_uses_router_for_quick_alias(monkeypatch) -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app.config = {
        "quick_commands": {
            "observe": {"type": "alias", "target": "tasks"},
        }
    }
    handled: list[str] = []
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    initialize_command_execution(
        app, command_handlers={"tasks": lambda request: handled.append(request.original)}
    )
    monkeypatch.setattr(cli_module, "_get_skill_commands", lambda: {})
    monkeypatch.setattr(cli_module, "_get_plugin_cmd_handler_names", lambda: set())

    assert app.process_command("/observe bg MixedCase") is True
    assert handled == ["/tasks bg MixedCase"]


def test_cli_process_uses_router_for_unique_prefix(monkeypatch) -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app.config = {"quick_commands": {}}
    handled: list[str] = []
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    initialize_command_execution(
        app, command_handlers={"tasks": lambda request: handled.append(request.original)}
    )
    monkeypatch.setattr(cli_module, "_get_skill_commands", lambda: {})
    monkeypatch.setattr(cli_module, "_get_plugin_cmd_handler_names", lambda: set())

    assert app.process_command("/tas fg MixedCase") is True
    assert handled == ["/tasks fg MixedCase"]
