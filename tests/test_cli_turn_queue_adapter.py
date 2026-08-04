from __future__ import annotations

import queue

import pytest

from VoidCube_cli.turn_queue_adapter import (
    InterruptPollStatus,
    poll_interrupt_input,
    requeue_interrupted_inputs,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_adapter_drains_interrupt_queue_without_using_empty_as_a_guard() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    interrupts.put("second")
    interrupts.put("third")

    batch = requeue_interrupted_inputs(pending, interrupts, "first")

    assert batch.payloads == ("first", "second", "third")
    assert pending.get_nowait() == "first\nsecond\nthird"
    with pytest.raises(queue.Empty):
        interrupts.get_nowait()


def test_interrupt_poll_returns_structured_empty_result() -> None:
    result = poll_interrupt_input(
        queue.Queue(),
        queue.Queue(),
        timeout=0,
        defer=False,
    )

    assert result.status is InterruptPollStatus.EMPTY
    assert result.interrupt is None


def test_interrupt_poll_defers_payload_during_clarification() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    payload = ("inspect", ["screen.png"])
    interrupts.put(payload)

    result = poll_interrupt_input(
        pending,
        interrupts,
        timeout=0,
        defer=True,
    )

    assert result.status is InterruptPollStatus.DEFERRED
    assert result.interrupt is None
    assert pending.get_nowait() == payload


def test_interrupt_poll_preserves_payload_for_new_input_interrupt() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    payload = ("inspect", ["screen.png"])
    interrupts.put(payload)

    result = poll_interrupt_input(
        pending,
        interrupts,
        timeout=0,
        defer=False,
    )

    assert result.status is InterruptPollStatus.READY
    assert result.interrupt is not None
    assert result.interrupt.agent_message == "inspect"
    assert result.interrupt.followup_payload == payload
