from types import SimpleNamespace

from voidcube.interfaces.cli.commands.handlers.context import (
    handle_context_command,
    parse_context_length,
)
from voidcube.interfaces.cli.commands.router import parse_cli_command


def test_parse_context_length_accepts_common_units():
    assert parse_context_length("256k") == 256_000
    assert parse_context_length("512K") == 512_000
    assert parse_context_length("1M") == 1_000_000
    assert parse_context_length("131072") == 131_072
    assert parse_context_length("nope") is None


def test_context_command_applies_override_and_persists_model_specific_value():
    events = []
    saved = []
    state = {"context_length": 128_000, "threshold_tokens": 64_000,
             "target_ratio": 0.20, "tail_token_budget": 12_800,
             "source": "fallback", "minimum_context_length": 64_000}
    agent = SimpleNamespace()

    class Ports:
        agent = lambda self: agent
        current_model = lambda self: "demo.model"
        current_provider = lambda self: "custom"
        context_state = lambda self, _agent: state
        set_context_length = lambda self, _agent, length: {
            **state, "context_length": length, "threshold_tokens": 600_000,
            "target_ratio": 0.15,
        }
        save_context_length = lambda self, provider, model, length: saved.append(
            (provider, model, length)
        ) or True
        emit = lambda self, text: events.append(text)

    handle_context_command(parse_cli_command("/context 1M"), ports=Ports())
    assert saved == [("custom", "demo.model", 1_000_000)]
    assert "1,000,000" in events[-1]


def test_context_command_rejects_too_small_window():
    events = []
    agent = SimpleNamespace()

    class Ports:
        agent = lambda self: agent
        context_state = lambda self, _agent: {"minimum_context_length": 64_000}
        emit = lambda self, text: events.append(text)

    handle_context_command(parse_cli_command("/context 32K"), ports=Ports())
    assert "at least" in events[-1]


def test_context_command_rejects_retired_64k_preset():
    events = []
    agent = SimpleNamespace()

    class Ports:
        agent = lambda self: agent
        context_state = lambda self, _agent: {"minimum_context_length": 64_000}
        emit = lambda self, text: events.append(text)

    handle_context_command(parse_cli_command("/context 64K"), ports=Ports())
    assert "128,000" in events[-1]


def test_context_command_persists_before_agent_is_created():
    events = []
    saved = []

    class Ports:
        agent = lambda self: None
        current_model = lambda self: "demo"
        current_provider = lambda self: "custom"
        save_context_length = lambda self, provider, model, length: saved.append(
            (provider, model, length)
        ) or True
        emit = lambda self, text: events.append(text)

    handle_context_command(parse_cli_command("/context 512K"), ports=Ports())
    assert saved == [("custom", "demo", 512_000)]
    assert "when the Agent starts" in events[-1]
