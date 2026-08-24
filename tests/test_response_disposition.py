from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.domain.agent.conversation_runtime import ConversationTurnPorts, ConversationTurnRuntime
from voidcube.domain.agent.effect_outcomes import EffectOutcome
from voidcube.domain.agent.response_disposition import (
    ResponseLoopControl,
    TextResponseAction,
    TextResponseDisposition,
    apply_text_response_disposition,
    apply_tool_call_inspection,
    decide_text_response_disposition,
    inspect_tool_calls,
    normalize_assistant_content,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _tool_call(name: str, arguments):
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"text": "hello"}, "hello"),
        ({"content": "world"}, "world"),
        (
            ["first", {"type": "text", "text": "second"}, {"text": 3}],
            "first\nsecond\n3",
        ),
        (42, "42"),
    ],
)
def test_normalize_assistant_content_handles_compatible_variants(
    content,
    expected,
):
    assert normalize_assistant_content(content) == expected


def test_tool_inspection_repairs_names_and_normalizes_arguments():
    calls = [
        _tool_call("readfile", {"path": "a.txt"}),
        _tool_call("terminal", ""),
    ]

    inspection = inspect_tool_calls(
        calls,
        valid_tool_names={"read_file", "terminal"},
        repair_tool_name=lambda name: "read_file" if name == "readfile" else None,
    )

    assert [(repair.original, repair.repaired) for repair in inspection.repairs] == [
        ("readfile", "read_file")
    ]
    assert calls[0].function.arguments == '{"path": "a.txt"}'
    assert calls[1].function.arguments == "{}"
    assert inspection.invalid_tool_names == ()
    assert inspection.invalid_json_arguments == ()


def test_tool_inspection_stops_at_unknown_tool_names():
    call = _tool_call("missing", "not-json")

    inspection = inspect_tool_calls(
        [call],
        valid_tool_names={"terminal"},
        repair_tool_name=lambda _name: None,
    )

    assert inspection.invalid_tool_names == ("missing",)
    assert inspection.invalid_json_arguments == ()
    assert call.function.arguments == "not-json"


def test_tool_inspection_distinguishes_truncated_invalid_json():
    call = _tool_call("terminal", '{"command": "echo')

    inspection = inspect_tool_calls(
        [call],
        valid_tool_names={"terminal"},
        repair_tool_name=lambda _name: None,
    )

    assert inspection.invalid_json_arguments[0][0] == "terminal"
    assert inspection.truncated_json_arguments is True


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"content": "answer"}, TextResponseAction.final_text),
        (
            {"content": "", "prior_content_available": True},
            TextResponseAction.use_prior_content,
        ),
        (
            {"content": "", "structured_reasoning": True},
            TextResponseAction.prefill_reasoning,
        ),
        ({"content": ""}, TextResponseAction.retry_empty),
        (
            {"content": "", "empty_content_retries": 3, "fallback_available": True},
            TextResponseAction.try_fallback,
        ),
        (
            {"content": "", "empty_content_retries": 3},
            TextResponseAction.terminal_empty,
        ),
    ],
)
def test_text_response_disposition_selects_one_loop_action(options, expected):
    values = {
        "content": "",
        "structured_reasoning": False,
        "thinking_prefill_retries": 0,
        "empty_content_retries": 0,
        "prior_content_available": False,
        "fallback_available": False,
    }
    values.update(options)

    disposition = decide_text_response_disposition(**values)

    assert disposition.action is expected


