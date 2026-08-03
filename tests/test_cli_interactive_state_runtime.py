from __future__ import annotations

from VoidCube_cli.cli_interactive_state_runtime import (
    CliInteractiveStatePorts,
    CliInteractiveStateRuntime,
)
from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


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
    assert state.pending_input.empty()
    assert state.interrupt_queue.empty()
    assert state.config_mtime == config.stat().st_mtime
    assert state.config_mcp_servers == {"local": {"command": "server"}}
    assert state.voice_runtime_state is voice_state
    assert state.attached_images == []
    assert state.approval_lock is not second_state.approval_lock
