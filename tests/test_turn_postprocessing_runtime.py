from __future__ import annotations

from voidcube.domain.contracts.turn import TurnOutcome
from voidcube.interfaces.cli.turn.postprocessing import (
    TurnPostprocessingPorts,
    TurnPostprocessingRuntime,
)


def _runtime(calls, *, voice=True, title_generator=None):
    state = {"voice": voice}
    return TurnPostprocessingRuntime(
        TurnPostprocessingPorts(
            session_db=lambda: "db",
            session_id=lambda: "session-1",
            voice_continuous=lambda: state["voice"],
            stop_voice_continuous=lambda: (state.__setitem__("voice", False), calls.append("stop-voice")),
            emit=lambda value: calls.append(("emit", value)),
            title_generator=title_generator or (lambda *args, **kwargs: None),
        )
    )


def test_postprocessing_runtime_triggers_title_generation_for_usable_turn():
    calls = []

    result = _runtime(
        calls,
        title_generator=lambda *args, **kwargs: calls.append(("title", args)),
    ).process(
        outcome=TurnOutcome(
            conversation_history=(),
            response="answer",
            failed=False,
            partial=False,
            interrupted=False,
            error="",
        ),
        message="question",
        conversation_history=[{"role": "user", "content": "question"}],
        turn_result={"response": "answer"},
    )

    assert result.response == "answer"
    assert calls[0][0] == "title"
    assert calls[0][1][:4] == (
        "db",
        "session-1",
        "question",
        "answer",
    )


def test_postprocessing_runtime_handles_error_without_requeueing_input():
    calls = []
    runtime = _runtime(calls)
    result = runtime.process(
        outcome=TurnOutcome(
            conversation_history=(),
            response="",
            failed=True,
            partial=False,
            interrupted=True,
            error="rate limited",
            interrupt_message="fallback prompt",
        ),
        message="question",
        conversation_history=[],
        turn_result={"failed": True},
    )

    assert result.response == "Error: rate limited"
    assert result.turn_result["response"] == "Error: rate limited"
    assert calls[0] == "stop-voice"
    assert calls[1][0] == "emit"
