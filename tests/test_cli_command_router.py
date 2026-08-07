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
    custom_commands=None,
    plugin_names=None,
    skill_commands=None,
):
    return resolve_dynamic_command(
        parse_cli_command(text),
        custom_commands=custom_commands or {},
        plugin_names=plugin_names or set(),
        skill_commands=skill_commands or {},
    )


def test_slash_detection_distinguishes_commands_from_absolute_paths() -> None:
    assert looks_like_slash_command("/help") is True
    assert looks_like_slash_command("/model Model-With-Case") is True
    assert looks_like_slash_command("/Users/name/project.py please inspect") is False
    assert looks_like_slash_command("plain text") is False


def test_parse_preserves_arguments_and_rejects_removed_registry_alias() -> None:
    request = parse_cli_command("  /R Mixed Case Session  ")

    assert request.original == "/R Mixed Case Session"
    assert request.normalized == "/r mixed case session"
    assert request.base_token == "/r"
    assert request.name == "r"
    assert request.canonical == "r"
    assert request.arguments == "Mixed Case Session"


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


def test_custom_exec_command_has_priority() -> None:
    exec_route = _resolve(
        "/deploy now",
        custom_commands={"deploy": {"type": "exec", "command": "echo deploy"}},
        plugin_names={"deploy"},
        skill_commands={"/deploy": {"name": "deploy"}},
    )
    assert exec_route.kind == "custom_exec"
    assert exec_route.executable == "echo deploy"


def test_custom_command_rejects_unsupported_type() -> None:
    route = _resolve(
        "/switch MixedCase",
        custom_commands={"switch": {"type": "alias", "target": "model"}},
    )

    assert route.kind == "custom_invalid"


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


def test_unknown_commands_are_not_prefix_resolved() -> None:
    assert _resolve("/qui Mixed Case").kind == "unknown"
    assert _resolve("/mo").kind == "unknown"
    assert _resolve("/missing").kind == "unknown"
