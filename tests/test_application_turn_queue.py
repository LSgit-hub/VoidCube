from __future__ import annotations

import pytest

from VoidCube_app.turn_queue import (
    BusyInputMode,
    TurnInterruptReason,
    TurnInputRoute,
    cancel_turn,
    interrupt_for_input,
    interrupt_text,
    normalize_busy_input_mode,
    prepare_interrupted_input_batch,
    resolve_interrupted_followup,
    route_turn_input,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_busy_input_mode_is_canonical_and_defaults_to_interrupt() -> None:
    assert normalize_busy_input_mode(" QUEUE ") is BusyInputMode.QUEUE
    assert normalize_busy_input_mode("interrupt") is BusyInputMode.INTERRUPT
    assert normalize_busy_input_mode("unsupported") is BusyInputMode.INTERRUPT
    assert normalize_busy_input_mode(None) is BusyInputMode.INTERRUPT


@pytest.mark.parametrize(
    ("agent_running", "is_command", "mode", "expected"),
    [
        (False, False, "interrupt", TurnInputRoute.NEXT_TURN),
        (True, True, "interrupt", TurnInputRoute.NEXT_TURN),
        (True, False, "queue", TurnInputRoute.NEXT_TURN),
        (True, False, "interrupt", TurnInputRoute.INTERRUPT),
        (True, False, "invalid", TurnInputRoute.INTERRUPT),
    ],
)
def test_turn_input_route_is_independent_of_cli_queues(
    agent_running: bool,
    is_command: bool,
    mode: str,
    expected: TurnInputRoute,
) -> None:
    assert route_turn_input(
        agent_running=agent_running,
        is_command=is_command,
        busy_input_mode=mode,
    ) is expected


def test_all_text_interrupts_are_combined_for_one_follow_up_turn() -> None:
    batch = prepare_interrupted_input_batch("first", ["second", "third"])

    assert batch.payloads == ("first", "second", "third")
    assert batch.pending_payloads == ("first\nsecond\nthird",)


def test_multimodal_interrupts_keep_payloads_and_order() -> None:
    first = ("inspect this", ["screen.png"])
    batch = prepare_interrupted_input_batch(first, ["", "then summarize"])

    assert interrupt_text(first) == "inspect this"
    assert batch.payloads == (first, "then summarize")
    assert batch.pending_payloads == batch.payloads


def test_dunder_text_is_regular_user_input_not_an_internal_sentinel() -> None:
    assert interrupt_text("__HELLO__") == "__HELLO__"
    assert route_turn_input(
        agent_running=True,
        is_command=False,
        busy_input_mode="interrupt",
    ) is TurnInputRoute.INTERRUPT


def test_new_input_interrupt_exposes_agent_text_and_followup_payload() -> None:
    payload = ("inspect", ["screen.png"])
    interrupt = interrupt_for_input(payload)

    assert interrupt.reason is TurnInterruptReason.NEW_INPUT
    assert interrupt.agent_message == "inspect"
    assert interrupt.followup_payload == payload
    assert resolve_interrupted_followup(interrupt, "agent fallback") == payload


def test_timeout_cancel_has_no_agent_message_or_followup_payload() -> None:
    interrupt = cancel_turn(TurnInterruptReason.TIMEOUT)

    assert interrupt.agent_message is None
    assert interrupt.followup_payload is None
    assert resolve_interrupted_followup(interrupt, "timeout marker") is None


def test_unobserved_interrupt_preserves_agent_followup_payload() -> None:
    assert resolve_interrupted_followup(None, "external message") == "external message"


def test_new_input_is_not_a_valid_cancel_reason() -> None:
    with pytest.raises(ValueError, match="cancel reason"):
        cancel_turn(TurnInterruptReason.NEW_INPUT)
