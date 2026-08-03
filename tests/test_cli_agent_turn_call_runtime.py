from VoidCube_cli.cli_agent_turn_call_runtime import (
    CliAgentTurnCallPorts,
    CliAgentTurnCallRuntime,
)


def _ports(events, **overrides):
    values = {
        "message": "hello",
        "voice_prefix": "",
        "pending_model_switch_note": lambda: None,
        "clear_pending_model_switch_note": lambda: events.append("clear-note"),
        "prior_history": [{"role": "user", "content": "before"}],
        "session_id": "session",
        "stream_callback": None,
        "persist_user_message": None,
        "new_trace_id": lambda: "trace-id",
        "set_trace_id": lambda value: events.append(("trace", value)),
        "run_conversation": lambda **kwargs: events.append(("run", kwargs)) or {"ok": True},
        "summarize_error": lambda error: f"summary:{error}",
        "log_error": lambda error: events.append(("error", str(error))),
    }
    values.update(overrides)
    return CliAgentTurnCallPorts(**values)


def test_agent_turn_call_runtime_projects_note_voice_and_trace_inputs():
    events = []
    result = CliAgentTurnCallRuntime(
        _ports(
            events,
            message="question",
            voice_prefix="[voice] ",
            pending_model_switch_note=lambda: "[model changed]",
            persist_user_message="question",
        )
    ).run()

    assert result == {"ok": True}
    assert events[0] == "clear-note"
    assert events[1] == ("trace", "trace-id")
    kwargs = events[2][1]
    assert kwargs["user_message"] == "[model changed]\n\n[voice] question"
    assert kwargs["conversation_history"] == [{"role": "user", "content": "before"}]
    assert kwargs["task_id"] == "session"
    assert kwargs["trace_id"] == "trace-id"
    assert kwargs["persist_user_message"] == "question"


def test_agent_turn_call_runtime_returns_normalized_error_result():
    events = []

    def fail(**_kwargs):
        raise ValueError("bad request")

    result = CliAgentTurnCallRuntime(
        _ports(events, run_conversation=fail)
    ).run()

    assert result["failed"] is True
    assert result["final_response"] == "Error: summary:bad request"
    assert events == [("trace", "trace-id"), ("error", "bad request")]
