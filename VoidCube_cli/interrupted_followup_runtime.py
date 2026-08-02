"""Requeue and announce an interrupted follow-up input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from VoidCube_app.turn_queue import InterruptedInputBatch, interrupt_text


@dataclass(frozen=True, slots=True)
class InterruptedFollowupPorts:
    """Queue transition and terminal output supplied by the CLI host."""

    has_queue: Callable[[], bool]
    requeue: Callable[[Any], InterruptedInputBatch]
    emit: Callable[[str], None]


class InterruptedFollowupRuntime:
    """Own the follow-up queue transition after an interrupted turn."""

    def __init__(self, ports: InterruptedFollowupPorts) -> None:
        self.ports = ports

    def requeue(self, pending_message: Any) -> bool:
        if not pending_message or not self.ports.has_queue():
            return False
        batch = self.ports.requeue(pending_message)
        preview_text = interrupt_text(batch.payloads[0])
        preview = preview_text[:50] + ("..." if len(preview_text) > 50 else "")
        if len(batch.payloads) > 1:
            self.ports.emit(
                f"\n🔧 Sending {len(batch.payloads)} messages after interrupt: '{preview}'"
            )
        else:
            self.ports.emit(f"\n🔧 Sending after interrupt: '{preview}'")
        return True
