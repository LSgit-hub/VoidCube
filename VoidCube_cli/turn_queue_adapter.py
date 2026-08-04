"""Queue adapter for the shared interrupted-input transition."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from enum import Enum
from typing import Any

from VoidCube_app.turn_queue import (
    InterruptedInputBatch,
    TurnInterrupt,
    interrupt_for_input,
    prepare_interrupted_input_batch,
)


class InterruptPollStatus(str, Enum):
    EMPTY = "empty"
    DEFERRED = "deferred"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class InterruptPollResult:
    status: InterruptPollStatus
    interrupt: TurnInterrupt | None = None


def poll_interrupt_input(
    pending_queue: queue.Queue,
    interrupt_queue: queue.Queue,
    *,
    timeout: float,
    defer: bool,
) -> InterruptPollResult:
    """Read one CLI interrupt payload, optionally deferring it to the next turn."""
    try:
        payload = interrupt_queue.get(timeout=max(0.0, float(timeout)))
    except queue.Empty:
        return InterruptPollResult(InterruptPollStatus.EMPTY)
    if not payload:
        return InterruptPollResult(InterruptPollStatus.EMPTY)
    if defer:
        pending_queue.put(payload)
        return InterruptPollResult(InterruptPollStatus.DEFERRED)
    return InterruptPollResult(
        InterruptPollStatus.READY,
        interrupt=interrupt_for_input(payload),
    )


def requeue_interrupted_inputs(
    pending_queue: queue.Queue,
    interrupt_queue: queue.Queue,
    first_payload: Any,
) -> InterruptedInputBatch:
    """Drain queued interrupts and enqueue the shared transition result."""
    following_payloads: list[Any] = []
    while True:
        try:
            following_payloads.append(interrupt_queue.get_nowait())
        except queue.Empty:
            break

    batch = prepare_interrupted_input_batch(first_payload, following_payloads)
    for payload in batch.pending_payloads:
        pending_queue.put(payload)
    return batch
