"""Session identity and persistence bootstrap from explicit ports."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...infrastructure.config.runtime_paths import get_VoidCube_home

from ...infrastructure.persistence.session_runtime import SessionPersistence


def _new_session_uuid() -> str:
    return uuid.uuid4().hex


def _build_checkpoint_manager(**kwargs: Any) -> Any:
    from ...infrastructure.persistence.checkpoint_manager import CheckpointManager

    return CheckpointManager(**kwargs)


@dataclass(frozen=True, slots=True)
class AgentSessionInitializationPorts:
    """Inputs and factories required to create one Agent session boundary."""

    requested_session_id: str | None
    platform: str | None
    model_reader: Callable[[], str]
    base_url_reader: Callable[[], str]
    system_prompt_reader: Callable[[], str | None]
    tools_reader: Callable[[], Sequence[Mapping[str, Any]] | None]
    user_message_override_reader: Callable[[], tuple[int | None, Any]]
    session_db: Any
    parent_session_id: str | None
    max_iterations: int
    reasoning_config: Any
    max_tokens: int | None
    persist_session: bool
    verbose_logging: bool
    checkpoints_enabled: bool
    checkpoint_max_snapshots: int
    home_provider: Callable[[], Path] = get_VoidCube_home
    clock: Callable[[], datetime] = datetime.now
    session_uuid_factory: Callable[[], str] = _new_session_uuid
    checkpoint_factory: Callable[..., Any] = _build_checkpoint_manager
    persistence_factory: Callable[..., SessionPersistence] = SessionPersistence


@dataclass(frozen=True, slots=True)
class AgentSessionInitializationResult:
    """Structured session infrastructure returned to the Agent host."""

    session_id: str
    session_start: datetime
    logs_dir: Path
    checkpoint_manager: Any
    session_persistence: SessionPersistence


class AgentSessionInitializationRuntime:
    """Own session identity, DB registration, checkpoints and persistence wiring."""

    def __init__(self, ports: AgentSessionInitializationPorts) -> None:
        self.ports = ports

    def initialize(self) -> AgentSessionInitializationResult:
        ports = self.ports
        session_start = ports.clock()
        session_id = ports.requested_session_id or self._generate_session_id(
            session_start
        )
        logs_dir = Path(ports.home_provider()) / "sessions"
        checkpoint_manager = ports.checkpoint_factory(
            enabled=ports.checkpoints_enabled,
            max_snapshots=ports.checkpoint_max_snapshots,
        )

        self._register_session(
            session_id=session_id,
            model=ports.model_reader(),
        )
        persistence = ports.persistence_factory(
            enabled=ports.persist_session,
            logs_dir=logs_dir,
            session_db=ports.session_db,
            session_start=session_start,
            session_id=lambda: session_id,
            model=ports.model_reader,
            base_url=ports.base_url_reader,
            platform=lambda: ports.platform,
            system_prompt=ports.system_prompt_reader,
            tools=ports.tools_reader,
            user_message_override=ports.user_message_override_reader,
            verbose_logging=ports.verbose_logging,
        )
        return AgentSessionInitializationResult(
            session_id=session_id,
            session_start=session_start,
            logs_dir=logs_dir,
            checkpoint_manager=checkpoint_manager,
            session_persistence=persistence,
        )

    def _generate_session_id(self, session_start: datetime) -> str:
        timestamp = session_start.strftime("%Y%m%d_%H%M%S")
        return f"{timestamp}_{self.ports.session_uuid_factory()[:6]}"

    def _register_session(self, *, session_id: str, model: str) -> None:
        ports = self.ports
        if not ports.session_db:
            return
        try:
            ports.session_db.create_session(
                session_id=session_id,
                source=ports.platform
                or os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
                model=model,
                model_config={
                    "max_iterations": ports.max_iterations,
                    "reasoning_config": ports.reasoning_config,
                    "max_tokens": ports.max_tokens,
                },
                user_id=None,
                parent_session_id=ports.parent_session_id,
            )
        except Exception as exc:
            # A transient SQLite lock must not disable later persistence.
            import logging

            logging.getLogger(__name__).warning(
                "Session DB create_session failed (session_search still available): %s",
                exc,
            )
