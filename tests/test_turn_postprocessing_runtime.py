from __future__ import annotations

from VoidCube_app.turn_contract import TurnOutcome
from VoidCube_app.turn_queue import TurnInterrupt, TurnInterruptReason
from VoidCube_cli.turn_postprocessing_runtime import (
    TurnPostprocessingPorts,
    TurnPostprocessingRuntime,
)


def _runtime(calls, *, voice=True):
    state = {"voice": voice}
    return TurnPostprocessingRuntime(
        TurnPostprocessingPorts(
            session_db=lambda: "db",
            session_id=lambda: "session-1",
            voice_continuous=lambda: state["voice"],
            stop_voice_continuous=lambda: (state.__setitem__("voice", False), calls.append("stop-voice")),
            emit=lambda value: calls.append(("emit", value)),
        )
    )


def test_postprocessing_runtime_triggers_title_generation_for_usable_turn(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agent.title_generator.maybe_auto_title",
        lambda *args: calls.append(("title", args)),
    )

    result = _runtime(calls).process(
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
        turn_interrupt=None,
    )

    assert result.response == "answer"
    assert calls[0][0] == "title"
    assert calls[0][1][:4] == (
        "db",
        "session-1",
        "question",
        "answer",
    )


def test_postprocessing_runtime_handles_error_and_interrupted_followup():
    calls = []
    runtime = _runtime(calls)
    interrupted = TurnInterrupt(TurnInterruptReason.NEW_INPUT, "next prompt")

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
        turn_interrupt=interrupted,
    )

    assert result.response == (
        "Error: rate limited\n\n---\n_[Interrupted - processing new message]_"
    )
    assert result.turn_result["response"] == "Error: rate limited"
    assert result.pending_message == "next prompt"
    assert calls[0] == "stop-voice"
    assert calls[1][0] == "emit"
