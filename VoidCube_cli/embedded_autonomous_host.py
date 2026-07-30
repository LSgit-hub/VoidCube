"""Lifecycle assembly for the CLI's embedded autonomous child host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmbeddedAutonomousHostPorts:
    """Host-owned operations needed to create or reuse a child CLI host."""

    get_component_host: Callable[[], Any | None]
    create_component_host: Callable[[], Any]
    set_component_active: Callable[[Any, bool], None]
    bind_component_parent: Callable[[Any], None]
    ensure_task_session: Callable[[Any], None]
    store_component_host: Callable[[Any], None]


def ensure_embedded_autonomous_component_host(
    ports: EmbeddedAutonomousHostPorts,
) -> Any:
    """Reuse the existing child host or assemble its one-time lifecycle state."""
    component_host = ports.get_component_host()
    if component_host is not None:
        return component_host

    component_host = ports.create_component_host()
    ports.set_component_active(component_host, True)
    ports.bind_component_parent(component_host)
    ports.ensure_task_session(component_host)
    ports.store_component_host(component_host)
    return component_host
