"""Queue adapter for the shared interrupted-input transition."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from enum import Enum
from typing import Any

from VoidCube_app.turn_queue import (
    InterruptedInputBatch,
    TurnInterrupt,
    TurnInputRoute,
    interrupt_for_input,
    prepare_interrupted_input_batch,
    route_turn_input,
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


def enqueue_turn_input(
    pending_queue: queue.Queue,
    interrupt_queue: queue.Queue,
    payload: Any,
    *,
    agent_running: bool,
    is_command: bool,
    busy_input_mode: Any,
) -> TurnInputRoute:
    """Project the shared route decision into the CLI-owned queues."""
    route = route_turn_input(
        agent_running=agent_running,
        is_command=is_command,
        busy_input_mode=busy_input_mode,
    )
    target_queue = pending_queue if route is TurnInputRoute.NEXT_TURN else interrupt_queue
    target_queue.put(payload)
    return route
