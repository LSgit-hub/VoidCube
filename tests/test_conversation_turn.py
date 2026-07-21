from __future__ import annotations

import pytest

from agent.conversation_turn import ConversationTurnState
from agent.iteration_control import IterationBudget


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_turn_state_owns_iteration_and_completion_lifecycle():
    budget = IterationBudget(2)
    state = ConversationTurnState()

    assert state.can_continue(max_iterations=2, iteration_budget=budget)
    assert state.begin_iteration(budget)
    assert state.api_call_count == 1
    assert budget.remaining == 1

    state.final_response = "done"

    assert not state.can_continue(max_iterations=2, iteration_budget=budget)
    assert state.completed(max_iterations=2)


def test_turn_state_reports_budget_exhaustion_without_cross_turn_flags():
    budget = IterationBudget(1)
    state = ConversationTurnState()

    assert state.begin_iteration(budget)

    assert state.exhausted(max_iterations=3, iteration_budget=budget)
    assert not state.can_continue(max_iterations=3, iteration_budget=budget)


def test_empty_response_retry_must_return_state_to_non_terminal():
    budget = IterationBudget(2)
    state = ConversationTurnState(final_response="")

    assert not state.can_continue(max_iterations=2, iteration_budget=budget)

    state.final_response = None

    assert state.can_continue(max_iterations=2, iteration_budget=budget)


def test_turn_state_clears_text_continuation_as_one_operation():
    state = ConversationTurnState(
        length_continue_retries=2,
        truncated_response_prefix="partial ",
    )

    state.clear_text_continuation()

    assert state.length_continue_retries == 0
    assert state.truncated_response_prefix == ""
