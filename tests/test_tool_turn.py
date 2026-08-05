from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.conversation_turn import ConversationTurnState
from agent.conversation_runtime import ConversationTurnPorts, ConversationTurnRuntime
from agent.tool_turn import (
    ContextPressureTracker,
    execute_successful_tool_turn,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _tool_call(name: str):
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments="{}"),
    )


def _owner(events, *, tool_name="terminal", compress=False):
    compressor = SimpleNamespace(
        last_prompt_tokens=90 if compress else 10,
        last_completion_tokens=5 if compress else 2,
        threshold_tokens=100,
        should_compress=lambda _tokens: compress,
    )

    def execute_tools(_assistant, messages, _task_id):
        events.append("execute")
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call-{tool_name}",
                "content": "done",
            }
        )

    def compress_context(messages, _system, **_kwargs):
        events.append("compress")
        return messages[-2:], "compressed-policy"

    turn_runtime = ConversationTurnRuntime(
        ConversationTurnPorts(
            persist_session=lambda _messages, _history: events.append("persist"),
            save_session_log=lambda _messages: events.append("save"),
            cleanup_task_resources=lambda _task_id: events.append("cleanup"),
            clear_interrupt=lambda: events.append("clear_interrupt"),
            emit_status=lambda message: events.append(("status", message)),
            emit_verbose=lambda message, force: events.append(
                ("print", message, force)
            ),
        )
    )
    return SimpleNamespace(
        quiet_mode=True,
        stream_delta_callback=lambda value: events.append(("stream", value)),
        iteration_budget=SimpleNamespace(
            refund=lambda: events.append("refund")
        ),
        context_compressor=compressor,
        compression_enabled=compress,
        session_id="session-1",
        _last_content_with_tools=None,
        _mute_post_response=False,
        _thinking_prefill_retries=1,
        _empty_content_retries=2,
        _stream_needs_break=False,
        _conversation_turn_runtime=turn_runtime,
        _cap_delegate_task_calls=lambda calls: events.append("cap") or calls,
        _deduplicate_tool_calls=lambda calls: events.append("dedupe") or calls,
        _build_assistant_message=lambda message, finish: (
            events.append("build")
            or {
                "role": "assistant",
                "content": message.content,
                "finish_reason": finish,
                "tool_calls": [
                    {
                        "id": call.id,
                        "function": {"name": call.function.name},
                    }
                    for call in message.tool_calls
                ],
            }
        ),
        _has_stream_consumers=lambda: False,
        _vprint=lambda message, **_kwargs: events.append(("print", message)),
        _emit_interim_assistant_message=lambda _message: events.append("interim"),
        _execute_tool_calls=execute_tools,
        _emit_context_pressure=lambda *_args: events.append("pressure"),
        _safe_print=lambda _message: events.append("compress_notice"),
        _compress_context=compress_context,
    )


def test_context_pressure_tracker_deduplicates_and_resets_by_session():
    now = [100.0]
    tracker = ContextPressureTracker(cooldown=10.0, clock=lambda: now[0])

    assert tracker.next_warning("s1", 0.86) == 0.85
    assert tracker.next_warning("s1", 0.90) == 0.0
    assert tracker.next_warning("s1", 0.96) == 0.95

    now[0] += 11.0
    assert tracker.next_warning("s1", 0.96) == 0.95

    tracker.reset("s1")
    assert tracker.next_warning("s1", 0.86) == 0.85


def test_successful_tool_turn_runs_one_ordered_sequence():
    events = []
    owner = _owner(events, tool_name="execute_code")
    state = ConversationTurnState(truncated_tool_call_retries=1)
    assistant = SimpleNamespace(
        content="working",
        tool_calls=[_tool_call("execute_code")],
    )
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "_thinking_prefill": True},
    ]

    result = execute_successful_tool_turn(
        owner,
        state=state,
        assistant_message=assistant,
        finish_reason="tool_calls",
        messages=messages,
        system_message="policy",
        active_system_prompt="active-policy",
        task_id="task-1",
        pressure_tracker=ContextPressureTracker(),
    )

    assert [event for event in events if isinstance(event, str)] == [
        "cap",
        "dedupe",
        "build",
        "interim",
        "execute",
        "refund",
        "save",
    ]
    assert ("stream", None) in events
    assert owner._thinking_prefill_retries == 0
    assert owner._empty_content_retries == 0
    assert owner._stream_needs_break is True
    assert state.truncated_tool_call_retries == 0
    assert result.conversation_history_reset is False
    assert [message["role"] for message in result.messages] == [
        "user",
        "assistant",
        "tool",
    ]


def test_successful_tool_turn_emits_pressure_and_returns_compressed_state():
    events = []
    owner = _owner(events, compress=True)
    state = ConversationTurnState()
    assistant = SimpleNamespace(
        content="",
        tool_calls=[_tool_call("terminal")],
    )

    result = execute_successful_tool_turn(
        owner,
        state=state,
        assistant_message=assistant,
        finish_reason="tool_calls",
        messages=[{"role": "user", "content": "question"}],
        system_message="policy",
        active_system_prompt="active-policy",
        task_id="task-1",
        pressure_tracker=ContextPressureTracker(),
    )

    assert "pressure" in events
    assert events.index("pressure") < events.index("compress")
    assert events.index("compress") < events.index("save")
    assert result.system_prompt == "compressed-policy"
    assert result.conversation_history_reset is True
