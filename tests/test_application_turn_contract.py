from __future__ import annotations

import pytest

from voidcube.domain.contracts.turn import begin_turn, normalize_turn_outcome


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_begin_turn_preserves_prior_history_and_appends_user_input() -> None:
    history = [{"role": "assistant", "content": "previous"}]
    multimodal = [{"type": "text", "text": "hello"}]

    turn = begin_turn(history, multimodal)

    assert history == [{"role": "assistant", "content": "previous"}]
    assert turn.prior_history == tuple(history)
    assert turn.user_message is multimodal
    assert turn.conversation_history == (
        {"role": "assistant", "content": "previous"},
        {"role": "user", "content": multimodal},
    )


def test_missing_turn_result_keeps_fallback_history_and_reports_failure() -> None:
    fallback = [{"role": "user", "content": "hello"}]

    outcome = normalize_turn_outcome(None, fallback_history=fallback)

    assert outcome.conversation_history == tuple(fallback)
    assert outcome.failed is True
    assert outcome.partial is False
    assert outcome.error == "No result returned"
    assert outcome.response_or_error() == "Error: No result returned"
    assert outcome.usable is False


def test_turn_outcome_uses_agent_history_and_structured_status() -> None:
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "answer"},
    ]
    interrupt_payload = ("next", ["screen.png"])

    outcome = normalize_turn_outcome(
        {
            "messages": messages,
            "final_response": "answer",
            "failed": False,
            "partial": False,
            "interrupted": True,
            "interrupt_message": interrupt_payload,
            "response_previewed": True,
            "last_reasoning": "checked",
        },
        fallback_history=[],
    )

    assert outcome.conversation_history == tuple(messages)
    assert outcome.response == "answer"
    assert outcome.interrupted is True
    assert outcome.interrupt_message == interrupt_payload
    assert outcome.response_previewed is True
    assert outcome.last_reasoning == "checked"
    assert outcome.usable is True
    assert outcome.observation() == {
        "failed": False,
        "partial": False,
        "interrupted": True,
        "error": "",
        "response": "answer",
    }


def test_partial_empty_outcome_builds_error_without_replacing_bad_history_shape() -> None:
    fallback = [{"role": "user", "content": "hello"}]

    outcome = normalize_turn_outcome(
        {
            "messages": "not-a-history",
            "final_response": "",
            "partial": True,
            "error": "rate limited",
        },
        fallback_history=fallback,
    )

    assert outcome.conversation_history == tuple(fallback)
    assert outcome.response_or_error() == "Error: rate limited"
    assert outcome.observation(response="Error: rate limited")["response"] == (
        "Error: rate limited"
    )
