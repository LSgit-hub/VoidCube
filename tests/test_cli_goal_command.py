from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.interfaces.cli.commands.handlers.goal import GoalCommandPorts, handle_goal_command
from voidcube.interfaces.cli.commands.router import parse_cli_command
from voidcube.interfaces.cli.session_goal_runtime import (
    ACTIVE,
    BLOCKED,
    COMPLETED,
    clear_goal,
    create_goal,
    get_goal,
    goal_prompt,
    update_goal,
)
from voidcube.interfaces.cli.application import VoidcubeCLI


pytestmark = pytest.mark.unit


def _host() -> SimpleNamespace:
    return SimpleNamespace(session_id="goal-session", _session_goals={})


def test_goal_runtime_is_session_isolated_and_prompt_is_active_only():
    host = _host()
    other = SimpleNamespace(session_id="other-session", _session_goals={})

    create_goal(host, "Finish the TUI quality pass")
    assert get_goal(host)["status"] == ACTIVE
    assert get_goal(other) is None
    assert "Finish the TUI quality pass" in goal_prompt(get_goal(host))

    update_goal(host, COMPLETED, "verified")
    assert get_goal(host)["status"] == COMPLETED
    assert goal_prompt(get_goal(host)) == ""
    assert clear_goal(host) is True
    assert get_goal(host) is None


def test_active_goal_is_included_in_agent_system_prompt():
    host = _host()
    host.system_prompt = "Base instructions"
    create_goal(host, "Keep the command surface canonical")

    prompt = VoidcubeCLI._effective_system_prompt(host)

    assert prompt.startswith("Base instructions")
    assert "Active Session Goal" in prompt
    assert "Keep the command surface canonical" in prompt


def test_goal_handler_creates_and_queues_objective():
    state: dict[str, object] = {"goal": None}
    output: list[str] = []
    queued: list[str] = []
    reset_count = 0

    def reset_agent() -> None:
        nonlocal reset_count
        reset_count += 1

    def translate(key: str, **kwargs: str) -> str:
        return key + (f":{kwargs}" if kwargs else "")

    ports = GoalCommandPorts(
        get_goal=lambda: state["goal"],
        create_goal=lambda objective: state.update(
            goal={"objective": objective, "status": ACTIVE}
        ) or state["goal"],
        update_goal=lambda _status, _reason: False,
        clear_goal=lambda: False,
        start_goal=queued.append,
        reset_agent=reset_agent,
        emit=output.append,
        translate=translate,
    )

    handle_goal_command(
        parse_cli_command("/goal Build a reliable terminal harness"),
        ports=ports,
    )

    assert queued == ["Build a reliable terminal harness"]
    assert reset_count == 1
    assert output[0].startswith("goal_command.created")


def test_goal_handler_requires_terminal_transition_before_clear():
    state: dict[str, object] = {
        "goal": {"objective": "active", "status": ACTIVE},
    }
    output: list[str] = []
    ports = GoalCommandPorts(
        get_goal=lambda: state["goal"],
        create_goal=lambda objective: state["goal"],
        update_goal=lambda status, reason: state["goal"].update(
            status=status, reason=reason
        ) or True,
        clear_goal=lambda: state.update(goal=None) or True,
        start_goal=None,
        reset_agent=lambda: None,
        emit=output.append,
        translate=lambda key, **kwargs: key,
    )

    handle_goal_command(parse_cli_command("/goal clear"), ports=ports)
    assert output == ["goal_command.clear_active"]

    handle_goal_command(
        parse_cli_command("/goal blocked waiting for credentials"),
        ports=ports,
    )
    assert state["goal"]["status"] == BLOCKED
    handle_goal_command(parse_cli_command("/goal clear"), ports=ports)
    assert state["goal"] is None
