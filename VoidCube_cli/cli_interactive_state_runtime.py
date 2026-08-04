"""Create the state snapshot for one interactive CLI run."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


@dataclass(slots=True)
class CliInteractiveRunState:
    """Transient values copied into the CLI host at interactive-run start."""

    agent_running: bool
    should_exit: bool
    last_ctrl_c_time: float
    config_mtime: float
    config_mcp_servers: dict[str, Any]
    last_config_check: float
    clarify_state: Any
    clarify_freetext: bool
    clarify_deadline: float
    sudo_state: Any
    sudo_deadline: float
    modal_input_snapshot: Any
    approval_state: Any
    approval_deadline: float
    approval_lock: threading.Lock
    secret_state: Any
    secret_deadline: float
    attached_images: list[Path]
    image_counter: int
    voice_runtime_state: CliVoiceRuntimeState


@dataclass(frozen=True, slots=True)
class CliInteractiveStatePorts:
    """Configuration inputs required to create an interactive-run snapshot."""

    config_path: Path
    config_mcp_servers: Mapping[str, Any]
    voice_state_factory: Callable[[], CliVoiceRuntimeState] = CliVoiceRuntimeState


class CliInteractiveStateRuntime:
    """Build run-scoped state without reading or mutating the CLI host."""

    def __init__(self, ports: CliInteractiveStatePorts) -> None:
        self.ports = ports

    def initialize(self) -> CliInteractiveRunState:
        config_path = self.ports.config_path
        config_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
        return CliInteractiveRunState(
            agent_running=False,
            should_exit=False,
            last_ctrl_c_time=0.0,
            config_mtime=config_mtime,
            config_mcp_servers=dict(self.ports.config_mcp_servers),
            last_config_check=0.0,
            clarify_state=None,
            clarify_freetext=False,
            clarify_deadline=0.0,
            sudo_state=None,
            sudo_deadline=0.0,
            modal_input_snapshot=None,
            approval_state=None,
            approval_deadline=0.0,
            approval_lock=threading.Lock(),
            secret_state=None,
            secret_deadline=0.0,
            attached_images=[],
            image_counter=0,
            voice_runtime_state=self.ports.voice_state_factory(),
        )
