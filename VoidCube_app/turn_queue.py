"""UI-independent routing and requeue transitions for turn input."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class BusyInputMode(str, Enum):
    INTERRUPT = "interrupt"
    QUEUE = "queue"


class TurnInputRoute(str, Enum):
    NEXT_TURN = "next_turn"
    INTERRUPT = "interrupt"


class TurnInterruptReason(str, Enum):
    NEW_INPUT = "new_input"
    USER_CANCELLED = "user_cancelled"
    TIMEOUT = "timeout"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class TurnInterrupt:
    reason: TurnInterruptReason
    payload: Any = None

    @property
    def agent_message(self) -> str | None:
        if self.reason is TurnInterruptReason.NEW_INPUT:
            return interrupt_text(self.payload)
        return None

    @property
    def followup_payload(self) -> Any:
        if self.reason is TurnInterruptReason.NEW_INPUT:
            return self.payload
        return None


@dataclass(frozen=True, slots=True)
class InterruptedInputBatch:
    payloads: tuple[Any, ...]
    pending_payloads: tuple[Any, ...]


def normalize_busy_input_mode(value: Any) -> BusyInputMode:
    if str(value or "").strip().lower() == BusyInputMode.QUEUE.value:
        return BusyInputMode.QUEUE
    return BusyInputMode.INTERRUPT


def route_turn_input(
    *,
    agent_running: bool,
    is_command: bool,
    busy_input_mode: Any,
) -> TurnInputRoute:
    """Choose the queue owned by the CLI adapter for one input payload."""
    if not agent_running or is_command:
        return TurnInputRoute.NEXT_TURN
    if normalize_busy_input_mode(busy_input_mode) is BusyInputMode.QUEUE:
        return TurnInputRoute.NEXT_TURN
    return TurnInputRoute.INTERRUPT


def interrupt_text(payload: Any) -> str:
    """Return interrupt text while leaving a multimodal payload intact."""
    if isinstance(payload, tuple) and payload:
        return str(payload[0] or "")
    return str(payload or "")


def interrupt_for_input(payload: Any) -> TurnInterrupt:
    return TurnInterrupt(reason=TurnInterruptReason.NEW_INPUT, payload=payload)


def cancel_turn(reason: TurnInterruptReason) -> TurnInterrupt:
    if reason is TurnInterruptReason.NEW_INPUT:
        raise ValueError("cancel reason cannot be new_input")
    return TurnInterrupt(reason=reason)


def resolve_interrupted_followup(
    observed_interrupt: TurnInterrupt | None,
    outcome_payload: Any,
) -> Any:
    """Select a follow-up payload without requeueing cancellation metadata."""
    if observed_interrupt is not None:
        return observed_interrupt.followup_payload
    return outcome_payload


def prepare_interrupted_input_batch(
    first_payload: Any,
    following_payloads: Iterable[Any] = (),
) -> InterruptedInputBatch:
    """Preserve input order and combine only an all-text interrupted batch."""
    payloads = (first_payload, *(payload for payload in following_payloads if payload))
    if all(isinstance(payload, str) for payload in payloads):
        pending_payloads = ("\n".join(payloads),)
    else:
        pending_payloads = payloads
    return InterruptedInputBatch(
        payloads=payloads,
        pending_payloads=pending_payloads,
    )