def _action_owner(events, *, fallback=False):
    def build_message(message, finish_reason):
        return {
            "role": "assistant",
            "content": message.content,
            "finish_reason": finish_reason,
            "tool_calls": [
                {
                    "id": call.id,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in (message.tool_calls or [])
            ],
        }

    def persist(messages, history):
        events.append(("persist", messages, history))
        return EffectOutcome(status="succeeded")

    def cleanup(task_id):
        events.append(("cleanup", task_id))
        return EffectOutcome(status="succeeded")

    turn_runtime = ConversationTurnRuntime(
        ConversationTurnPorts(
            persist_session=persist,
            save_session_log=lambda messages: events.append(
                ("save_log", messages)
            ),
            cleanup_task_resources=cleanup,
            clear_interrupt=lambda: events.append(("clear_interrupt",)),
            emit_status=lambda message: events.append(("status", message)),
            emit_verbose=lambda message, force: events.append(
                ("print", message, {"force": force})
            ),
        )
    )
    return SimpleNamespace(
        model="safe-model",
        provider="test-provider",
        log_prefix="[test] ",
        valid_tool_names={"terminal"},
        quiet_mode=True,
        _invalid_tool_retries=0,
        _invalid_json_retries=0,
        _empty_content_retries=0,
        _thinking_prefill_retries=0,
        _last_content_with_tools=None,
        _fallback_chain=[{"model": "fallback"}] if fallback else [],
        _response_was_previewed=False,
        _conversation_turn_runtime=turn_runtime,
        _vprint=lambda message, **kwargs: events.append(
            ("print", message, kwargs)
        ),
        _emit_status=lambda message: events.append(("status", message)),
        _build_assistant_message=build_message,
        _try_activate_fallback=lambda: fallback,
    )


def test_tool_action_applies_unknown_name_recovery_messages():
    events = []
    owner = _action_owner(events)
    call = _tool_call("missing", "{}")
    assistant = SimpleNamespace(content="", tool_calls=[call])
    inspection = inspect_tool_calls(
        [call],
        valid_tool_names=owner.valid_tool_names,
        repair_tool_name=lambda _name: None,
    )
    messages = []

    execution = apply_tool_call_inspection(
        owner,
        inspection,
        assistant_message=assistant,
        finish_reason="tool_calls",
        messages=messages,
        conversation_history=None,
        api_call_count=1,
        task_id="task-1",
    )

    assert execution.control is ResponseLoopControl.continue_loop
    assert owner._invalid_tool_retries == 1
    assert [message["role"] for message in messages] == ["assistant", "tool"]
    assert "does not exist" in messages[-1]["content"]


def test_tool_action_returns_terminal_result_for_truncated_json():
    events = []
    owner = _action_owner(events)
    call = _tool_call("terminal", '{"command": "echo')
    assistant = SimpleNamespace(content="", tool_calls=[call])
    inspection = inspect_tool_calls(
        [call],
        valid_tool_names=owner.valid_tool_names,
        repair_tool_name=lambda _name: None,
    )

    execution = apply_tool_call_inspection(
        owner,
        inspection,
        assistant_message=assistant,
        finish_reason="tool_calls",
        messages=[],
        conversation_history=None,
        api_call_count=2,
        task_id="task-1",
    )

    assert execution.control is ResponseLoopControl.terminal
    assert execution.terminal_result["partial"] is True
    assert [event[0] for event in events if event[0] != "print"] == [
        "cleanup",
        "persist",
    ]


def test_text_action_applies_prefill_and_returns_continue():
    events = []
    owner = _action_owner(events)
    state = SimpleNamespace(final_response="", exit_reason="unknown")
    assistant = SimpleNamespace(content="", tool_calls=[])
    messages = []

    execution = apply_text_response_disposition(
        owner,
        TextResponseDisposition(TextResponseAction.prefill_reasoning),
        assistant_message=assistant,
        finish_reason="stop",
        state=state,
        messages=messages,
    )

    assert execution.control is ResponseLoopControl.continue_loop
    assert state.final_response is None
    assert owner._thinking_prefill_retries == 1
    assert messages[-1]["_thinking_prefill"] is True


def test_failed_empty_fallback_becomes_terminal_empty():
    events = []
    owner = _action_owner(events, fallback=False)
    owner._fallback_chain = [{"model": "unavailable"}]
    state = SimpleNamespace(final_response="", exit_reason="unknown")
    assistant = SimpleNamespace(content="", tool_calls=[], reasoning=None)
    messages = []

    execution = apply_text_response_disposition(
        owner,
        TextResponseDisposition(TextResponseAction.try_fallback),
        assistant_message=assistant,
        finish_reason="stop",
        state=state,
        messages=messages,
    )

    assert execution.control is ResponseLoopControl.break_loop
    assert state.final_response == "(empty)"
    assert state.exit_reason == "empty_response_exhausted"
