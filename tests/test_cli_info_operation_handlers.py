from __future__ import annotations

from pathlib import Path

import pytest

from voidcube.interfaces.cli.commands.handlers.info import (
    PluginsCommandPorts,
    ProfileCommandPorts,
    handle_plugins_command,
    handle_profile_command,
)
from voidcube.interfaces.cli.commands.handlers.operations import (
    CancelCommandPorts,
    StopCommandPorts,
    handle_cancel_command,
    handle_stop_command,
)
from voidcube.interfaces.cli.commands.router import parse_cli_command


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_cancel_handler_interrupts_only_an_active_user_turn() -> None:
    events: list[str] = []
    ports = CancelCommandPorts(
        agent_running=lambda: True,
        interrupt_agent=lambda: events.append("interrupt"),
        emit=events.append,
    )

    handle_cancel_command(parse_cli_command("/cancel"), ports=ports)

    assert events == [
        "interrupt",
        "  已请求取消当前用户任务。",
    ]


def test_cancel_handler_reports_when_no_user_turn_is_active() -> None:
    output: list[str] = []

    handle_cancel_command(
        parse_cli_command("/cancel"),
        ports=CancelCommandPorts(
            agent_running=lambda: False,
            interrupt_agent=lambda: pytest.fail("idle cancel must not interrupt"),
            emit=output.append,
        ),
    )

    assert output == ["  当前没有可取消的用户任务。"]


def test_stop_handler_reports_when_no_process_is_running() -> None:
    output: list[str] = []

    handle_stop_command(
        parse_cli_command("/stop"),
        ports=StopCommandPorts(
            list_processes=lambda: (
                {"status": "completed"},
                {"status": "failed"},
            ),
            kill_all=lambda: pytest.fail("no running process must not kill"),
            emit=output.append,
            no_running_message="none running",
            stopping_message=lambda count: f"stopping {count}",
            stopped_message=lambda count: f"stopped {count}",
        ),
    )

    assert output == ["none running"]


def test_stop_handler_counts_running_processes_before_kill_all() -> None:
    events: list[str] = []

    handle_stop_command(
        parse_cli_command("/stop"),
        ports=StopCommandPorts(
            list_processes=lambda: (
                {"status": "running"},
                {"status": "completed"},
                {"status": "running"},
            ),
            kill_all=lambda: events.append("kill") or 2,
            emit=events.append,
            no_running_message="none running",
            stopping_message=lambda count: f"stopping {count}",
            stopped_message=lambda count: f"stopped {count}",
        ),
    )

    assert events == ["stopping 2", "kill", "stopped 2"]


@pytest.mark.parametrize(
    ("home_suffix", "expected_profile"),
    [((), "default profile"), (("work", "nested"), "  Profile: work")],
)
def test_profile_handler_projects_default_or_named_profile(
    tmp_path: Path,
    home_suffix: tuple[str, ...],
    expected_profile: str,
) -> None:
    output: list[str] = []
    profiles_parent = tmp_path / ".VoidCube" / "profiles"
    home = (
        profiles_parent.joinpath(*home_suffix)
        if home_suffix
        else tmp_path / "custom-home"
    )

    handle_profile_command(
        parse_cli_command("/profile"),
        ports=ProfileCommandPorts(
            home=lambda: home,
            display_home=lambda: "~/display-home",
            profiles_parent=lambda: profiles_parent,
            emit=output.append,
            default_profile_message="default profile",
        ),
    )

    assert output == ["", expected_profile, "  Home:    ~/display-home", ""]


def test_plugins_handler_projects_empty_registry() -> None:
    output: list[str] = []
    calls: list[str] = []

    handle_plugins_command(
        parse_cli_command("/plugins"),
        ports=PluginsCommandPorts(
            discover=lambda: calls.append("discover"),
            list_plugins=lambda: (),
            plugins_home=lambda: "~/.VoidCube",
            emit=output.append,
        ),
    )

    assert calls == ["discover"]
    assert output == [
        "No plugins installed.",
        "Drop plugin directories into ~/.VoidCube/plugins/ to get started.",
    ]


def test_plugins_handler_projects_status_version_counts_and_error() -> None:
    output: list[str] = []

    handle_plugins_command(
        parse_cli_command("/plugins"),
        ports=PluginsCommandPorts(
            discover=lambda: None,
            list_plugins=lambda: (
                {
                    "name": "active",
                    "enabled": True,
                    "version": "1.2",
                    "tools": 2,
                    "hooks": 1,
                    "error": "",
                },
                {
                    "name": "broken",
                    "enabled": False,
                    "version": "",
                    "tools": 0,
                    "hooks": 0,
                    "error": "load failed",
                },
            ),
            plugins_home=lambda: "unused",
            emit=output.append,
        ),
    )

    assert output == [
        "Plugins (2):",
        "  ✓ active v1.2 (2 tools, 1 hooks)",
        "  ✗ broken — load failed",
    ]


def test_plugins_handler_projects_discovery_failure() -> None:
    output: list[str] = []

    handle_plugins_command(
        parse_cli_command("/plugins"),
        ports=PluginsCommandPorts(
            discover=lambda: (_ for _ in ()).throw(RuntimeError("scan failed")),
            list_plugins=lambda: pytest.fail("failed discovery must not list"),
            plugins_home=lambda: "unused",
            emit=output.append,
        ),
    )

    assert output == ["Plugin system error: scan failed"]
