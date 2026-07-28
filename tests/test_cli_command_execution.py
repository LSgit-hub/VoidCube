from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli as cli_module
from cli import VoidcubeCLI
from VoidCube_cli.command_execution import (
    BUILTIN_COMMAND_SPECS,
    CommandBusyLifecycle,
    initialize_command_execution,
)
from VoidCube_cli.command_router import parse_cli_command
from VoidCube_cli.commands import COMMAND_REGISTRY


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


EXPECTED_BUILTINS = {
    "api",
    "auto",
    "auto-q",
    "background",
    "branch",
    "browser",
    "btw",
    "clear",
    "compress",
    "config",
    "connect",
    "debug",
    "doctor",
    "fast",
    "help",
    "history",
    "image",
    "language",
    "mcp",
    "memory",
    "model",
    "new",
    "paste",
    "personality",
    "plan",
    "plugins",
    "preset",
    "profile",
    "provider",
    "queue",
    "quit",
    "reasoning",
    "reload-mcp",
    "resume",
    "retry",
    "rollback",
    "save",
    "skills",
    "skin",
    "status",
    "statusbar",
    "stop",
    "tasks",
    "title",
    "tools",
    "toolsets",
    "undo",
    "usage",
    "verbose",
    "voice",
    "yolo",
}


def test_interrupted_text_payloads_are_combined_for_the_next_turn() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    interrupts.put("second")

    payloads = cli_module._requeue_interrupted_payloads(
        pending,
        interrupts,
        "first",
    )

    assert payloads == ["first", "second"]
    assert pending.get_nowait() == "first\nsecond"


def test_interrupted_multimodal_payload_keeps_attachments_and_order() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    first = ("inspect this", ["screen.png"])
    interrupts.put("then summarize")

    payloads = cli_module._requeue_interrupted_payloads(
        pending,
        interrupts,
        first,
    )

    assert cli_module._interrupt_text(first) == "inspect this"
    assert payloads == [first, "then summarize"]
    assert pending.get_nowait() == first
    assert pending.get_nowait() == "then summarize"


def test_builtin_table_is_complete_and_contains_no_removed_commands() -> None:
    assert set(BUILTIN_COMMAND_SPECS) == EXPECTED_BUILTINS
    assert "cron" not in BUILTIN_COMMAND_SPECS
    assert "insights" not in BUILTIN_COMMAND_SPECS
    for spec in BUILTIN_COMMAND_SPECS.values():
        if spec.exits:
            continue
        assert hasattr(VoidcubeCLI, spec.handler_name), spec.handler_name


def test_retired_cron_integration_has_no_active_runtime_or_config_surface() -> None:
    active_surfaces = (
        "config.yaml",
        "agent/display.py",
        "agent/memory_provider.py",
        "agent/prompt_builder.py",
        "VoidCube_cli/config.py",
        "VoidCube_cli/main.py",
        "VoidCube_cli/status.py",
        "VoidCube_cli/locales/en_US.json",
        "VoidCube_cli/locales/zh_CN.json",
        "VoidCube_core/logging.py",
    )

    for path in active_surfaces:
        source = Path(path).read_text(encoding="utf-8").casefold()
        assert "cron" not in source, path


def test_every_discoverable_cli_builtin_has_an_execution_spec() -> None:
    discoverable = {
        command.name for command in COMMAND_REGISTRY if not command.gateway_only
    }

    assert discoverable <= set(BUILTIN_COMMAND_SPECS)


def test_executor_distinguishes_exit_builtin_and_dynamic_command() -> None:
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    initialize_command_execution(host)

    exit_result = host._builtin_command_executor.execute(parse_cli_command("/quit"))
    dynamic_result = host._builtin_command_executor.execute(
        parse_cli_command("/plugin-command")
    )

    assert exit_result.handled is True
    assert exit_result.continue_running is False
    assert dynamic_result.handled is False
    assert dynamic_result.continue_running is True


def test_executor_passes_original_command_only_when_declared() -> None:
    calls: list[tuple[str, str | None]] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    host._handle_tools_command = lambda command: calls.append(("tools", command))
    host.show_help = lambda: calls.append(("help", None))
    initialize_command_execution(host)

    host._builtin_command_executor.execute(
        parse_cli_command("/tools Enable MixedCase")
    )
    host._builtin_command_executor.execute(parse_cli_command("/help"))

    assert calls == [("tools", "/tools Enable MixedCase"), ("help", None)]


def test_busy_lifecycle_restores_nested_and_exceptional_state() -> None:
    invalidations: list[float] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda *, min_interval: invalidations.append(min_interval)
    lifecycle = CommandBusyLifecycle(host)

    with lifecycle.activate("outer"):
        assert (host._command_running, host._command_status) == (True, "outer")
        with lifecycle.activate("inner"):
            assert (host._command_running, host._command_status) == (True, "inner")
        assert (host._command_running, host._command_status) == (True, "outer")

    assert (host._command_running, host._command_status) == (False, "")
    with pytest.raises(RuntimeError):
        with lifecycle.activate("failing"):
            raise RuntimeError("stop")
    assert (host._command_running, host._command_status) == (False, "")
    assert invalidations == [0.0] * 6


def test_busy_spec_wraps_handler_and_restores_state() -> None:
    observed: list[tuple[bool, str, str]] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    host._handle_skills_command = lambda command: observed.append(
        (host._command_running, host._command_status, command)
    )
    initialize_command_execution(host)

    result = host._builtin_command_executor.execute(
        parse_cli_command("/skills search MixedCase")
    )

    assert result.handled is True
    assert observed == [(True, "Searching skills...", "/skills search MixedCase")]
    assert (host._command_running, host._command_status) == (False, "")


def test_cli_process_uses_execution_table_for_quit() -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    initialize_command_execution(app)

    assert app.process_command("/quit") is False


def test_cli_process_uses_execution_table_for_queue(monkeypatch) -> None:
    output: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._pending_input = queue.Queue()
    app._agent_running = True
    initialize_command_execution(app)
    monkeypatch.setattr(cli_module, "_cprint", output.append)

    assert app.process_command("/queue Keep MixedCase") is True
    assert app._pending_input.get_nowait() == "Keep MixedCase"
    assert output == ["  Queued for the next turn: Keep MixedCase"]
