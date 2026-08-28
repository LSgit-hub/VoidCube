from __future__ import annotations

import ast
from pathlib import Path

import pytest

from voidcube.interfaces.cli.entrypoints.dispatch import dispatch_cli
from voidcube.interfaces.cli.entrypoints.parser import build_parser


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_main_is_only_the_cli_composition_root():
    path = ROOT / "src" / "voidcube" / "interfaces" / "cli" / "main.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    imports = {
        (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert functions == ["main"]
    assert imports == {
        ("__future__", "annotations"),
        ("entrypoints.dispatch", "dispatch_cli"),
        ("entrypoints.parser", "build_parser"),
    }
    assert "cmd_" not in source
    assert len(source.splitlines()) <= 20


def test_parser_registry_is_independently_constructible():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices

    assert {
        "chat",
        "login",
        "logout",
        "doctor",
        "body",
        "serve",
        "memory",
        "sessions",
        "api",
        "profile",
    } <= commands.keys()
    assert "model" not in commands
    assert parser.parse_args(["body", "status"]).body_action == "status"
    assert parser.parse_args(["chat", "-q", "hello"]).query == "hello"


def test_entrypoint_command_registry_has_no_aliases():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices

    assert {"autonomous", "auto-cli"}.isdisjoint(commands)

    mcp_commands = commands["mcp"]._subparsers._group_actions[0].choices
    assert {"remove", "list", "configure"} <= mcp_commands.keys()
    assert {"rm", "ls", "config"}.isdisjoint(mcp_commands)


def test_dispatch_maps_version_without_reading_process_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "voidcube.interfaces.cli.entrypoints.dispatch.cmd_version",
        lambda args: calls.append(args.command),
    )

    dispatch_cli(build_parser(), ["--version"])

    assert calls == [None]


def test_dispatch_defaults_to_chat_through_explicit_handler_port(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "voidcube.interfaces.cli.entrypoints.dispatch.cmd_chat",
        lambda args: calls.append(args),
    )
    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.get_container_exec_info",
        lambda: None,
    )

    dispatch_cli(build_parser(), [])

    assert len(calls) == 1
    assert calls[0].command is None
    assert calls[0].query is None


def test_unknown_command_preserves_argparse_exit_code():
    with pytest.raises(SystemExit) as exc_info:
        dispatch_cli(build_parser(), ["not-a-command"])

    assert exc_info.value.code == 2


def test_entrypoint_modules_do_not_create_new_large_methods():
    violations = []
    for path in (ROOT / "src" / "voidcube" / "interfaces" / "cli" / "entrypoints").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lines = (node.end_lineno or node.lineno) - node.lineno + 1
            if lines > 250:
                violations.append(f"{path.name}:{node.name}={lines}")

    assert violations == []
