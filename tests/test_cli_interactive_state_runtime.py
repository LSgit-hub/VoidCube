from __future__ import annotations

from pathlib import Path

from voidcube.interfaces.cli.lifecycle.state import (
    CliInteractiveStateApplyPorts,
    CliInteractiveStatePorts,
    CliInteractiveStateRuntime,
)
from voidcube.interfaces.cli.voice_runtime_state import CliVoiceRuntimeState


def test_interactive_state_runtime_creates_fresh_run_scoped_state(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n", encoding="utf-8")
    voice_state = CliVoiceRuntimeState()

    runtime = CliInteractiveStateRuntime(
        CliInteractiveStatePorts(
            config_path=config,
            config_mcp_servers={"local": {"command": "server"}},
            voice_state_factory=lambda: voice_state,
        )
    )
    state = runtime.initialize()
    second_state = runtime.initialize()

    assert state.agent_running is False
    assert state.config_mtime == config.stat().st_mtime
    assert state.config_mcp_servers == {"local": {"command": "server"}}
    assert state.voice_runtime_state is voice_state
    assert state.attached_images == []
    assert state.approval_lock is not second_state.approval_lock


def test_interactive_state_runtime_projects_all_fields_to_host() -> None:
    runtime = CliInteractiveStateRuntime(
        CliInteractiveStatePorts(
            config_path=Path("missing-config.yaml"),
            config_mcp_servers={"local": {}},
        )
    )
    state = runtime.initialize()
    values: dict[str, object] = {}
    reset_calls: list[bool] = []

    runtime.apply(
        state,
        CliInteractiveStateApplyPorts(
            reset_input_queues=lambda: reset_calls.append(True),
            set_value=lambda name, value: values.__setitem__(name, value),
        ),
    )

    assert reset_calls == [True]
    assert set(values) == {
        "agent_running",
        "should_exit",
        "config_mtime",
        "config_mcp_servers",
        "last_config_check",
        "clarify_state",
        "clarify_freetext",
        "clarify_deadline",
        "sudo_state",
        "sudo_deadline",
        "modal_input_snapshot",
        "approval_state",
        "approval_deadline",
        "approval_lock",
        "secret_state",
        "secret_deadline",
        "attached_images",
        "image_counter",
        "voice_runtime_state",
    }
    assert values["config_mcp_servers"] == {"local": {}}
    assert values["approval_lock"] is state.approval_lock
